# VisionX — AI Multi-Camera Video Analyzer

An intelligent, voice-enabled, multi-camera video analysis system powered by Google Gemini.

## Features

- **Multi-Camera Streaming** — Connect local webcams and IP cameras (RTSP/MJPEG)
- **Agentic Camera Routing** — AI automatically selects the right camera based on your question
- **Visual Question Answering** — Ask natural language questions about what any camera sees
- **Voice Interaction** — Push-to-talk (hold Space or mic button) with spoken AI responses
- **Dynamic Camera Grid** — Add/remove cameras on the fly via the web UI

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (Frontend)                                         │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Camera   │  │ Query Panel  │  │ Web Speech API       │  │
│  │ Grid     │  │ (Text/Voice) │  │ (STT + TTS)          │  │
│  └────┬─────┘  └──────┬───────┘  └──────────────────────┘  │
│       │ WebSocket      │ REST POST                          │
└───────┼────────────────┼────────────────────────────────────┘
        │                │
┌───────┼────────────────┼────────────────────────────────────┐
│  FastAPI Server        │                                    │
│       │         ┌──────┴───────┐                            │
│  ┌────┴─────┐   │ Agentic      │   ┌───────────────────┐   │
│  │ Camera   │   │ Router       │──▶│ Visual Reasoner   │   │
│  │ Manager  │   │ (Gemini LLM) │   │ (Gemini Vision)   │   │
│  │ (OpenCV) │   └──────────────┘   └───────────────────┘   │
│  └────┬─────┘                                               │
│       │          ┌───────────────┐                           │
│       └─────────▶│ KV Cache      │◀── Frames @ 30 FPS      │
│                  │ (Thread-safe) │                           │
│                  └───────────────┘                           │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Clone & Install

```bash
cd "Video Analyzer/X"
pip install -r backend/requirements.txt
```

### 2. Set API Key

Create a `.env` file in the project root:

```env
GOOGLE_API_KEY=your-gemini-api-key-here
```

Get a free API key at [ai.google.dev](https://ai.google.dev/).

### 3. Run

```bash
python -m uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** in Chrome/Edge.

## Usage

| Action | How |
|---|---|
| **Ask a question** | Type in the query box and press Enter or click Send |
| **Voice input** | Hold **Spacebar** or the mic button to speak |
| **Add a camera** | Click the **+** button in the header |
| **Remove a camera** | Hover over a camera tile and click the **×** |

### Example Questions

- *"Is anyone at the desk right now?"*
- *"What objects can you see?"*
- *"Is the door open or closed?"*
- *"How many people are in the room?"*

## Camera Sources

| Type | Source Format |
|---|---|
| Local webcam | `0`, `1`, `2`... |
| IP Camera (MJPEG) | `http://192.168.1.10:8080/video` |
| RTSP stream | `rtsp://user:pass@192.168.1.10:554/stream` |
| ESP32-CAM | `http://192.168.1.10:81/stream` |
| Phone (IP Webcam app) | `http://192.168.1.10:8080/video` |

## Tech Stack

- **Backend**: Python 3, FastAPI, Uvicorn, OpenCV
- **AI**: Google Gemini 2.0 Flash (multimodal)
- **Frontend**: HTML5, CSS3, Vanilla JS, Web Speech API
- **Streaming**: WebSockets (binary JPEG frames)
