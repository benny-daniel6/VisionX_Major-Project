# VisionX — AI-Powered Multi-Camera Video Analyzer & Surveillance System

VisionX is an intelligent, voice-enabled, multi-camera video analysis system powered by Google Gemini Multimodal LLMs and OpenCV. It transforms standard camera networks (webcams, RTSP streams, IP cameras, ESP32-CAMs) into an agentic visual reasoning grid capable of answering natural language questions about physical spaces in real time.

---

## 📑 Table of Contents
1. [Key Features](#-key-features)
2. [System Design & Architecture](#-system-design--architecture)
   - [High-Level Architecture](#high-level-architecture)
   - [End-to-End Data Flow](#end-to-end-data-flow)
   - [Core Subsystem Specifications](#core-subsystem-specifications)
3. [Project Directory Structure](#-project-directory-structure)
4. [API Reference](#-api-reference)
5. [Getting Started](#-getting-started)
   - [Prerequisites](#prerequisites)
   - [Installation](#installation)
   - [Environment Configuration](#environment-configuration)
   - [Running the Application](#running-the-application)
6. [Supported Camera Sources](#-supported-camera-sources)

---

## 🌟 Key Features

- **🎥 Multi-Camera Live Streaming**: Simultaneous capture and streaming of local webcams (`0`, `1`, etc.), RTSP video feeds, HTTP/MJPEG streams, and ESP32-CAM streams via WebSockets up to 30 FPS.
- **🧠 Agentic Camera Router**: AI routing agent automatically evaluates user queries against camera metadata (names, locations, keywords) to route questions to the single most relevant camera feed.
- **🔍 Joint Multi-Camera Intelligence**: For global queries (e.g. *"Is anyone in the building?"* or *"Count people across all feeds"*), VisionX automatically aggregates live frames from **all** active cameras into a single multimodal payload for unified reasoning.
- **👁️ Multimodal Visual QA**: Powered by Google Gemini Vision (Gemini 2.5 Flash / 2.0 Flash) to perform scene analysis, object detection/counting, activity recognition, and spatial reasoning.
- **🎙️ Hands-Free Voice Interaction**: Integrated Web Speech API for push-to-talk speech-to-text (STT) input (hold **Spacebar**) and automatic natural voice synthesis (TTS) answers.
- **💻 Modern Dynamic Web UI**: Premium glassmorphic interface with real-time dynamic grid layout, live connection status indicators, and on-the-fly camera configuration (add, update, remove, retry).
- **⚡ Thread-Safe Frame Buffering**: Dedicated background capture workers feed a non-blocking Key-Value cache (`KVCache`) to guarantee zero frame lag during AI analysis.

---

## 🏗️ System Design & Architecture

### High-Level Architecture

VisionX is architected using a decoupled **Client-Server Architecture** with asynchronous event loops, background worker threads, non-blocking frame caches, and an agentic LLM routing layer.

```mermaid
flowchart TB
    subgraph Client ["Client Browser (Frontend)"]
        UI["Glassmorphic Web UI (HTML5/CSS3)"]
        VoiceEngine["Web Speech API (STT / TTS)"]
        WSClient["WebSocket Stream Receiver"]
        RestClient["REST API Client (Fetch API)"]
    end

    subgraph Gateway ["FastAPI Web Server (Backend)"]
        AppServer["FastAPI Router / Static Server"]
        WSManager["WebSocket Connection Manager"]
    end

    subgraph Memory ["High-Performance Frame Buffer"]
        Cache["KVCache (Thread-Safe Dict + Lock)"]
    end

    subgraph CaptureEngine ["Camera Management Engine"]
        Manager["CameraManager"]
        Worker1["CameraWorker (Cam 1 / Thread)"]
        Worker2["CameraWorker (Cam 2 / Thread)"]
        WorkerN["CameraWorker (Cam N / Thread)"]
    end

    subgraph AIService ["Cognitive AI Services"]
        Classifier["Question Classifier"]
        Router["Agentic Router (Gemini / Keyword Fallback)"]
        Reasoner["Visual Reasoner (Gemini Multimodal)"]
    end

    UI <--> WSClient
    UI <--> RestClient
    UI <--> VoiceEngine

    WSClient <== "Binary / B64 JPEG Streams (30 FPS)" ==> WSManager
    RestClient <== "JSON REST Requests / Responses" ==> AppServer

    AppServer --> Router
    AppServer --> Reasoner
    AppServer --> Cache

    Manager --> Worker1
    Manager --> Worker2
    Manager --> WorkerN

    Worker1 -- "Push Latest Frame" --> Cache
    Worker2 -- "Push Latest Frame" --> Cache
    WorkerN -- "Push Latest Frame" --> Cache

    Cache -- "Retrieve Current Snapshots" --> Reasoner
    Router -- "Select Target Camera ID" --> Reasoner
    Reasoner -- "Gemini API Call" --> ExternalGemini["Google Gemini Cloud API"]
```

---

### End-to-End Data Flow

The sequence diagram below outlines the full lifecycle of a user query—from speech input to camera selection, frame retrieval, Gemini visual reasoning, and TTS response.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Frontend as Web Client (JS/Speech)
    participant FastAPI as FastAPI Backend
    participant CamMgr as Camera Manager & Cache
    participant Router as Gemini Agentic Router
    participant Reasoner as Gemini Visual Reasoner
    participant GeminiAPI as Google Gemini API

    User->>Frontend: Speaks question or types query
    Frontend->>FastAPI: POST /api/ask { question }
    FastAPI->>CamMgr: Fetch active cameras with frames
    CamMgr-->>FastAPI: Return active camera list
    
    FastAPI->>Router: classify_question(question, active_cameras)
    
    alt Single-Camera Question
        Router-->>FastAPI: Returns mode: "SINGLE"
        FastAPI->>Router: route_question(question, cameras)
        Router->>GeminiAPI: Prompt with camera metadata & question
        GeminiAPI-->>Router: Returns selected camera_id
        Router-->>FastAPI: camera_id (e.g. "cam_front_door")
        FastAPI->>CamMgr: Get latest JPEG frame for camera_id
        CamMgr-->>FastAPI: Return frame bytes
        FastAPI->>Reasoner: analyze_frame(question, frame_bytes, cam_name)
        Reasoner->>GeminiAPI: Send image payload + query prompt
        GeminiAPI-->>Reasoner: Visual analysis answer string
        Reasoner-->>FastAPI: Answer text
    else Multi-Camera Global Question
        Router-->>FastAPI: Returns mode: "ALL"
        FastAPI->>CamMgr: Get latest JPEG frames for ALL active cameras
        CamMgr-->>FastAPI: Return frames list
        FastAPI->>Reasoner: analyze_all_frames(question, all_frames)
        Reasoner->>GeminiAPI: Send combined multi-image payload
        GeminiAPI-->>Reasoner: Synthesized multi-camera answer
        Reasoner-->>FastAPI: Synthesized Answer text
    end

    FastAPI-->>Frontend: JSON { answer, camera_id, mode }
    Frontend->>User: Displays answer & trigger Text-To-Speech (TTS)
```

---

### Core Subsystem Specifications

#### 1. Camera Manager & Thread Worker Pool (`backend/camera_manager.py`)
- **Isolation**: Each camera stream runs in its own dedicated daemon thread (`CameraWorker`).
- **Resilience**: Features exponential backoff retry mechanisms (up to 5 reconnection attempts) for dropped RTSP or IP streams.
- **Persistence**: Camera configurations are persistently saved to `cameras.json` to retain stream setups across server restarts.

#### 2. Thread-Safe KV-Frame Cache (`backend/kv_cache.py`)
- **Locking**: Implements Python `threading.Lock` around frame writes and reads.
- **Zero Latency**: Eliminates stream decoding delays during AI inference by storing pre-encoded Base64 JPEG strings and raw bytes in memory.

#### 3. Agentic Routing & Visual QA Engine (`backend/gemini_service.py` & `backend/router.py`)
- **Intent Classifier**: Inspects questions to determine if target intent is single-camera or all-cameras.
- **Routing Decision**: Uses metadata matching (camera name, description, location keywords) to bind natural language queries to discrete camera IDs. Supports fallback to offline keyword matching when cloud API is unavailable.
- **Multimodal Reasoner**: Formulates visual prompts sending full-resolution camera frame JPEG buffers directly to Gemini 2.5 Flash / 2.0 Flash APIs.

---

## 📁 Project Directory Structure

```
Video Analyzer/X/
├── backend/
│   ├── __init__.py          # Backend package initializer
│   ├── app.py               # Main FastAPI server & REST/WebSocket routes
│   ├── camera_manager.py    # OpenCV multi-threaded camera stream manager
│   ├── config.py            # System configuration & env loading
│   ├── gemini_service.py    # Gemini API wrapper (Router & Visual Reasoner)
│   ├── kv_cache.py          # Thread-safe in-memory frame cache
│   ├── router.py            # Offline keyword routing fallback module
│   └── requirements.txt     # Python dependencies
├── frontend/
│   ├── index.html           # Main UI structure & layout
│   ├── styles.css           # Glassmorphic CSS styling & animations
│   └── app.js               # Frontend controller, WebSockets & Speech API
├── .env                     # API key & environment settings (local)
├── .env.example             # Template for environment configuration
├── cameras.json             # Saved camera configuration state
└── README.md                # Project documentation & architecture guide
```

---

## 📡 API Reference

### REST API Endpoints

| Method | Endpoint | Description | Payload / Parameters |
|---|---|---|---|
| `GET` | `/` | Serves main Web Interface | — |
| `GET` | `/api/cameras` | Retrieves metadata of all registered cameras | — |
| `POST` | `/api/cameras` | Adds a new camera feed | `{ "name": "Desk", "source": "0", "keywords": ["computer", "desk"] }` |
| `PATCH` | `/api/cameras/{id}` | Updates existing camera details | `{ "name": "New Name", "keywords": [...] }` |
| `DELETE` | `/api/cameras/{id}` | Removes a camera and stops its stream | — |
| `POST` | `/api/cameras/{id}/retry` | Forces reconnection to a camera | — |
| `POST` | `/api/ask` | Submits natural language question to AI | `{ "question": "Is anyone sitting at the desk?", "camera_id": null }` |

### WebSocket Endpoints

| Endpoint | Direction | Description | Format |
|---|---|---|---|
| `/ws/stream/{cam_id}` | Server ➔ Client | Pushes continuous live video frames for target camera | Binary JPEG / Text Base64 Frame |

---

## 🚀 Getting Started

### Prerequisites

- **Python**: Version `3.10` or higher
- **Web Browser**: Google Chrome or Microsoft Edge (recommended for Web Speech API support)
- **Gemini API Key**: Free API key from [Google AI Studio](https://aistudio.google.com/apikey)

---

### Installation

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/benny-daniel6/Major-Project.git
   cd Major-Project
   ```

2. **Create and Activate Virtual Environment** (Optional but Recommended):
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On Linux/macOS:
   source venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r backend/requirements.txt
   ```

---

### Environment Configuration

Create a `.env` file in the root directory (or copy from `.env.example`):

```env
# Google Gemini API Key (Required)
GOOGLE_API_KEY=your_actual_gemini_api_key_here

# Selected Gemini Model (Optional, defaults to gemini-2.5-flash-lite)
GEMINI_MODEL=gemini-2.5-flash-lite
```

---

### Running the Application

Launch the FastAPI application server using Uvicorn:

```bash
python -m uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000
```

Once started, access the web UI at:
👉 **[http://localhost:8000](http://localhost:8000)**

---

## 📷 Supported Camera Sources

| Camera Type | Source Value Format | Example |
|---|---|---|
| **Local Webcam** | Integer index string | `0`, `1`, `2` |
| **IP Camera (MJPEG)** | HTTP / HTTPS Stream URL | `http://192.168.1.50:8080/video` |
| **RTSP Camera Stream** | RTSP protocol URL | `rtsp://admin:pass@192.168.1.100:554/stream1` |
| **ESP32-CAM Stream** | ESP32 Stream URL | `http://192.168.1.80:81/stream` |
| **Mobile IP Webcam** | Mobile app video stream | `http://192.168.1.15:8080/video` |

---

## 🛡️ License

Distributed under the MIT License. See `LICENSE` for details.
