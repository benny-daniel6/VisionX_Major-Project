"""
Shared benchmark fixtures — camera metadata, 18-question test set, and helpers.
"""
import sys
import io

# Fix Windows cp1252 encoding crash for emoji/unicode box-drawing chars
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# Two cameras with distinct domains for clear routing separation.

BENCH_CAMERAS = [
    {
        "id": "cam_front_door",
        "name": "Front Door",
        "source": "rtsp://fake:554/front",
        "keywords": ["entrance", "door", "visitor", "package", "driveway", "porch"],
        "active": True,
        "has_frame": True,
    },
    {
        "id": "cam_kitchen",
        "name": "Kitchen",
        "source": "rtsp://fake:554/kitchen",
        "keywords": ["kitchen", "stove", "fridge", "cooking", "sink", "food"],
        "active": True,
        "has_frame": True,
    },
]

# ── 18-Question Test Set ───────────────────────────────────────────────────────
# 3 buckets × 6 questions each.
#
# expected_camera: the camera ID the router should select
#                  "ALL" means classify_question should return ALL mode
# bucket: "explicit" | "ambiguous" | "global"

ROUTING_TEST_SET = [
    # ── Bucket 1: Explicit (direct keyword / name match) ───────────────────
    {
        "question": "Is someone standing at the front door?",
        "expected_camera": "cam_front_door",
        "expected_mode": "SINGLE",
        "bucket": "explicit",
    },
    {
        "question": "Show me the kitchen camera feed",
        "expected_camera": "cam_kitchen",
        "expected_mode": "SINGLE",
        "bucket": "explicit",
    },
    {
        "question": "Is the stove still on?",
        "expected_camera": "cam_kitchen",
        "expected_mode": "SINGLE",
        "bucket": "explicit",
    },
    {
        "question": "Did a visitor ring the doorbell?",
        "expected_camera": "cam_front_door",
        "expected_mode": "SINGLE",
        "bucket": "explicit",
    },
    {
        "question": "Is anyone cooking right now?",
        "expected_camera": "cam_kitchen",
        "expected_mode": "SINGLE",
        "bucket": "explicit",
    },
    {
        "question": "Is there a package on the porch?",
        "expected_camera": "cam_front_door",
        "expected_mode": "SINGLE",
        "bucket": "explicit",
    },

    # ── Bucket 2: Ambiguous (requires semantic reasoning, no direct match) ─
    {
        "question": "Did my DoorDash order arrive?",
        "expected_camera": "cam_front_door",
        "expected_mode": "SINGLE",
        "bucket": "ambiguous",
    },
    {
        "question": "Is breakfast ready?",
        "expected_camera": "cam_kitchen",
        "expected_mode": "SINGLE",
        "bucket": "ambiguous",
    },
    {
        "question": "Can you check if the delivery guy is here?",
        "expected_camera": "cam_front_door",
        "expected_mode": "SINGLE",
        "bucket": "ambiguous",
    },
    {
        "question": "Did someone leave the gas on?",
        "expected_camera": "cam_kitchen",
        "expected_mode": "SINGLE",
        "bucket": "ambiguous",
    },
    {
        "question": "Is the pizza guy outside?",
        "expected_camera": "cam_front_door",
        "expected_mode": "SINGLE",
        "bucket": "ambiguous",
    },
    {
        "question": "What is being prepared for dinner?",
        "expected_camera": "cam_kitchen",
        "expected_mode": "SINGLE",
        "bucket": "ambiguous",
    },

    # ── Bucket 3: Global (should trigger ALL mode) ─────────────────────────
    {
        "question": "Is anyone in the building right now?",
        "expected_camera": "ALL",
        "expected_mode": "ALL",
        "bucket": "global",
    },
    {
        "question": "How many people are visible across all cameras?",
        "expected_camera": "ALL",
        "expected_mode": "ALL",
        "bucket": "global",
    },
    {
        "question": "Is everyone safe everywhere?",
        "expected_camera": "ALL",
        "expected_mode": "ALL",
        "bucket": "global",
    },
    {
        "question": "Give me a status update from every camera",
        "expected_camera": "ALL",
        "expected_mode": "ALL",
        "bucket": "global",
    },
    {
        "question": "Where is my cat? Search all rooms",
        "expected_camera": "ALL",
        "expected_mode": "ALL",
        "bucket": "global",
    },
    {
        "question": "Is there any motion detected anywhere?",
        "expected_camera": "ALL",
        "expected_mode": "ALL",
        "bucket": "global",
    },
]


# ── Latency Test Questions ─────────────────────────────────────────────────────
# 15 single-camera + 5 ALL-mode queries = 20 total

LATENCY_SINGLE_QUERIES = [
    "Is there anyone at the front door?",
    "What do you see in the kitchen?",
    "Is the door locked?",
    "Is there food on the counter?",
    "Did someone just come in?",
    "Is the oven turned off?",
    "Can you see any cars in the driveway?",
    "What's happening near the entrance?",
    "Are the lights on in the kitchen?",
    "Is the fridge door open?",
    "Is someone waiting outside?",
    "How many people are in the kitchen?",
    "Is the porch light on?",
    "What's on the stove?",
    "Can you see through the front door window?",
]

LATENCY_ALL_QUERIES = [
    "How many people are visible across all cameras?",
    "Is anyone home? Check everywhere",
    "Give me a status report from every camera",
    "Is there any unusual activity anywhere?",
    "Are all rooms empty right now?",
]


# ── Token-Cost Session Questions ───────────────────────────────────────────────
# A mix of single/all queries for realistic 10-min sessions

TOKEN_COST_QUERIES = [
    "Is someone at the door?",
    "What's cooking in the kitchen?",
    "Any packages on the porch?",
    "How many people are visible across all cameras?",
    "Is the stove on?",
    "Did anyone ring the doorbell?",
    "Is the fridge open?",
    "Is anyone in the building?",
    "What's on the kitchen counter?",
    "Are there any visitors outside?",
    "Is everyone safe everywhere?",
    "Is someone cooking right now?",
    "Check all cameras for movement",
    "Is the entrance clear?",
    "What time is it on the kitchen clock?",
    "How many people total across all feeds?",
    "Is there a car in the driveway?",
    "Give me a full status report from every camera",
    "Is the kitchen light on?",
    "Any activity near the front door?",
    "Is the whole house empty?",
    "Did my delivery arrive?",
    "What's happening everywhere right now?",
    "Is there smoke in the kitchen?",
    "Can you see who's at the entrance?",
    "Are all rooms occupied?",
    "Is the front door open or closed?",
    "What's the situation in the kitchen?",
    "Search everywhere for my dog",
    "Is there any person visible on any camera?",
]


# ── Helper Utilities ───────────────────────────────────────────────────────────

import time
import json
import os
from pathlib import Path
from datetime import datetime


RESULTS_DIR = Path(__file__).parent / "results"


def ensure_results_dir():
    """Create the results directory if it doesn't exist."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def rate_limit_sleep(rpm: int = 15):
    """Sleep enough to stay within the given RPM limit."""
    gap = 60.0 / rpm
    time.sleep(gap)


def save_results(filename: str, data: dict):
    """Save benchmark results as JSON to the results directory."""
    ensure_results_dir()
    filepath = RESULTS_DIR / filename
    data["_metadata"] = {
        "timestamp": datetime.now().isoformat(),
        "results_file": str(filepath),
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\n💾 Results saved to: {filepath}")
    return filepath


def print_table(headers: list[str], rows: list[list[str]], title: str = ""):
    """Print a formatted ASCII table to console."""
    if title:
        print(f"\n{'═' * 70}")
        print(f"  {title}")
        print(f"{'═' * 70}")

    # Calculate column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))

    # Header
    header_line = " │ ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    separator = "─┼─".join("─" * col_widths[i] for i in range(len(headers)))
    print(f"  {header_line}")
    print(f"  {separator}")

    # Rows
    for row in rows:
        line = " │ ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row))
        print(f"  {line}")
    print()
