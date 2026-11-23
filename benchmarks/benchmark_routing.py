"""
Benchmark 1 — Routing Accuracy Test.

Tests 18 questions (6 explicit × 6 ambiguous × 6 global) against 2 simulated
cameras, comparing keyword-baseline vs LLM (Gemini) router.

Usage:
    python -m benchmarks.benchmark_routing           # runs both routers
    python -m benchmarks.benchmark_routing --keyword  # keyword-only (zero API)
    python -m benchmarks.benchmark_routing --llm      # LLM-only (18 API calls)
"""

import sys
import io
import time
import argparse
from collections import defaultdict

# Fix Windows cp1252 encoding crash for emoji/unicode chars
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Ensure project root is importable
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.conftest import (
    BENCH_CAMERAS,
    ROUTING_TEST_SET,
    save_results,
    print_table,
    rate_limit_sleep,
)
from backend.router import route_query_local
from backend.gemini_service import GeminiService


def run_keyword_baseline(test_set: list[dict], cameras: list[dict]) -> list[dict]:
    """
    Run the keyword-baseline router on all questions.
    Uses classify_question (local logic) + route_query_local (keyword matching).
    Zero API calls.
    """
    # We use GeminiService only for classify_question (which is purely local logic)
    gemini = GeminiService()
    results = []

    for q in test_set:
        question = q["question"]
        expected_cam = q["expected_camera"]
        expected_mode = q["expected_mode"]

        # Step 1: classify (local keyword logic — no API call)
        mode = gemini.classify_question(question, len(cameras), cameras)

        # Step 2: route if SINGLE
        if mode == "SINGLE":
            selected_id = route_query_local(question, cameras)
            if selected_id is None and cameras:
                selected_id = cameras[0]["id"]
        else:
            selected_id = "ALL"

        correct = (
            (expected_mode == "ALL" and mode == "ALL") or
            (expected_mode == "SINGLE" and mode == "SINGLE" and selected_id == expected_cam)
        )

        results.append({
            "question": question,
            "bucket": q["bucket"],
            "expected_camera": expected_cam,
            "expected_mode": expected_mode,
            "predicted_camera": selected_id,
            "predicted_mode": mode,
            "correct": correct,
        })

    return results


def run_llm_router(test_set: list[dict], cameras: list[dict], rpm: int = 15) -> list[dict]:
    """
    Run the LLM (Gemini) router on all questions.
    Uses classify_question (local) + GeminiService.route_question (API).
    Burns 1 API call per SINGLE-mode question.
    """
    gemini = GeminiService()
    results = []
    api_calls = 0

    for i, q in enumerate(test_set):
        question = q["question"]
        expected_cam = q["expected_camera"]
        expected_mode = q["expected_mode"]

        # Step 1: classify (local keyword logic — no API call)
        mode = gemini.classify_question(question, len(cameras), cameras)

        # Step 2: route via Gemini if SINGLE
        if mode == "SINGLE":
            if api_calls > 0:
                rate_limit_sleep(rpm)

            selected_id = gemini.route_question(question, cameras)
            api_calls += 1

            if selected_id is None:
                selected_id = cameras[0]["id"]
        else:
            selected_id = "ALL"

        correct = (
            (expected_mode == "ALL" and mode == "ALL") or
            (expected_mode == "SINGLE" and mode == "SINGLE" and selected_id == expected_cam)
        )

        results.append({
            "question": question,
            "bucket": q["bucket"],
            "expected_camera": expected_cam,
            "expected_mode": expected_mode,
            "predicted_camera": selected_id,
            "predicted_mode": mode,
            "correct": correct,
        })

        progress = f"[{i+1}/{len(test_set)}]"
        status = "✅" if correct else "❌"
        print(f"  {progress} {status} {question[:50]:50s} → {selected_id}")

    print(f"\n  Total Gemini API calls: {api_calls}")
    return results


def compute_accuracy(results: list[dict]) -> dict:
    """Compute per-bucket and overall accuracy."""
    buckets = defaultdict(lambda: {"correct": 0, "total": 0})
    overall = {"correct": 0, "total": 0}

    for r in results:
        bucket = r["bucket"]
        buckets[bucket]["total"] += 1
        overall["total"] += 1
        if r["correct"]:
            buckets[bucket]["correct"] += 1
            overall["correct"] += 1

    summary = {}
    for bucket, counts in buckets.items():
        pct = (counts["correct"] / counts["total"] * 100) if counts["total"] > 0 else 0
        summary[bucket] = {
            "correct": counts["correct"],
            "total": counts["total"],
            "accuracy_pct": round(pct, 1),
        }

    overall_pct = (overall["correct"] / overall["total"] * 100) if overall["total"] > 0 else 0
    summary["overall"] = {
        "correct": overall["correct"],
        "total": overall["total"],
        "accuracy_pct": round(overall_pct, 1),
    }

    return summary


def display_results(keyword_results: list[dict] | None, llm_results: list[dict] | None):
    """Pretty-print comparison table."""

    # Per-question detail table
    if keyword_results and llm_results:
        headers = ["#", "Bucket", "Question", "Expected", "Keyword", "LLM"]
        rows = []
        for i, (kw, llm) in enumerate(zip(keyword_results, llm_results)):
            kw_status = "✅" if kw["correct"] else f"❌ {kw['predicted_camera']}"
            llm_status = "✅" if llm["correct"] else f"❌ {llm['predicted_camera']}"
            rows.append([
                str(i + 1),
                kw["bucket"],
                kw["question"][:45],
                kw["expected_camera"],
                kw_status,
                llm_status,
            ])
        print_table(headers, rows, "ROUTING ACCURACY — Per-Question Results")

    # Summary table
    summaries = {}
    if keyword_results:
        summaries["Keyword"] = compute_accuracy(keyword_results)
    if llm_results:
        summaries["LLM"] = compute_accuracy(llm_results)

    headers = ["Bucket"] + [f"{name} Accuracy" for name in summaries]
    bucket_names = ["explicit", "ambiguous", "global", "overall"]
    rows = []
    for bucket in bucket_names:
        row = [bucket.upper() if bucket == "overall" else bucket.capitalize()]
        for name, summary in summaries.items():
            s = summary.get(bucket, {"correct": 0, "total": 0, "accuracy_pct": 0})
            row.append(f"{s['correct']}/{s['total']} ({s['accuracy_pct']}%)")
        rows.append(row)

    print_table(headers, rows, "ROUTING ACCURACY — Summary")

    return summaries


def main():
    parser = argparse.ArgumentParser(description="Benchmark 1: Routing Accuracy")
    parser.add_argument("--keyword", action="store_true", help="Run keyword-baseline only")
    parser.add_argument("--llm", action="store_true", help="Run LLM router only")
    parser.add_argument("--rpm", type=int, default=15, help="API rate limit (requests/minute)")
    args = parser.parse_args()

    # Default: run both
    run_kw = not args.llm or args.keyword
    run_llm = not args.keyword or args.llm
    if not args.keyword and not args.llm:
        run_kw = True
        run_llm = True

    cameras = BENCH_CAMERAS
    test_set = ROUTING_TEST_SET

    print(f"\n🎯 Routing Accuracy Benchmark")
    print(f"   Cameras: {len(cameras)} | Questions: {len(test_set)} | RPM: {args.rpm}")
    print(f"   Routers: {'Keyword' if run_kw else ''} {'+ LLM' if run_llm else ''}\n")

    keyword_results = None
    llm_results = None

    if run_kw:
        print("── Keyword Baseline (zero API calls) ──────────────────────")
        keyword_results = run_keyword_baseline(test_set, cameras)
        for i, r in enumerate(keyword_results):
            status = "✅" if r["correct"] else "❌"
            print(f"  [{i+1}/{len(test_set)}] {status} {r['question'][:50]:50s} → {r['predicted_camera']}")

    if run_llm:
        print("\n── LLM Router (Gemini API calls) ──────────────────────────")
        llm_results = run_llm_router(test_set, cameras, rpm=args.rpm)

    summaries = display_results(keyword_results, llm_results)

    # Save results
    save_data = {
        "benchmark": "routing_accuracy",
        "cameras": [{"id": c["id"], "name": c["name"], "keywords": c["keywords"]} for c in cameras],
        "total_questions": len(test_set),
    }
    if keyword_results:
        save_data["keyword_baseline"] = {
            "results": keyword_results,
            "summary": summaries.get("Keyword", {}),
        }
    if llm_results:
        save_data["llm_router"] = {
            "results": llm_results,
            "summary": summaries.get("LLM", {}),
        }

    save_results("routing_accuracy.json", save_data)


if __name__ == "__main__":
    main()
