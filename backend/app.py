"""
FastAPI Application — Multi-Camera Video Analyzer.

Serves REST endpoints for camera management and AI queries,
WebSocket endpoints for live video streaming, and the frontend UI.
"""
import asyncio
import base64
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.config import DEFAULT_CAMERAS, HOST, PORT
from backend.kv_cache import KVCache
from backend.camera_manager import CameraManager
from backend.gemini_service import GeminiService

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(name)-20s │ %(levelname)-7s │ %(message)s",
)
logger = logging.getLogger(__name__)

# ── Shared State ───────────────────────────────────────────────────────────────
cache = KVCache()
cam_mgr = CameraManager(cache)
gemini = GeminiService()


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: load saved cameras or fallback to defaults
    cam_mgr.load_cameras(DEFAULT_CAMERAS)
    logger.info("✓ Camera initialization complete")
    yield
    # Shutdown: stop all cameras without wiping the saved config
    for cam in cam_mgr.get_cameras():
        cam_mgr.remove_camera(cam["id"], skip_save=True)
    logger.info("✓ All cameras stopped")


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Multi-Camera Video Analyzer",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ══════════════════════════════════════════════════════════════════════════════
#  REST ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════


@app.get("/")
async def serve_frontend():
    """Serve the main HTML page."""
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


# ── Camera Management ─────────────────────────────────────────────────────────


@app.get("/api/cameras")
async def list_cameras():
    """Return metadata for all registered cameras."""
    return JSONResponse({"cameras": cam_mgr.get_cameras()})


class AddCameraRequest(BaseModel):
    name: str
    source: str          # URL or integer index as string
    keywords: list[str] = []


@app.post("/api/cameras")
async def add_camera(req: AddCameraRequest):
    """Add a new camera source."""
    # Detect if source is an integer (local webcam index)
    source = req.source
    try:
        source = int(source)
    except ValueError:
        pass  # It's a URL string

    cam = cam_mgr.add_camera(
        name=req.name,
        source=source,
        keywords=req.keywords,
    )
    return JSONResponse({
        "status": "ok",
        "camera": {
            "id": cam.id,
            "name": cam.name,
            "source": str(cam.source),
            "keywords": cam.keywords,
        },
    })


@app.delete("/api/cameras/{cam_id}")
async def delete_camera(cam_id: str):
    """Remove a camera."""
    removed = cam_mgr.remove_camera(cam_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Camera not found")
    return JSONResponse({"status": "ok", "removed": cam_id})


@app.post("/api/cameras/{cam_id}/retry")
async def retry_camera(cam_id: str):
    """Force restart a camera connection."""
    cam = cam_mgr.retry_camera(cam_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
    return JSONResponse({"status": "ok", "message": "Retrying connection..."})


class UpdateCameraRequest(BaseModel):
    name: str | None = None
    source: str | None = None
    keywords: list[str] | None = None


@app.patch("/api/cameras/{cam_id}")
async def update_camera(cam_id: str, req: UpdateCameraRequest):
    """Update a camera's name, source, or keywords."""
    # Parse source to int if it's a local webcam index
    source = req.source
    if source is not None:
        try:
            source = int(source)
        except ValueError:
            pass

    cam = cam_mgr.update_camera(
        cam_id=cam_id,
        name=req.name,
        source=source,
        keywords=req.keywords,
    )
    if cam is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    return JSONResponse({
        "status": "ok",
        "camera": {
            "id": cam.id,
            "name": cam.name,
            "source": str(cam.source),
            "keywords": cam.keywords,
        },
    })


# ── AI Query ─────────────────────────────────────────────────────────────────


class AskRequest(BaseModel):
    question: str
    camera_id: str | None = None  # Optional: force a specific camera


@app.post("/api/ask")
async def ask_question(req: AskRequest):
    """
    Submit a natural language question.
    Classifies the question to decide:
      - SINGLE: Route to one camera, analyze that frame.
      - ALL: Analyze frames from ALL cameras together.
    """
    cameras = cam_mgr.get_cameras()
    active_cameras = [c for c in cameras if c["active"] and c["has_frame"]]

    if not active_cameras:
        return JSONResponse({
            "answer": "No active cameras with available frames. Please add or check your cameras.",
            "camera_id": None,
            "camera_name": None,
            "mode": "none",
        })

    # Step 0: Classify the question
    if req.camera_id:
        mode = "SINGLE"
    else:
        mode = gemini.classify_question(req.question, len(active_cameras), active_cameras)

    # ── MULTI-CAMERA MODE ────────────────────────────────────────────
    if mode == "ALL" and len(active_cameras) > 1:
        # Gather frames from ALL active cameras
        all_frames = []
        for cam in active_cameras:
            frame = cam_mgr.get_frame(cam["id"])
            if frame:
                all_frames.append({
                    "name": cam["name"],
                    "id": cam["id"],
                    "frame_bytes": frame,
                })

        if not all_frames:
            return JSONResponse({
                "answer": "Could not retrieve frames from any camera.",
                "camera_id": None,
                "camera_name": None,
                "mode": "all",
            })

        # Send ALL frames to Gemini in one call (thread pool)
        answer = await asyncio.to_thread(
            gemini.analyze_all_frames,
            req.question,
            all_frames,
        )

        # Return first frame as preview thumbnail
        frame_b64 = base64.b64encode(all_frames[0]["frame_bytes"]).decode("utf-8")
        camera_names = [f["name"] for f in all_frames]

        return JSONResponse({
            "answer": answer,
            "camera_id": None,
            "camera_name": ", ".join(camera_names),
            "cameras_analyzed": camera_names,
            "analyzed_frame": frame_b64,
            "mode": "all",
        })

    # ── SINGLE-CAMERA MODE ───────────────────────────────────────────
    # Step 1: Route to the best camera
    if req.camera_id:
        selected_id = req.camera_id
    elif len(active_cameras) == 1:
        selected_id = active_cameras[0]["id"]
    else:
        selected_id = await asyncio.to_thread(
            gemini.route_question, req.question, active_cameras
        )

    # Validate selection
    selected_cam = None
    for c in active_cameras:
        if c["id"] == selected_id:
            selected_cam = c
            break
    if not selected_cam:
        selected_cam = active_cameras[0]
        selected_id = selected_cam["id"]

    # Step 2: Get latest frame
    frame = cam_mgr.get_frame(selected_id)
    if not frame:
        return JSONResponse({
            "answer": f"Camera '{selected_cam['name']}' has no available frame right now.",
            "camera_id": selected_id,
            "camera_name": selected_cam["name"],
            "mode": "single",
        })

    # Step 3: Analyze with Gemini Vision (thread pool)
    answer = await asyncio.to_thread(
        gemini.analyze_frame,
        req.question,
        frame,
        selected_cam["name"],
    )

    frame_b64 = base64.b64encode(frame).decode("utf-8")

    return JSONResponse({
        "answer": answer,
        "camera_id": selected_id,
        "camera_name": selected_cam["name"],
        "analyzed_frame": frame_b64,
        "mode": "single",
    })


# ── Snapshot endpoint ─────────────────────────────────────────────────────────

@app.get("/api/cameras/{cam_id}/snapshot")
async def camera_snapshot(cam_id: str):
    """Return the latest JPEG frame as base64."""
    frame = cam_mgr.get_frame(cam_id)
    if not frame:
        raise HTTPException(status_code=404, detail="No frame available")
    return JSONResponse({
        "camera_id": cam_id,
        "frame": base64.b64encode(frame).decode("utf-8"),
    })


# ══════════════════════════════════════════════════════════════════════════════
#  WEBSOCKET ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════


@app.websocket("/ws/stream/{cam_id}")
async def ws_stream_camera(websocket: WebSocket, cam_id: str):
    """Stream JPEG frames for a specific camera via WebSocket."""
    await websocket.accept()
    logger.info("WebSocket connected for camera %s", cam_id)
    try:
        while True:
            frame = cam_mgr.get_frame(cam_id)
            if frame:
                await websocket.send_bytes(frame)
            await asyncio.sleep(1 / 30)  # ~30 FPS
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for camera %s", cam_id)
    except Exception as e:
        logger.warning("WebSocket error for camera %s: %s", cam_id, e)


@app.websocket("/ws/stream/grid")
async def ws_stream_grid(websocket: WebSocket):
    """
    Stream frames from ALL cameras as a JSON-like protocol.
    Each message: { "cam_id": "...", "frame": "<base64>" }
    Rotates through cameras round-robin style.
    """
    await websocket.accept()
    logger.info("WebSocket grid connected")
    try:
        while True:
            cameras = cam_mgr.get_cameras()
            for cam in cameras:
                if cam["active"] and cam["has_frame"]:
                    frame = cam_mgr.get_frame(cam["id"])
                    if frame:
                        b64 = base64.b64encode(frame).decode("utf-8")
                        await websocket.send_json({
                            "cam_id": cam["id"],
                            "cam_name": cam["name"],
                            "frame": b64,
                        })
            await asyncio.sleep(1 / 15)  # ~15 FPS per cycle
    except WebSocketDisconnect:
        logger.info("WebSocket grid disconnected")
    except Exception as e:
        logger.warning("WebSocket grid error: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
#  RUN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app:app", host=HOST, port=PORT, reload=True)
