"""
Thread-safe Key-Value Cache for camera frames.

Decouples fast video capture threads from slower, on-demand AI inference.
Each camera ID maps to a bounded deque of recent JPEG-encoded frames.
"""
import threading
from collections import deque
from typing import Optional

from backend.config import FRAME_BUFFER_SIZE


class KVCache:
    """Thread-safe frame cache keyed by camera ID."""

    def __init__(self, max_frames: int = FRAME_BUFFER_SIZE):
        self._lock = threading.Lock()
        self._store: dict[str, deque[bytes]] = {}
        self._max_frames = max_frames

    # ── Write ─────────────────────────────────────────────────────────────

    def put(self, cam_id: str, frame_bytes: bytes) -> None:
        """Store a JPEG frame for the given camera."""
        with self._lock:
            if cam_id not in self._store:
                self._store[cam_id] = deque(maxlen=self._max_frames)
            self._store[cam_id].append(frame_bytes)

    # ── Read ──────────────────────────────────────────────────────────────

    def get_latest(self, cam_id: str) -> Optional[bytes]:
        """Return the most recent frame for a camera, or None."""
        with self._lock:
            buf = self._store.get(cam_id)
            if buf and len(buf) > 0:
                return buf[-1]
            return None

    def get_recent(self, cam_id: str, n: int = 3) -> list[bytes]:
        """Return the last *n* frames (for future temporal reasoning)."""
        with self._lock:
            buf = self._store.get(cam_id)
            if not buf:
                return []
            return list(buf)[-n:]

    # ── Management ────────────────────────────────────────────────────────

    def remove(self, cam_id: str) -> None:
        """Remove all cached frames for a camera."""
        with self._lock:
            self._store.pop(cam_id, None)

    def has(self, cam_id: str) -> bool:
        with self._lock:
            buf = self._store.get(cam_id)
            return buf is not None and len(buf) > 0

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())
