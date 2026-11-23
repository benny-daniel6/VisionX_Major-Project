# Testing Offline Routing Fallback & Cloud Degradation

This guide explains how to manually verify the offline fallback and graceful degradation behavior in VisionX.

---

## Prerequisites

1. Have the server running with simulated or real cameras:
   ```bash
   python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
   ```
2. Verify cameras are loaded (e.g., Camera 1: **Front Door** (`cam_front_door`), Camera 2: **Kitchen** (`cam_kitchen`)).

---

## Test Scenario 1: Simulating Cloud API Outage / Offline

To simulate network disconnection or an unreachable Gemini API:
- Temporarily change `GOOGLE_API_KEY` in `.env` to an invalid string like `AIzaSyInvalidKeyForTesting12345`, or disconnect your network adapter.
- Restart or reload the server.

---

## Test Scenario 2: Verifying Keyword Fallback to a Non-First Camera

Submit a question targeting the second camera (`cam_kitchen`):
- **Query**: `"Is the stove still on?"` or `"Check the kitchen sink"`

### Expected Log Output (Terminal):
Look for the three distinct routing state logs in the terminal:
```text
ERROR │ Routing failed: ... (API key invalid or network unreachable)
INFO  │ Local keyword router matched keyword 'stove' -> ID: cam_kitchen
INFO  │ Routing state: (b) Gemini routing failed but keyword fallback succeeded -> camera_id=cam_kitchen
```
Notice that:
- Routing **did NOT** blindly default to the first camera (`cam_front_door`).
- It properly engaged **state (b)**: Gemini routing failed, but keyword fallback routed to `cam_kitchen`.

### Expected UI Behavior:
1. **Source Label**: Displays `Kitchen [⚡ Keyword Fallback]`, visually indicating that offline fallback was used.
2. **Camera Feed**: Highlights the Kitchen camera feed.
3. **Response Area**:
   - Displays: `"Visual analysis unavailable — Gemini API key is missing, invalid, or unauthorized."` (or `"Visual analysis unavailable — cloud offline..."` if network disconnected).
   - Shows the degraded styling (light red border / background notice) rather than masquerading as a normal answer.

---

## Test Scenario 3: Verifying Last-Resort Default (State c)

Submit an ambiguous query with no keyword match while offline:
- **Query**: `"What color is the sky outside right now?"`

### Expected Log Output (Terminal):
```text
ERROR   │ Routing failed: ...
INFO    │ Local keyword router found no match for query: 'What color is the sky outside right now?'
ERROR   │ Keyword routing fallback failed: Keyword router found no matching camera keywords or name
WARNING │ Routing state: (c) Both failed and we defaulted to the first camera -> camera_id=cam_front_door
```
Notice that:
- It correctly engages **state (c)** when both the cloud router and the keyword fallback fail.

---

## Test Scenario 4: Automated Benchmark Verification

To verify that `route_query_local` operates identically between the benchmark and the application runtime without API calls:
```bash
python -m benchmarks.benchmark_routing --keyword
```
Expected result:
- 18/18 questions evaluated using the exact same keyword logic and camera schema (`cam["name"]` and `cam["keywords"]`).
