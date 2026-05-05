"""
Configuration constants for the Multi-Camera Video Analyzer.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env — try multiple paths for robustness
_this_dir = Path(__file__).resolve().parent
_project_root = _this_dir.parent
_env_candidates = [
    _project_root / ".env",          # Relative to this file
    Path.cwd() / ".env",             # Relative to working directory
]
for _env_path in _env_candidates:
    if _env_path.is_file():
        load_dotenv(_env_path, override=True)
        break

# ─── Google Gemini ────────────────────────────────────────────────────────────
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# Startup validation
if GOOGLE_API_KEY and GOOGLE_API_KEY != "PASTE_YOUR_KEY_HERE":
    print(f"  [OK] GOOGLE_API_KEY loaded ({GOOGLE_API_KEY[:8]}...)")
else:
    print("  [WARN] GOOGLE_API_KEY not set — AI features will not work")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# ─── Camera Defaults ─────────────────────────────────────────────────────────
DEFAULT_CAMERAS = [
    {
        "name": "Webcam",
        "source": 0,                       # OpenCV index for local webcam
        "keywords": ["desk", "room", "person", "laptop", "face", "office"],
    },
]

# ─── Capture Settings ────────────────────────────────────────────────────────
CAPTURE_FPS = 30                           # Target frames per second per camera
JPEG_QUALITY = 70                          # JPEG compression quality (0-100)
FRAME_BUFFER_SIZE = 5                      # Frames kept in deque per camera
CAPTURE_WIDTH = 640                        # Resize width for captured frames
CAPTURE_HEIGHT = 480                       # Resize height for captured frames

# ─── Server ───────────────────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 8000
