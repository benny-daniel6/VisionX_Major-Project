/**
 * VisionX — Client-side Application Logic
 *
 * Handles:
 *  - WebSocket connections for live camera streaming
 *  - Dynamic camera grid management
 *  - AI query submission (text + voice)
 *  - Web Speech API (STT push-to-talk + TTS response)
 *  - UI state management and animations
 */

// ══════════════════════════════════════════════════════════════════════════════
//  CONSTANTS & STATE
// ══════════════════════════════════════════════════════════════════════════════

const API_BASE = window.location.origin;
const WS_BASE = `ws://${window.location.host}`;

const state = {
    cameras: [],
    wsConnections: {},      // cam_id → WebSocket
    isListening: false,
    isQuerying: false,
};

// ── DOM Refs ──────────────────────────────────────────────────────────────

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const dom = {
    grid:             $('#camera-grid'),
    cameraCount:      $('#camera-count'),
    emptyCameras:     $('#empty-cameras'),
    queryInput:       $('#query-input'),
    btnSend:          $('#btn-send'),
    btnMic:           $('#btn-mic'),
    voiceFeedback:    $('#voice-feedback'),
    responsePlaceholder: $('#response-placeholder'),
    responseContent:  $('#response-content'),
    responseLoading:  $('#response-loading'),
    responseText:     $('#response-text'),
    routedCameraName: $('#routed-camera-name'),
    analyzedFrame:    $('#analyzed-frame'),
    analyzedFrameContainer: $('#analyzed-frame-container'),
    statusDot:        $('#status-dot'),
    statusText:       $('#status-text'),
    btnAddCamera:     $('#btn-add-camera'),
    modalOverlay:     $('#modal-overlay'),
    modalClose:       $('#modal-close'),
    addCameraForm:    $('#add-camera-form'),
    btnStopSpeech:    $('#btn-stop-speech'),
};

// ══════════════════════════════════════════════════════════════════════════════
//  INITIALIZATION
// ══════════════════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    initEventListeners();
    initSpeechRecognition();
    initResizeHandle();
    loadCameras();
    setInterval(loadCameras, 5000); // Poll for camera updates
});

function initEventListeners() {
    // Send query
    dom.btnSend.addEventListener('click', () => submitQuery());
    dom.queryInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            submitQuery();
        }
    });

    // Mic button
    dom.btnMic.addEventListener('mousedown', startListening);
    dom.btnMic.addEventListener('mouseup', stopListening);
    dom.btnMic.addEventListener('mouseleave', stopListening);
    dom.btnMic.addEventListener('touchstart', (e) => { e.preventDefault(); startListening(); });
    dom.btnMic.addEventListener('touchend', (e) => { e.preventDefault(); stopListening(); });

    // Spacebar push-to-talk
    document.addEventListener('keydown', (e) => {
        if (e.code === 'Space' && document.activeElement !== dom.queryInput && !state.isListening) {
            e.preventDefault();
            startListening();
        }
    });
    document.addEventListener('keyup', (e) => {
        if (e.code === 'Space' && state.isListening) {
            e.preventDefault();
            stopListening();
        }
    });

    // Add camera modal
    dom.btnAddCamera.addEventListener('click', openModal);
    dom.modalClose.addEventListener('click', closeModal);
    dom.modalOverlay.addEventListener('click', (e) => {
        if (e.target === dom.modalOverlay) closeModal();
    });
    dom.addCameraForm.addEventListener('submit', handleAddCamera);

    // ESC to close modal and stop speech
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeModal();
            stopSpeaking();
        }
    });

    // Stop speech button
    dom.btnStopSpeech.addEventListener('click', stopSpeaking);
}

// ══════════════════════════════════════════════════════════════════════════════
//  CAMERA MANAGEMENT
// ══════════════════════════════════════════════════════════════════════════════

async function loadCameras() {
    try {
        const res = await fetch(`${API_BASE}/api/cameras`);
        const data = await res.json();
        const newCameras = data.cameras || [];

        // Detect changes
        const oldIds = new Set(state.cameras.map(c => c.id));
        const newIds = new Set(newCameras.map(c => c.id));

        // Add new cameras
        for (const cam of newCameras) {
            if (!oldIds.has(cam.id)) {
                addCameraTile(cam);
                connectCameraWS(cam.id);
            }
        }

        // Remove deleted cameras
        for (const cam of state.cameras) {
            if (!newIds.has(cam.id)) {
                removeCameraTile(cam.id);
                disconnectCameraWS(cam.id);
            }
        }

        // Update error/status on existing tiles
        for (const cam of newCameras) {
            updateCameraTileStatus(cam);
        }

        state.cameras = newCameras;
        updateCameraCount();
        updateStatus();
    } catch (err) {
        console.error('Failed to load cameras:', err);
        setStatus('error', 'Connection lost');
    }
}

function updateCameraTileStatus(cam) {
    const tile = $(`#cam-tile-${cam.id}`);
    if (!tile) return;

    const statusEl = tile.querySelector('.camera-tile-status');
    const noSignal = $(`#no-signal-${cam.id}`);

    if (statusEl) {
        const dot = statusEl.querySelector('.dot');
        const label = statusEl.querySelector('span:last-child');
        if (cam.error) {
            dot.className = 'dot error';
            label.textContent = 'Connection Error';  // Short text for the tiny corner pill
        } else if (cam.has_frame) {
            dot.className = 'dot';
            label.textContent = 'Live';
        } else {
            dot.className = 'dot';
            label.textContent = 'Waiting for frames...';
        }
    }

    // Update the no-signal overlay text with more detail
    if (noSignal) {
        const errSpan = noSignal.querySelector('span');

        // Remove old retry button if it exists
        const oldBtn = noSignal.querySelector('.retry-btn');
        if (oldBtn) oldBtn.remove();

        if (cam.error) {
            // If there's an error, force the error overlay, even if we had a past frame
            noSignal.style.display = 'flex';
            if (errSpan) errSpan.textContent = cam.error;  // Full long text in the center
            noSignal.classList.add('error-state');

            // Inject retry button
            const btn = document.createElement('button');
            btn.className = 'btn retry-btn';
            btn.style.marginTop = '0.75rem';
            btn.style.padding = '0.4rem 0.8rem';
            btn.style.fontSize = '0.75rem';
            btn.style.pointerEvents = 'auto'; // ensure clickable
            btn.textContent = 'Retry Connection';
            btn.onclick = (e) => {
                e.stopPropagation();
                window.retryCamera(cam.id);
            };
            noSignal.appendChild(btn);
        } else if (!cam.has_frame) {
            // Still connecting, no frame yet
            noSignal.style.display = 'flex';
            if (errSpan) errSpan.textContent = 'Connecting...';
            noSignal.classList.remove('error-state');
        } else {
            // Has frame, no error
            noSignal.style.display = 'none';
            if (errSpan) errSpan.textContent = 'Connecting...';
            noSignal.classList.remove('error-state');
        }
    }
}

// Ensure retryCamera is available globally
window.retryCamera = async function(camId) {
    const tile = document.querySelector(`#cam-tile-${camId}`);
    if (tile) {
        const btn = tile.querySelector('.retry-btn');
        if (btn) btn.textContent = 'Retrying...';
        const errSpan = tile.querySelector('.no-signal span');
        if (errSpan) errSpan.textContent = 'Reconnecting...';
        const noSignal = tile.querySelector('.no-signal');
        if (noSignal) noSignal.classList.remove('error-state');
    }

    try {
        await fetch(`${API_BASE}/api/cameras/${camId}/retry`, { method: 'POST' });
        // The periodic status poll will pick up the 'Connecting...' status shortly
    } catch (err) {
        console.error('Failed to retry camera:', err);
    }
}

function addCameraTile(cam) {
    // Hide empty state
    dom.emptyCameras.style.display = 'none';

    const tile = document.createElement('div');
    tile.className = 'camera-tile';
    tile.id = `cam-tile-${cam.id}`;
    tile.dataset.camId = cam.id;

    tile.innerHTML = `
        <canvas id="cam-canvas-${cam.id}" width="640" height="480"></canvas>
        <div class="camera-tile-overlay">
            <div>
                <div class="camera-tile-name">${escapeHtml(cam.name)}</div>
                <div class="camera-tile-status">
                    <span class="dot ${cam.error ? 'error' : ''}"></span>
                    <span>${cam.error || 'Live'}</span>
                </div>
            </div>
        </div>
        <div class="camera-tile-actions">
            <button class="btn btn-edit-sm" title="Edit camera" onclick="editCamera('${cam.id}')">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                </svg>
            </button>
            <button class="btn btn-danger-sm" title="Remove camera" onclick="removeCamera('${cam.id}')">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="18" y1="6" x2="6" y2="18"/>
                    <line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
            </button>
        </div>
        <div class="no-signal" id="no-signal-${cam.id}" style="position:absolute;inset:0;">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/>
                <circle cx="12" cy="13" r="4"/>
            </svg>
            <span>Connecting...</span>
        </div>
    `;

    dom.grid.appendChild(tile);
}

function removeCameraTile(camId) {
    const tile = $(`#cam-tile-${camId}`);
    if (tile) {
        tile.style.animation = 'tile-in 0.3s var(--ease-out) reverse both';
        setTimeout(() => tile.remove(), 300);
    }
    // Show empty state if no cameras left
    setTimeout(() => {
        if (state.cameras.length <= 1) {  // About to remove the last one
            dom.emptyCameras.style.display = '';
        }
    }, 350);
}

function highlightCamera(camId) {
    // Remove previous highlights
    $$('.camera-tile.highlighted').forEach(el => el.classList.remove('highlighted'));
    const tile = $(`#cam-tile-${camId}`);
    if (tile) {
        tile.classList.add('highlighted');
        tile.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}

function updateCameraCount() {
    const active = state.cameras.filter(c => c.active).length;
    dom.cameraCount.textContent = `${active} camera${active !== 1 ? 's' : ''}`;
}

// ── Camera WebSocket ─────────────────────────────────────────────────────

function connectCameraWS(camId) {
    if (state.wsConnections[camId]) return;

    const ws = new WebSocket(`${WS_BASE}/ws/stream/${camId}`);
    ws.binaryType = 'blob';

    ws.onopen = () => {
        console.log(`[WS] Connected to camera ${camId}`);
    };

    ws.onmessage = (event) => {
        const canvas = $(`#cam-canvas-${camId}`);
        const noSignal = $(`#no-signal-${camId}`);
        if (!canvas) return;

        const cam = state.cameras.find(c => c.id === camId);
        if (noSignal && (!cam || !cam.error)) {
            noSignal.style.display = 'none';
        }

        const url = URL.createObjectURL(event.data);
        const img = new Image();
        img.onload = () => {
            const ctx = canvas.getContext('2d');
            canvas.width = img.width;
            canvas.height = img.height;
            ctx.drawImage(img, 0, 0);
            URL.revokeObjectURL(url);
        };
        img.src = url;
    };

    ws.onclose = () => {
        console.log(`[WS] Disconnected from camera ${camId}`);
        delete state.wsConnections[camId];
        // Reconnect after delay
        setTimeout(() => {
            if (state.cameras.some(c => c.id === camId)) {
                connectCameraWS(camId);
            }
        }, 2000);
    };

    ws.onerror = (err) => {
        console.error(`[WS] Error for camera ${camId}:`, err);
    };

    state.wsConnections[camId] = ws;
}

function disconnectCameraWS(camId) {
    const ws = state.wsConnections[camId];
    if (ws) {
        ws.close();
        delete state.wsConnections[camId];
    }
}

// ── Add / Edit / Remove Camera API ───────────────────────────────────────

async function handleAddCamera(e) {
    e.preventDefault();
    const editId = $('#cam-edit-id').value;
    const name = $('#cam-name').value.trim();
    const source = $('#cam-source').value.trim();
    const keywordsRaw = $('#cam-keywords').value.trim();
    const keywords = keywordsRaw ? keywordsRaw.split(',').map(k => k.trim()).filter(Boolean) : [];

    if (!name || !source) return;

    try {
        let res;
        if (editId) {
            // Edit existing camera
            res = await fetch(`${API_BASE}/api/cameras/${editId}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, source, keywords }),
            });
        } else {
            // Add new camera
            res = await fetch(`${API_BASE}/api/cameras`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, source, keywords }),
            });
        }
        if (res.ok) {
            closeModal();
            dom.addCameraForm.reset();
            loadCameras();
        }
    } catch (err) {
        console.error('Failed to save camera:', err);
    }
}

function editCamera(camId) {
    const cam = state.cameras.find(c => c.id === camId);
    if (!cam) return;

    // Pre-fill the modal with current values
    $('#cam-edit-id').value = cam.id;
    $('#cam-name').value = cam.name;
    $('#cam-source').value = cam.source;
    $('#cam-keywords').value = (cam.keywords || []).join(', ');

    // Switch modal to edit mode
    $('#modal-title').textContent = 'Edit Camera';
    $('#modal-submit-text').textContent = 'Save Changes';

    dom.modalOverlay.classList.add('active');
    setTimeout(() => $('#cam-name').focus(), 100);
}
window.editCamera = editCamera;

async function removeCamera(camId) {
    try {
        await fetch(`${API_BASE}/api/cameras/${camId}`, { method: 'DELETE' });
        loadCameras();
    } catch (err) {
        console.error('Failed to remove camera:', err);
    }
}
// Expose to onclick handler in HTML
window.removeCamera = removeCamera;

// ══════════════════════════════════════════════════════════════════════════════
//  AI QUERY
// ══════════════════════════════════════════════════════════════════════════════

async function submitQuery(text) {
    const question = text || dom.queryInput.value.trim();
    if (!question || state.isQuerying) return;

    state.isQuerying = true;
    dom.queryInput.value = question;
    showLoading();

    try {
        const res = await fetch(`${API_BASE}/api/ask`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question }),
        });

        const data = await res.json();
        showResponse(data);

        // Highlight the selected camera
        if (data.camera_id) {
            highlightCamera(data.camera_id);
        }

        // Speak the response
        speakText(data.answer);
    } catch (err) {
        console.error('Query failed:', err);
        showResponse({
            answer: 'Sorry, I could not process your request. Please check the connection.',
            camera_name: null,
        });
    } finally {
        state.isQuerying = false;
        dom.queryInput.value = '';
    }
}

function showLoading() {
    dom.responsePlaceholder.style.display = 'none';
    dom.responseContent.style.display = 'none';
    dom.responseLoading.style.display = 'flex';
}

function showResponse(data) {
    dom.responseLoading.style.display = 'none';
    dom.responsePlaceholder.style.display = 'none';
    dom.responseContent.style.display = 'flex';

    // Show mode-aware camera label
    if (data.mode === 'all' && data.cameras_analyzed) {
        dom.routedCameraName.textContent = `📡 All (${data.cameras_analyzed.length}): ${data.cameras_analyzed.join(', ')}`;
    } else {
        dom.routedCameraName.textContent = data.camera_name || 'Unknown';
    }

    // Show analyzed frame if available
    if (data.analyzed_frame) {
        dom.analyzedFrame.src = `data:image/jpeg;base64,${data.analyzed_frame}`;
        dom.analyzedFrameContainer.style.display = 'block';
    } else {
        dom.analyzedFrameContainer.style.display = 'none';
    }

    // Strip markdown before typing so we don't display raw asterisks/hashes
    const cleanAnswer = (data.answer || '')
        .replace(/[*#_`~]/g, '')
        .replace(/^[-•]\s+/gm, '')
        .trim();

    // Typing animation
    typeText(dom.responseText, cleanAnswer);
}

function typeText(element, text, speed = 18) {
    element.innerHTML = '';
    let i = 0;
    const cursor = document.createElement('span');
    cursor.className = 'typing-cursor';

    function type() {
        if (i < text.length) {
            element.textContent += text[i];
            element.appendChild(cursor);
            i++;
            setTimeout(type, speed);
        } else {
            // Remove cursor after a delay
            setTimeout(() => cursor.remove(), 1500);
        }
    }
    type();
}

// ══════════════════════════════════════════════════════════════════════════════
//  SPEECH RECOGNITION (STT) + SYNTHESIS (TTS)
// ══════════════════════════════════════════════════════════════════════════════

let recognition = null;

function initSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        console.warn('SpeechRecognition not supported in this browser');
        dom.btnMic.style.display = 'none';
        return;
    }

    recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = 'en-US';

    recognition.onresult = (event) => {
        let transcript = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
            transcript += event.results[i][0].transcript;
        }
        dom.queryInput.value = transcript;

        // If final result, auto-submit
        if (event.results[event.results.length - 1].isFinal) {
            stopListening();
            setTimeout(() => submitQuery(transcript), 300);
        }
    };

    recognition.onerror = (event) => {
        console.error('Speech recognition error:', event.error);
        stopListening();
    };

    recognition.onend = () => {
        stopListening();
    };
}

function startListening() {
    if (!recognition || state.isListening) return;
    state.isListening = true;
    dom.btnMic.classList.add('listening');
    dom.voiceFeedback.classList.add('active');

    try {
        recognition.start();
    } catch (e) {
        // Already started
    }
}

function stopListening() {
    if (!state.isListening) return;
    state.isListening = false;
    dom.btnMic.classList.remove('listening');
    dom.voiceFeedback.classList.remove('active');

    try {
        recognition.stop();
    } catch (e) {
        // Already stopped
    }
}

// ── Voice Cache ──────────────────────────────────────────────────────────
let _cachedVoice = null;
let _voicesLoaded = false;

/**
 * Pick the best American/British English voice available.
 * Priority order:
 *   1. Google US English
 *   2. Microsoft US voices (David, Mark, Zira)
 *   3. Any en-US voice
 *   4. Any en-GB voice
 *   5. First English voice found
 */
function getBestVoice() {
    if (_cachedVoice && _voicesLoaded) return _cachedVoice;

    const voices = speechSynthesis.getVoices();
    if (!voices.length) return null;

    _voicesLoaded = true;

    // Ranked preference matchers (first match wins)
    const rankedPrefs = [
        (v) => v.name === 'Google US English',
        (v) => v.name.includes('Google') && v.lang === 'en-US',
        (v) => v.name.includes('Microsoft David') && v.lang === 'en-US',
        (v) => v.name.includes('Microsoft Mark') && v.lang === 'en-US',
        (v) => v.name.includes('Microsoft Zira') && v.lang === 'en-US',
        (v) => v.lang === 'en-US' && !v.name.includes('India'),
        (v) => v.lang === 'en-GB',
        (v) => v.lang.startsWith('en') && !v.name.includes('India') && !v.lang.includes('IN'),
    ];

    for (const matcher of rankedPrefs) {
        const found = voices.find(matcher);
        if (found) {
            _cachedVoice = found;
            console.log(`[TTS] Selected voice: "${found.name}" (${found.lang})`);
            return found;
        }
    }

    // Absolute fallback: first voice
    _cachedVoice = voices[0];
    console.log(`[TTS] Fallback voice: "${voices[0].name}" (${voices[0].lang})`);
    return voices[0];
}

function speakText(text) {
    if (!window.speechSynthesis || !text) return;

    // Cancel any ongoing speech
    speechSynthesis.cancel();

    // Strip markdown formatting so TTS doesn't read asterisks, hashes, etc.
    const cleanText = text
        .replace(/[*#_`~]/g, '')           // aggressive strip of markdown chars
        .replace(/^[-•]\s+/gm, '')         // bullet points
        .replace(/\n{2,}/g, '. ')          // paragraph breaks → pause
        .replace(/\n/g, ' ')               // remaining newlines
        .trim();

    const utterance = new SpeechSynthesisUtterance(cleanText);
    utterance.rate = 1.0;
    utterance.pitch = 1.0;
    utterance.lang = 'en-US';

    const voice = getBestVoice();
    if (voice) utterance.voice = voice;

    // Show stop button while speaking
    dom.btnStopSpeech.style.display = 'inline-flex';
    utterance.onend = () => { dom.btnStopSpeech.style.display = 'none'; };
    utterance.onerror = () => { dom.btnStopSpeech.style.display = 'none'; };

    speechSynthesis.speak(utterance);
}

function stopSpeaking() {
    if (window.speechSynthesis) {
        speechSynthesis.cancel();
    }
    dom.btnStopSpeech.style.display = 'none';
}

// Pre-load voices (Chrome loads them async)
if (window.speechSynthesis) {
    speechSynthesis.getVoices(); // Trigger initial load
    speechSynthesis.onvoiceschanged = () => {
        _cachedVoice = null;
        _voicesLoaded = false;
        getBestVoice(); // Re-select on voice list change
    };
}

// ══════════════════════════════════════════════════════════════════════════════
//  RESIZE HANDLE
// ══════════════════════════════════════════════════════════════════════════════

function initResizeHandle() {
    const handle = $('#resize-handle');
    const main = $('#app-main');
    if (!handle || !main) return;

    let isDragging = false;
    let startX = 0;
    let startWidth = 0;

    handle.addEventListener('mousedown', (e) => {
        e.preventDefault();
        isDragging = true;
        startX = e.clientX;
        const camSection = $('#camera-section');
        startWidth = camSection.getBoundingClientRect().width;
        handle.classList.add('dragging');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    });

    document.addEventListener('mousemove', (e) => {
        if (!isDragging) return;
        const dx = e.clientX - startX;
        const newWidth = Math.max(300, Math.min(startWidth + dx, window.innerWidth - 380));
        main.style.setProperty('--cam-col-width', `${newWidth}px`);
    });

    document.addEventListener('mouseup', () => {
        if (!isDragging) return;
        isDragging = false;
        handle.classList.remove('dragging');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
    });
}

// ══════════════════════════════════════════════════════════════════════════════
//  MODAL
// ══════════════════════════════════════════════════════════════════════════════

function openModal() {
    // Reset to Add mode
    $('#cam-edit-id').value = '';
    dom.addCameraForm.reset();
    $('#modal-title').textContent = 'Add Camera Source';
    $('#modal-submit-text').textContent = 'Add Camera';

    dom.modalOverlay.classList.add('active');
    setTimeout(() => $('#cam-name').focus(), 100);
}

function closeModal() {
    dom.modalOverlay.classList.remove('active');
    // Reset edit state
    $('#cam-edit-id').value = '';
    $('#modal-title').textContent = 'Add Camera Source';
    $('#modal-submit-text').textContent = 'Add Camera';
}

// ══════════════════════════════════════════════════════════════════════════════
//  STATUS & UTILITIES
// ══════════════════════════════════════════════════════════════════════════════

function updateStatus() {
    const active = state.cameras.filter(c => c.active && c.has_frame).length;
    const total = state.cameras.length;

    if (total === 0) {
        setStatus('error', 'No cameras');
    } else if (active === total) {
        setStatus('online', `${active} camera${active !== 1 ? 's' : ''} online`);
    } else {
        setStatus('warning', `${active}/${total} cameras active`);
    }
}

function setStatus(type, text) {
    dom.statusDot.className = 'status-indicator ' + (type === 'warning' ? '' : type);
    dom.statusText.textContent = text;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
