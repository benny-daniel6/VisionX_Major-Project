"""
Benchmark 2 — Latency Test.

Measures end-to-end response time for /api/ask with 20 queries
(15 single-camera + 5 ALL-mode), spaced to respect RPM limits.

Prerequisites:
    - VisionX server running: python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
    - At least 1-2 cameras active with frames

Usage:
    python -m benchmarks.benchmark_latency
    python -m benchmarks.benchmark_latency --rpm 10 --url http://localhost:8000
"""

import sys
import io
import time
import argparse
import statistics
from urllib import request as urllib_request
from urllib.error import URLError
import json

# Fix Windows cp1252 encoding crash for emoji/unicode chars
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.conftest import (
    LATENCY_SINGLE_QUERIES,
    LATENCY_ALL_QUERIES,
    save_results,
    print_table,
    rate_limit_sleep,
)


def check_server(base_url: str) -> bool:
    """Verify the VisionX server is running and has cameras."""
    try:
        url = f"{base_url}/api/cameras"
        req = urllib_request.Request(url)
        with urllib_request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            cameras = data.get("cameras", [])
            active = [c for c in cameras if c.get("active") and c.get("has_frame")]
            if not active:
                print(f"  ⚠️  Server is up but no active cameras with frames.")
                print(f"      Found {len(cameras)} camera(s), {len(active)} active with frames.")
                return False
            print(f"  ✅ Server OK — {len(active)} active camera(s) with frames")
            return True
    except URLError as e:
        print(f"  ❌ Cannot connect to {base_url}: {e}")
        return False


def send_query(base_url: str, question: str, camera_id: str | None = None) -> dict:
    """Send a single /api/ask query and measure round-trip time."""
    url = f"{base_url}/api/ask"
    payload = {"question": question}
    if camera_id:
        payload["camera_id"] = camera_id

    body = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    start = time.perf_counter()
    try:
        with urllib_request.urlopen(req, timeout=60) as resp:
            elapsed = time.perf_counter() - start
            response_data = json.loads(resp.read().decode())
            return {
                "success": True,
                "latency_ms": round(elapsed * 1000, 1),
                "mode": response_data.get("mode", "unknown"),
                "camera_name": response_data.get("camera_name", "N/A"),
                "answer_preview": response_data.get("answer", "")[:80],
                "status_code": resp.status,
            }
    except URLError as e:
        elapsed = time.perf_counter() - start
        error_msg = str(e)
        # Check if it's a rate limit
        is_429 = "429" in error_msg or "Resource" in error_msg
        return {
            "success": False,
            "latency_ms": round(elapsed * 1000, 1),
            "mode": "error",
            "camera_name": "N/A",
            "answer_preview": f"ERROR: {error_msg[:60]}",
            "status_code": 429 if is_429 else 0,
            "is_rate_limited": is_429,
        }


def main():
    parser = argparse.ArgumentParser(description="Benchmark 2: Latency Test")
    parser.add_argument("--url", default="http://localhost:8000", help="VisionX server URL")
    parser.add_argument("--rpm", type=int, default=15, help="API rate limit (requests/minute)")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    gap_seconds = 60.0 / args.rpm

    print(f"\n⏱️  Latency Benchmark")
    print(f"   Server: {base_url}")
    print(f"   Queries: {len(LATENCY_SINGLE_QUERIES)} single + {len(LATENCY_ALL_QUERIES)} ALL = 20 total")
    print(f"   Rate limit: {args.rpm} RPM ({gap_seconds:.1f}s between calls)\n")

    # Pre-flight check
    if not check_server(base_url):
        print("\n  Please start the VisionX server first:")
        print("    python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000\n")
        sys.exit(1)

    # Build the query list: 15 single + 5 ALL, interleaved
    queries = []
    for q in LATENCY_SINGLE_QUERIES:
        queries.append({"question": q, "expected_mode": "single"})
    for q in LATENCY_ALL_QUERIES:
        queries.append({"question": q, "expected_mode": "all"})

    # Run queries with rate limiting
    all_results = []
    rate_limited_count = 0

    print("── Running 20 queries ─────────────────────────────────────────\n")
    for i, q in enumerate(queries):
        if i > 0:
            time.sleep(gap_seconds)

        result = send_query(base_url, q["question"])
        result["question"] = q["question"]
        result["expected_mode"] = q["expected_mode"]
        result["query_index"] = i + 1
        all_results.append(result)

        if result.get("is_rate_limited"):
            rate_limited_count += 1

        status = "✅" if result["success"] else "❌"
        print(
            f"  [{i+1:2d}/20] {status} {result['latency_ms']:7.1f}ms │ "
            f"{result['mode']:6s} │ {q['question'][:50]}"
        )

    # ── Compute statistics ────────────────────────────────────────────────
    successful = [r for r in all_results if r["success"]]
    latencies = [r["latency_ms"] for r in successful]

    if latencies:
        stats = {
            "total_queries": len(queries),
            "successful": len(successful),
            "failed": len(queries) - len(successful),
            "rate_limited": rate_limited_count,
            "min_ms": round(min(latencies), 1),
            "max_ms": round(max(latencies), 1),
            "mean_ms": round(statistics.mean(latencies), 1),
            "median_ms": round(statistics.median(latencies), 1),
            "stdev_ms": round(statistics.stdev(latencies), 1) if len(latencies) > 1 else 0,
            "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 1) if len(latencies) >= 2 else latencies[0],
        }

        # Split by mode
        single_latencies = [r["latency_ms"] for r in successful if r["mode"] == "single"]
        all_latencies = [r["latency_ms"] for r in successful if r["mode"] == "all"]

        if single_latencies:
            stats["single_mean_ms"] = round(statistics.mean(single_latencies), 1)
        if all_latencies:
            stats["all_mean_ms"] = round(statistics.mean(all_latencies), 1)
    else:
        stats = {"total_queries": len(queries), "successful": 0, "failed": len(queries)}
        print("\n  ❌ No successful queries — cannot compute latency stats.")

    # ── Display summary ───────────────────────────────────────────────────
    print_table(
        ["Metric", "Value"],
        [
            ["Total queries", str(stats.get("total_queries", 0))],
            ["Successful", str(stats.get("successful", 0))],
            ["Failed / Rate-limited", f"{stats.get('failed', 0)} / {stats.get('rate_limited', 0)}"],
            ["Min latency", f"{stats.get('min_ms', 'N/A')} ms"],
            ["Max latency", f"{stats.get('max_ms', 'N/A')} ms"],
            ["Mean latency", f"{stats.get('mean_ms', 'N/A')} ms"],
            ["Median latency", f"{stats.get('median_ms', 'N/A')} ms"],
            ["Std deviation", f"{stats.get('stdev_ms', 'N/A')} ms"],
            ["P95 latency", f"{stats.get('p95_ms', 'N/A')} ms"],
            ["SINGLE mean", f"{stats.get('single_mean_ms', 'N/A')} ms"],
            ["ALL mean", f"{stats.get('all_mean_ms', 'N/A')} ms"],
        ],
        "LATENCY BENCHMARK — Summary",
    )

    # Save
    save_results("latency_results.json", {
        "benchmark": "latency",
        "config": {
            "server_url": base_url,
            "rpm_limit": args.rpm,
            "gap_seconds": gap_seconds,
        },
        "summary": stats,
        "results": all_results,
    })


if __name__ == "__main__":
    main()
