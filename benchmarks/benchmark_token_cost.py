"""
Benchmark 3 — Token-Cost Graph (10-min session, extrapolated to 1 hour).

Compares cumulative token usage between:
  - Agentic router: classify → route → analyze (normal flow)
  - Naive baseline: always send to ALL cameras (no routing)

Prerequisites:
    - VisionX server running with 2+ cameras active
    - matplotlib installed: pip install matplotlib

Usage:
    python -m benchmarks.benchmark_token_cost
    python -m benchmarks.benchmark_token_cost --duration 600 --rpm 15
"""

import sys
import io
import time
import random
import argparse
import json
from urllib import request as urllib_request
from urllib.error import URLError

# Fix Windows cp1252 encoding crash for emoji/unicode chars
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.conftest import (
    TOKEN_COST_QUERIES,
    save_results,
    print_table,
)


def check_server(base_url: str) -> dict | None:
    """Verify server and return camera info."""
    try:
        url = f"{base_url}/api/cameras"
        req = urllib_request.Request(url)
        with urllib_request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            cameras = data.get("cameras", [])
            active = [c for c in cameras if c.get("active") and c.get("has_frame")]
            if len(active) < 2:
                print(f"  ⚠️  Need 2+ active cameras for meaningful comparison, found {len(active)}")
                if not active:
                    return None
            return {"cameras": cameras, "active": active}
    except URLError as e:
        print(f"  ❌ Cannot connect to {base_url}: {e}")
        return None


def send_query(base_url: str, question: str, force_all: bool = False) -> dict:
    """Send a /api/ask query. If force_all=True, we don't pass camera_id to let
    the naive baseline naturally route (but we'll also need to simulate ALL mode)."""
    url = f"{base_url}/api/ask"
    payload = {"question": question}

    body = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib_request.urlopen(req, timeout=60) as resp:
            return {
                "success": True,
                "response": json.loads(resp.read().decode()),
            }
    except URLError as e:
        return {"success": False, "error": str(e)}


def get_token_usage(base_url: str) -> dict:
    """Get the last token usage from the server.
    Since we can't directly access GeminiService from a client, we'll estimate
    tokens based on response size as a proxy, OR we instrument a special endpoint.

    For this benchmark, we use a heuristic: count response answer length as a
    rough proxy for output tokens, and estimate input tokens from the query.
    """
    return {"prompt_tokens": 0, "candidates_tokens": 0, "total_tokens": 0}


def run_session(
    base_url: str,
    queries: list[str],
    duration_seconds: int,
    min_interval: float,
    max_interval: float,
    mode_label: str,
    force_all_prefix: bool = False,
) -> list[dict]:
    """
    Run a timed session of queries with random intervals.
    Returns timestamped list of results with cumulative token estimates.
    """
    results = []
    cumulative_tokens = 0
    session_start = time.time()
    query_index = 0

    print(f"\n── {mode_label} Session ({duration_seconds}s) ─────────────────")

    while (time.time() - session_start) < duration_seconds:
        question = queries[query_index % len(queries)]

        # For naive baseline, prepend "Check all cameras:" to force ALL mode
        if force_all_prefix:
            actual_question = f"Check all cameras: {question}"
        else:
            actual_question = question

        elapsed = time.time() - session_start
        result = send_query(base_url, actual_question)

        if result["success"]:
            response = result["response"]
            answer = response.get("answer", "")
            mode = response.get("mode", "unknown")

            # Estimate tokens from the response:
            # - Input: ~50 base prompt tokens + ~4 tokens per word in question
            #   + image tokens (~258 per image for Gemini)
            # - Output: ~1.3 tokens per word in answer
            question_tokens = len(question.split()) * 4 + 50
            cams_analyzed = response.get("cameras_analyzed", [])
            num_images = len(cams_analyzed) if cams_analyzed else 1
            image_tokens = num_images * 258  # Gemini's per-image token cost
            prompt_tokens = question_tokens + image_tokens
            output_tokens = int(len(answer.split()) * 1.3)
            total_tokens = prompt_tokens + output_tokens

            # For ALL mode, the naive baseline sends ALL camera images
            # (hence more image tokens)
            if force_all_prefix and mode != "all":
                # If the server didn't use ALL mode despite our prefix,
                # estimate as if it sent 1 image only
                pass

            cumulative_tokens += total_tokens

            results.append({
                "query_index": query_index + 1,
                "elapsed_seconds": round(elapsed, 1),
                "question": question[:60],
                "mode": mode,
                "prompt_tokens_est": prompt_tokens,
                "output_tokens_est": output_tokens,
                "total_tokens_est": total_tokens,
                "cumulative_tokens": cumulative_tokens,
                "num_images": num_images,
            })

            status_icon = "🔵" if mode == "single" else "🟢"
            print(
                f"  {status_icon} [{query_index+1:3d}] {elapsed:6.1f}s │ "
                f"{mode:6s} │ imgs={num_images} │ "
                f"+{total_tokens:5d} = {cumulative_tokens:6d} tokens │ "
                f"{question[:40]}"
            )
        else:
            print(f"  ❌ [{query_index+1:3d}] {elapsed:6.1f}s │ FAILED: {result.get('error', 'unknown')[:50]}")
            results.append({
                "query_index": query_index + 1,
                "elapsed_seconds": round(elapsed, 1),
                "question": question[:60],
                "mode": "error",
                "total_tokens_est": 0,
                "cumulative_tokens": cumulative_tokens,
            })

        query_index += 1

        # Random sleep interval (20-30 seconds default)
        sleep_time = random.uniform(min_interval, max_interval)
        remaining = duration_seconds - (time.time() - session_start)
        if remaining <= 0:
            break
        time.sleep(min(sleep_time, remaining))

    print(f"  ── Session complete: {query_index} queries, {cumulative_tokens} total tokens")
    return results


def plot_token_cost(
    agentic_results: list[dict],
    naive_results: list[dict],
    duration_seconds: int,
    output_path: Path,
):
    """Generate the token-cost graph with 1-hour extrapolation."""
    try:
        import matplotlib
        matplotlib.use("Agg")  # Non-interactive backend
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker
    except ImportError:
        print("\n  ⚠️  matplotlib not installed. Skipping graph generation.")
        print("      Install with: pip install matplotlib\n")
        return False

    fig, ax = plt.subplots(1, 1, figsize=(12, 6))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#161b22")

    # Colors
    agentic_color = "#58a6ff"
    naive_color = "#f85149"
    extrapolated_alpha = 0.4

    # ── Plot actual data ──────────────────────────────────────────────
    if agentic_results:
        a_times = [r["elapsed_seconds"] / 60 for r in agentic_results]
        a_tokens = [r["cumulative_tokens"] for r in agentic_results]
        ax.plot(a_times, a_tokens, color=agentic_color, linewidth=2.5,
                marker="o", markersize=4, label="Agentic Router (actual)", zorder=3)

    if naive_results:
        n_times = [r["elapsed_seconds"] / 60 for r in naive_results]
        n_tokens = [r["cumulative_tokens"] for r in naive_results]
        ax.plot(n_times, n_tokens, color=naive_color, linewidth=2.5,
                marker="s", markersize=4, label="Naive Baseline (actual)", zorder=3)

    # ── Extrapolate to 1 hour ─────────────────────────────────────────
    actual_minutes = duration_seconds / 60
    extrapolation_factor = 60 / actual_minutes  # e.g. 6× for 10-min session

    if agentic_results:
        final_a = agentic_results[-1]["cumulative_tokens"]
        extrapolated_a = int(final_a * extrapolation_factor)
        ax.plot(
            [a_times[-1], 60], [final_a, extrapolated_a],
            color=agentic_color, linewidth=2, linestyle="--", alpha=extrapolated_alpha,
            label=f"Agentic (extrapolated → {extrapolated_a:,} tokens/hr)",
        )

    if naive_results:
        final_n = naive_results[-1]["cumulative_tokens"]
        extrapolated_n = int(final_n * extrapolation_factor)
        ax.plot(
            [n_times[-1], 60], [final_n, extrapolated_n],
            color=naive_color, linewidth=2, linestyle="--", alpha=extrapolated_alpha,
            label=f"Naive (extrapolated → {extrapolated_n:,} tokens/hr)",
        )

    # ── Styling ───────────────────────────────────────────────────────
    ax.set_title(
        f"Cumulative Token Cost Over {int(actual_minutes)}-Minute Session\n"
        f"(Extrapolated to 1 Hour)",
        color="white", fontsize=14, fontweight="bold", pad=15,
    )
    ax.set_xlabel("Time (minutes)", color="white", fontsize=12)
    ax.set_ylabel("Cumulative Tokens", color="white", fontsize=12)

    # Add vertical line at session boundary
    ax.axvline(x=actual_minutes, color="#8b949e", linestyle=":", alpha=0.5, linewidth=1)
    ax.text(
        actual_minutes + 0.3, ax.get_ylim()[1] * 0.95,
        f"← Actual ({int(actual_minutes)} min) | Extrapolated →",
        color="#8b949e", fontsize=9, va="top",
    )

    ax.legend(loc="upper left", fontsize=10, facecolor="#21262d", edgecolor="#30363d",
              labelcolor="white", framealpha=0.9)
    ax.tick_params(colors="white")
    ax.spines["bottom"].set_color("#30363d")
    ax.spines["left"].set_color("#30363d")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, alpha=0.15, color="#8b949e")
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, p: f"{int(x):,}"))

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)

    print(f"\n📊 Graph saved to: {output_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Benchmark 3: Token-Cost Graph")
    parser.add_argument("--url", default="http://localhost:8000", help="VisionX server URL")
    parser.add_argument("--duration", type=int, default=600, help="Session duration in seconds (default: 600 = 10 min)")
    parser.add_argument("--min-interval", type=float, default=20, help="Min seconds between queries (default: 20)")
    parser.add_argument("--max-interval", type=float, default=30, help="Max seconds between queries (default: 30)")
    parser.add_argument("--rpm", type=int, default=15, help="API rate limit (for display only)")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")

    print(f"\n📊 Token-Cost Benchmark")
    print(f"   Server: {base_url}")
    print(f"   Duration: {args.duration}s ({args.duration/60:.0f} min)")
    print(f"   Query interval: {args.min_interval}-{args.max_interval}s")
    print(f"   Estimated queries: ~{args.duration / ((args.min_interval + args.max_interval) / 2):.0f}\n")

    # Pre-flight check
    server_info = check_server(base_url)
    if not server_info:
        print("\n  Please start the VisionX server first:")
        print("    python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000\n")
        sys.exit(1)

    queries = TOKEN_COST_QUERIES

    # ── Run Agentic Router Session ────────────────────────────────────
    agentic_results = run_session(
        base_url=base_url,
        queries=queries,
        duration_seconds=args.duration,
        min_interval=args.min_interval,
        max_interval=args.max_interval,
        mode_label="Agentic Router",
        force_all_prefix=False,
    )

    # Brief cooldown between sessions
    print("\n  ⏳ Cooldown (15s) before naive baseline session...")
    time.sleep(15)

    # ── Run Naive Baseline Session ────────────────────────────────────
    naive_results = run_session(
        base_url=base_url,
        queries=queries,
        duration_seconds=args.duration,
        min_interval=args.min_interval,
        max_interval=args.max_interval,
        mode_label="Naive Baseline (force ALL)",
        force_all_prefix=True,
    )

    # ── Summary Table ─────────────────────────────────────────────────
    a_total = agentic_results[-1]["cumulative_tokens"] if agentic_results else 0
    n_total = naive_results[-1]["cumulative_tokens"] if naive_results else 0
    savings = ((n_total - a_total) / n_total * 100) if n_total > 0 else 0
    extrapolation_factor = 3600 / args.duration

    print_table(
        ["Metric", "Agentic Router", "Naive Baseline"],
        [
            ["Queries sent", str(len(agentic_results)), str(len(naive_results))],
            ["Total tokens (actual)", f"{a_total:,}", f"{n_total:,}"],
            ["Extrapolated (1 hr)", f"{int(a_total * extrapolation_factor):,}", f"{int(n_total * extrapolation_factor):,}"],
            ["Token savings", f"{savings:.1f}%", "—"],
        ],
        "TOKEN-COST BENCHMARK — Summary",
    )

    # ── Generate Graph ────────────────────────────────────────────────
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    graph_path = results_dir / "token_cost_graph.png"

    plot_token_cost(agentic_results, naive_results, args.duration, graph_path)

    # ── Save raw data ─────────────────────────────────────────────────
    save_results("token_cost_data.json", {
        "benchmark": "token_cost",
        "config": {
            "server_url": base_url,
            "duration_seconds": args.duration,
            "min_interval": args.min_interval,
            "max_interval": args.max_interval,
            "extrapolation_factor": extrapolation_factor,
        },
        "summary": {
            "agentic_total_tokens": a_total,
            "naive_total_tokens": n_total,
            "agentic_extrapolated_1hr": int(a_total * extrapolation_factor),
            "naive_extrapolated_1hr": int(n_total * extrapolation_factor),
            "savings_pct": round(savings, 1),
            "agentic_queries": len(agentic_results),
            "naive_queries": len(naive_results),
        },
        "agentic_results": agentic_results,
        "naive_results": naive_results,
    })


if __name__ == "__main__":
    main()
