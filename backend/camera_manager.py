"""
Multi-threaded Camera Manager.

Manages connections to local webcams and network IP cameras.
Each camera runs its own daemon thread that captures frames via OpenCV
and stores JPEG-encoded bytes into the shared KVCache.
"""
import cv2
import time
import uuid
import threading
import logging
import json
import os
from dataclasses import dataclass, field
from typing import Union, Optional
from urllib.parse import urlparse, urlunparse

from backend.kv_cache import KVCache
from backend.config import (
    CAPTURE_FPS,
    JPEG_QUALITY,
    CAPTURE_WIDTH,
    CAPTURE_HEIGHT,
)

logger = logging.getLogger(__name__)

# Connection timeout for network cameras (seconds)
NETWORK_CONNECT_TIMEOUT_MS = 10_000
MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 1.0  # seconds, doubles each retry


@dataclass
class CameraSource:
    id: str
    name: str
    source: Union[int, str]          # OpenCV index or URL
    keywords: list[str]
    active: bool = True
    thread: Optional[threading.Thread] = field(default=None, repr=False)
    error: Optional[str] = None


def _normalize_ip_webcam_url(source: str) -> str:
    """
    Normalize IP Webcam app URLs for OpenCV compatibility.

    Handles common user inputs:
      - 192.168.1.5:8080          → http://192.168.1.5:8080/video
      - http://192.168.1.5:8080   → http://192.168.1.5:8080/video
      - http://192.168.1.5:8080/  → http://192.168.1.5:8080/video
      - http://192.168.1.5:8080/video  → unchanged
      - http://192.168.1.5:8080/shot.jpg  → unchanged (snapshot mode)
    """
    original = source.strip()

    # If it doesn't start with a scheme, add http://
    if not original.startswith(("http://", "https://", "rtsp://")):
        original = f"http://{original}"

    parsed = urlparse(original)

    # Only auto-append /video for http(s) URLs that look like IP Webcam
    if parsed.scheme in ("http", "https"):
        path = parsed.path.rstrip("/")
        # Known valid IP Webcam paths — leave them alone
        known_paths = ("/video", "/shot.jpg", "/videofeed", "/mjpegfeed",
                       "/audio.wav", "/photoaf.jpg")
        if path not in known_paths and not path:
            # Bare URL with no path → append /video for MJPEG stream
            parsed = parsed._replace(path="/video")
            logger.info("Auto-appended /video to IP camera URL: %s → %s",
                        source, urlunparse(parsed))

    return urlunparse(parsed)


def _open_camera_with_timeout(source: Union[int, str],
                               timeout_ms: int = NETWORK_CONNECT_TIMEOUT_MS
                               ) -> Optional[cv2.VideoCapture]:
    """
    Open a camera source with a timeout for network streams.
    Returns the opened VideoCapture, or None on failure.
    """
    is_network = isinstance(source, str)

    cap = cv2.VideoCapture()

    if is_network:
        # Set timeouts BEFORE opening for network streams
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms)

    opened = cap.open(source)
    if not opened or not cap.isOpened():
        cap.release()
        return None

    return cap


class CameraManager:
    """Manages multiple camera capture threads."""

    def __init__(self, cache: KVCache, config_file: str = "cameras.json"):
        self._cache = cache
        self._cameras: dict[str, CameraSource] = {}
        self._lock = threading.Lock()
        self._config_file = config_file

    def load_cameras(self, default_cameras: list[dict] | None = None) -> None:
        """Load cameras from disk, or fallback to defaults."""
        cameras_loaded = False
        if os.path.exists(self._config_file):
            try:
                with open(self._config_file, "r") as f:
                    cam_data = json.load(f)
                if cam_data:
                    for c in cam_data:
                        self.add_camera(
                            name=c["name"],
                            source=c["source"],
                            keywords=c.get("keywords", []),
                            cam_id=c.get("id"),
                            skip_save=True
                        )
                    logger.info("Loaded %d cameras from %s", len(cam_data), self._config_file)
                    cameras_loaded = True
            except Exception as e:
                logger.error("Failed to load %s: %s", self._config_file, e)

        if not cameras_loaded and default_cameras:
            for c in default_cameras:
                self.add_camera(
                    name=c["name"],
                    source=c["source"],
                    keywords=c.get("keywords", []),
                    skip_save=True
                )
            logger.info("Loaded %d default cameras", len(default_cameras))
            self._save_config()

    def _save_config(self) -> None:
        """Save current cameras to disk."""
        data = []
        with self._lock:
            for cam in self._cameras.values():
                data.append({
                    "id": cam.id,
                    "name": cam.name,
                    "source": cam.source,
                    "keywords": cam.keywords
                })
        try:
            with open(self._config_file, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error("Failed to save %s: %s", self._config_file, e)

    # ── Public API ────────────────────────────────────────────────────────

    def add_camera(
        self,
        name: str,
        source: Union[int, str],
        keywords: list[str] | None = None,
        cam_id: str | None = None,
        skip_save: bool = False,
    ) -> CameraSource:
        """Register a new camera and start its capture thread."""
        cam_id = cam_id or uuid.uuid4().hex[:8]

        # Normalize URL for network cameras
        if isinstance(source, str):
            source = _normalize_ip_webcam_url(source)

        cam = CameraSource(
            id=cam_id,
            name=name,
            source=source,
            keywords=keywords or [],
        )

        thread = threading.Thread(
            target=self._capture_loop,
            args=(cam,),
            daemon=True,
            name=f"cam-{cam_id}",
        )
        cam.thread = thread

        with self._lock:
            self._cameras[cam_id] = cam

        thread.start()
        logger.info("Camera '%s' (%s) started → id=%s", name, source, cam_id)
        if not skip_save:
            self._save_config()
        return cam

    def remove_camera(self, cam_id: str, skip_save: bool = False) -> bool:
        """Stop a camera's capture thread and clean up."""
        with self._lock:
            cam = self._cameras.pop(cam_id, None)
        if cam is None:
            return False
        cam.active = False
        if cam.thread and cam.thread.is_alive():
            cam.thread.join(timeout=3.0)
        self._cache.remove(cam_id)
        logger.info("Camera '%s' removed (id=%s)", cam.name, cam_id)
        if not skip_save:
            self._save_config()
        return True

    def get_cameras(self) -> list[dict]:
        """Return metadata for all registered cameras."""
        with self._lock:
            cameras = list(self._cameras.values())
        return [
            {
                "id": c.id,
                "name": c.name,
                "source": str(c.source),
                "keywords": c.keywords,
                "active": c.active,
                "has_frame": self._cache.has(c.id),
                "error": c.error,
            }
            for c in cameras
        ]

    def get_camera(self, cam_id: str) -> Optional[CameraSource]:
        with self._lock:
            return self._cameras.get(cam_id)

    def retry_camera(self, cam_id: str) -> Optional[CameraSource]:
        """Force restart a camera's capture thread, clearing any errors."""
        with self._lock:
            cam = self._cameras.get(cam_id)
        if cam is None:
            return None

        # Stop old capture thread
        cam.active = False
        if cam.thread and cam.thread.is_alive():
            cam.thread.join(timeout=3.0)
        self._cache.remove(cam_id)

        # Restart
        cam.active = True
        cam.error = None
        thread = threading.Thread(
            target=self._capture_loop,
            args=(cam,),
            daemon=True,
            name=f"cam-{cam.id}",
        )
        cam.thread = thread
        thread.start()
        logger.info("Camera '%s' manually retried → restarted capture", cam.name)
        return cam

    def update_camera(
        self,
        cam_id: str,
        name: str | None = None,
        source: Union[int, str, None] = None,
        keywords: list[str] | None = None,
    ) -> Optional[CameraSource]:
        """Update an existing camera's properties. Restarts capture if source changes."""
        with self._lock:
            cam = self._cameras.get(cam_id)
        if cam is None:
            return None

        # Normalize URL if source is being updated
        if isinstance(source, str):
            source = _normalize_ip_webcam_url(source)

        source_changed = source is not None and str(source) != str(cam.source)

        if name is not None:
            cam.name = name
        if keywords is not None:
            cam.keywords = keywords

        if source_changed:
            # Stop old capture thread
            cam.active = False
            if cam.thread and cam.thread.is_alive():
                cam.thread.join(timeout=3.0)
            self._cache.remove(cam_id)

            # Update source and restart
            cam.source = source
            cam.active = True
            cam.error = None
            thread = threading.Thread(
                target=self._capture_loop,
                args=(cam,),
                daemon=True,
                name=f"cam-{cam_id}",
            )
            cam.thread = thread
            thread.start()
            logger.info("Camera '%s' source changed → restarted capture", cam.name)

        logger.info("Camera '%s' updated (id=%s)", cam.name, cam_id)
        self._save_config()
        return cam

    def get_frame(self, cam_id: str) -> Optional[bytes]:
        """Retrieve the latest JPEG frame for a camera."""
        return self._cache.get_latest(cam_id)

    # ── Capture Loop ─────────────────────────────────────────────────────

    def _capture_loop(self, cam: CameraSource) -> None:
        """Continuously capture frames from a camera source with retry logic."""
        delay = 1.0 / CAPTURE_FPS
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        is_network = isinstance(cam.source, str)
        retry_count = 0
        retry_delay = INITIAL_RETRY_DELAY

        while cam.active and retry_count < MAX_RETRIES:
            # ── Connect ──────────────────────────────────────────────
            cam.error = f"Connecting to {cam.source}..." if is_network else None
            logger.info("Opening camera '%s' (source=%s, attempt %d/%d)",
                        cam.name, cam.source, retry_count + 1, MAX_RETRIES)

            cap = _open_camera_with_timeout(cam.source)

            if cap is None:
                retry_count += 1
                cam.error = (
                    f"Cannot connect to {cam.source}. "
                    f"Retry {retry_count}/{MAX_RETRIES} in {retry_delay:.0f}s. "
                    f"Check that the IP Webcam app is running and the URL is correct."
                )
                logger.warning("Failed to open '%s': %s", cam.name, cam.error)

                # Wait before retrying (but check cam.active periodically)
                wait_until = time.monotonic() + retry_delay
                while cam.active and time.monotonic() < wait_until:
                    time.sleep(0.5)
                retry_delay = min(retry_delay * 2, 16.0)  # Exponential backoff, max 16s
                continue

            # ── Connected — reset retries ────────────────────────────
            retry_count = 0
            retry_delay = INITIAL_RETRY_DELAY
            cam.error = None

            # Set resolution
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_HEIGHT)

            logger.info("Capture loop started for '%s'", cam.name)
            consecutive_failures = 0

            try:
                while cam.active:
                    start = time.monotonic()
                    ret, frame = cap.read()
                    if not ret:
                        consecutive_failures += 1
                        cam.error = f"Frame read failed ({consecutive_failures}x)"
                        logger.warning("Frame read failed for '%s' (%d consecutive)",
                                       cam.name, consecutive_failures)

                        if consecutive_failures >= 10:
                            logger.warning("Too many failures for '%s', reconnecting...",
                                           cam.name)
                            break  # Break inner loop → reconnect via outer loop

                        time.sleep(0.5)
                        continue

                    # Frame read succeeded
                    consecutive_failures = 0
                    cam.error = None

                    # Resize if needed
                    h, w = frame.shape[:2]
                    if w != CAPTURE_WIDTH or h != CAPTURE_HEIGHT:
                        frame = cv2.resize(frame, (CAPTURE_WIDTH, CAPTURE_HEIGHT))

                    # Encode to JPEG
                    ok, jpeg = cv2.imencode(".jpg", frame, encode_params)
                    if ok:
                        self._cache.put(cam.id, jpeg.tobytes())

                    # Maintain target FPS
                    elapsed = time.monotonic() - start
                    sleep_time = delay - elapsed
                    if sleep_time > 0:
                        time.sleep(sleep_time)
            except Exception as e:
                cam.error = str(e)
                logger.exception("Capture loop error for '%s': %s", cam.name, e)
            finally:
                cap.release()

            # If we broke out of the inner loop and cam is still active, retry
            if cam.active:
                cam.error = f"Connection lost — reconnecting in {retry_delay:.0f}s..."
                logger.info("Reconnecting camera '%s' in %.0fs", cam.name, retry_delay)
                wait_until = time.monotonic() + retry_delay
                while cam.active and time.monotonic() < wait_until:
                    time.sleep(0.5)

        # Exhausted retries or cam deactivated
        if retry_count >= MAX_RETRIES:
            cam.error = (
                f"Failed to connect after {MAX_RETRIES} attempts. "
                f"Check the URL and ensure IP Webcam is running on your phone."
            )
            logger.error("Camera '%s' gave up after %d retries", cam.name, MAX_RETRIES)

        cam.active = False
        logger.info("Capture loop ended for '%s'", cam.name)
