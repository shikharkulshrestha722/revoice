// ============================================================
// ReVoice — Frontend Logic
// ============================================================
// 1. Turn on the webcam (mirrored, like a selfie camera)
// 2. Track a pointer: your index fingertip (Finger mode) or a laser dot
// 3. Smooth the pointer and select letters by holding still (dwell)
// 4. Record voice bank clips and speak via the backend -> Colab
// ============================================================

import { PointerTracker, DwellSelector, detectLaserBlob } from "./tracking.js";

// MediaPipe is loaded later, only when Finger mode needs it.
// That way, if it fails to download, the camera still works.
const MEDIAPIPE_URL = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";

// ---------- SETTINGS YOU CAN TUNE ----------
const SETTINGS = {
  dwellMs: 1500,        // how long to hold on a letter to select it
  repeatExtraMs: 800,   // extra wait before the SAME letter types again
  switchFrames: 4,      // frames needed in a new cell before switching (stops border flicker)
  smoothing: 0.8,       // lower = steadier pointer, higher = snappier (0.3 to 2.0)
  responsiveness: 0.015,// higher = less lag when moving fast
  scanWidth: 160,       // laser mode scans a small copy of the frame (faster + less noise)
  scanHeight: 120,
  suggestionBarHeight: 84,  // height of the prediction bar at the top of the camera view
  suggestionCount: 3,
};

// ---------- ELEMENTS ----------
const video = document.getElementById("webcam");
const overlay = document.getElementById("overlay");
const ctx = overlay.getContext("2d");
const composedTextEl = document.getElementById("composedText");
const trackModeEl = document.getElementById("trackMode");
const trackStatusEl = document.getElementById("trackStatus");

const scanCanvas = document.createElement("canvas");
scanCanvas.width = SETTINGS.scanWidth;
scanCanvas.height = SETTINGS.scanHeight;
const scanCtx = scanCanvas.getContext("2d", { willReadFrequently: true });

// ---------- GRID ----------
const GRID_ROWS = 5;
const GRID_COLS = 6;
const GRID_LETTERS = [
  "A","B","C","D","E","F",
  "G","H","I","J","K","L",
  "M","N","O","P","Q","R",
  "S","T","U","V","W","X",
  "Y","Z","SPACE","DEL","_","_"
];

// ---------- STATE ----------
let composedText = "";
let handLandmarker = null;
let handLoading = false;
let lastVideoTime = -1;
let lastFingerPoint = null;
let running = false;
let suggestions = [];          // current predicted phrases
let suggestionSource = "";     // "gemini" or "offline"
let predictTimer = null;
let predictController = null;  // lets us cancel an in-flight request

const tracker = new PointerTracker({
  minCutoff: SETTINGS.smoothing,
  beta: SETTINGS.responsiveness,
  maxLostFrames: 10,
});
const dwell = new DwellSelector({
  dwellMs: SETTINGS.dwellMs,
  switchFrames: SETTINGS.switchFrames,
  repeatExtraMs: SETTINGS.repeatExtraMs,
});

// ---------- FINGER TRACKER (MediaPipe) ----------
async function loadHandTracker() {
  if (handLandmarker || handLoading) return;
  handLoading = true;
  trackStatusEl.textContent = "Loading finger tracker...";
  try {
    const { HandLandmarker, FilesetResolver } = await import(MEDIAPIPE_URL);
    const vision = await FilesetResolver.forVisionTasks(
      "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm"
    );
    const modelPath =
      "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";
    try {
      handLandmarker = await HandLandmarker.createFromOptions(vision, {
        baseOptions: { modelAssetPath: modelPath, delegate: "GPU" },
        runningMode: "VIDEO",
        numHands: 1,
      });
    } catch {
      handLandmarker = await HandLandmarker.createFromOptions(vision, {
        baseOptions: { modelAssetPath: modelPath, delegate: "CPU" },
        runningMode: "VIDEO",
        numHands: 1,
      });
    }
    trackStatusEl.textContent = "Finger tracker ready — point with your index finger.";
  } catch (err) {
    console.error(err);
    trackStatusEl.textContent = "Could not load finger tracker (check internet). Try Laser mode.";
  } finally {
    handLoading = false;
  }
}

trackModeEl.onchange = () => {
  tracker.reset();
  dwell.reset();
  lastFingerPoint = null;
  if (trackModeEl.value === "finger") loadHandTracker();
  else trackStatusEl.textContent = "Laser mode — aim a red laser dot at the grid.";
};

// ---------- CAMERA ----------
document.getElementById("startCamBtn").onclick = async () => {
  // Camera only works on http://localhost or https — not when opening the file directly
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    trackStatusEl.textContent =
      "Camera blocked: open the app at http://localhost:8000 (not by double-clicking index.html).";
    return;
  }

  trackStatusEl.textContent = "Requesting camera... click Allow if your browser asks.";

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 640 }, height: { ideal: 480 } },
      audio: false,
    });
  } catch (err) {
    console.error(err);
    const messages = {
      NotAllowedError: "Camera permission denied. Click the camera icon in the address bar, allow it, then refresh.",
      NotFoundError: "No camera found. Plug in a webcam and try again.",
      NotReadableError: "Camera is being used by another app (Zoom, Teams, Camera app). Close it and try again.",
      OverconstrainedError: "Camera doesn't support the requested size.",
    };
    trackStatusEl.textContent = messages[err.name] || `Camera error: ${err.name} — ${err.message}`;
    return;
  }

  video.srcObject = stream;
  try {
    await video.play();
  } catch (err) {
    console.error(err);
  }

  // wait until the camera reports its real size
  if (!video.videoWidth) {
    await new Promise((resolve) => video.addEventListener("loadedmetadata", resolve, { once: true }));
  }

  overlay.width = video.videoWidth;
  overlay.height = video.videoHeight;

  if (trackModeEl.value === "finger") loadHandTracker();
  else trackStatusEl.textContent = "Laser mode — aim a red laser dot at the grid.";

  if (!running) {
    running = true;
    requestAnimationFrame(loop);
  }
};

// Tell the page this script loaded (used by the check in index.html)
window.__revoiceLoaded = true;
trackStatusEl.textContent = "Ready. Click Start Camera.";
refreshSuggestions();        // show starter phrases straight away
updateVoiceBankStatus();     // show how many clips are banked

// ---------- MAIN LOOP ----------
function loop() {
  if (video.readyState >= 2) {
    const W = overlay.width, H = overlay.height;
    const now = performance.now();

    // draw the camera mirrored so moving right moves the pointer right
    ctx.save();
    ctx.translate(W, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(video, 0, 0, W, H);
    ctx.restore();

    const raw = getRawPoint(now);
    const pointer = tracker.update(raw, now);
    const cellUnder = pointer ? cellAt(pointer.x, pointer.y) : -1;
    const { cell, progress, selected } = dwell.update(cellUnder, now);

    if (selected !== null) selectCell(selected);

    drawGrid(cell);
    drawSuggestionBar(cell);
    if (pointer) drawPointer(pointer, progress);
  }
  requestAnimationFrame(loop);
}

function getRawPoint(now) {
  const W = overlay.width, H = overlay.height;

  if (trackModeEl.value === "finger") {
    if (!handLandmarker) return null;
    // only run the model when the camera has a NEW frame
    if (video.currentTime === lastVideoTime) return lastFingerPoint;
    lastVideoTime = video.currentTime;

    const result = handLandmarker.detectForVideo(video, now);
    if (result.landmarks && result.landmarks.length > 0) {
      const tip = result.landmarks[0][8];           // landmark 8 = index fingertip
      lastFingerPoint = { x: (1 - tip.x) * W, y: tip.y * H };  // mirrored
    } else {
      lastFingerPoint = null;
    }
    return lastFingerPoint;
  }

  // laser mode: scan a small mirrored copy of the frame
  const SW = SETTINGS.scanWidth, SH = SETTINGS.scanHeight;
  scanCtx.save();
  scanCtx.translate(SW, 0);
  scanCtx.scale(-1, 1);
  scanCtx.drawImage(video, 0, 0, SW, SH);
  scanCtx.restore();
  const img = scanCtx.getImageData(0, 0, SW, SH);
  const blob = detectLaserBlob(img.data, SW, SH);
  if (!blob) return null;
  return { x: blob.x * (W / SW), y: blob.y * (H / SH) };
}

// ---------- DRAWING ----------
function drawGrid(activeCell) {
  const barH = SETTINGS.suggestionBarHeight;
  const cellW = overlay.width / GRID_COLS;
  const cellH = (overlay.height - barH) / GRID_ROWS;

  ctx.font = "bold 18px Arial";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  for (let row = 0; row < GRID_ROWS; row++) {
    for (let col = 0; col < GRID_COLS; col++) {
      const idx = row * GRID_COLS + col;
      const x = col * cellW, y = barH + row * cellH;
      const label = GRID_LETTERS[idx];

      if (idx === activeCell && label !== "_") {
        ctx.fillStyle = "rgba(0, 200, 120, 0.35)";
        ctx.fillRect(x, y, cellW, cellH);
      }

      ctx.strokeStyle = "rgba(255,255,255,0.5)";
      ctx.lineWidth = 1;
      ctx.strokeRect(x, y, cellW, cellH);

      if (label && label !== "_") {
        ctx.fillStyle = "rgba(0,0,0,0.45)";
        ctx.fillRect(x + cellW / 2 - 30, y + cellH / 2 - 13, 60, 26);
        ctx.fillStyle = "white";
        ctx.fillText(label, x + cellW / 2, y + cellH / 2);
      }
    }
  }
}

function drawPointer(p, progress) {
  // pointer dot
  ctx.beginPath();
  ctx.arc(p.x, p.y, 7, 0, 2 * Math.PI);
  ctx.fillStyle = "lime";
  ctx.fill();

  // dwell progress ring: fills up as you hold still
  ctx.beginPath();
  ctx.arc(p.x, p.y, 18, -Math.PI / 2, -Math.PI / 2 + progress * 2 * Math.PI);
  ctx.strokeStyle = "lime";
  ctx.lineWidth = 4;
  ctx.stroke();
}

function cellAt(x, y) {
  const barH = SETTINGS.suggestionBarHeight;

  // top strip = suggestion buttons (ids 1000, 1001, 1002)
  if (y < barH) {
    const n = SETTINGS.suggestionCount;
    const i = Math.min(n - 1, Math.max(0, Math.floor(x / (overlay.width / n))));
    return 1000 + i;
  }

  const cellW = overlay.width / GRID_COLS;
  const cellH = (overlay.height - barH) / GRID_ROWS;
  const col = Math.min(GRID_COLS - 1, Math.max(0, Math.floor(x / cellW)));
  const row = Math.min(GRID_ROWS - 1, Math.max(0, Math.floor((y - barH) / cellH)));
  return row * GRID_COLS + col;
}

function drawSuggestionBar(activeCell) {
  const n = SETTINGS.suggestionCount;
  const barH = SETTINGS.suggestionBarHeight;
  const w = overlay.width / n;

  ctx.fillStyle = "rgba(0,0,0,0.55)";
  ctx.fillRect(0, 0, overlay.width, barH);

  ctx.font = "bold 15px Arial";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  for (let i = 0; i < n; i++) {
    const x = i * w;
    const isActive = activeCell === 1000 + i;
    const text = suggestions[i] || "...";

    if (isActive) {
      ctx.fillStyle = "rgba(0, 200, 120, 0.45)";
      ctx.fillRect(x, 0, w, barH);
    }

    ctx.strokeStyle = "rgba(255,255,255,0.5)";
    ctx.lineWidth = 1;
    ctx.strokeRect(x, 0, w, barH);

    // wrap long phrases onto two lines
    ctx.fillStyle = suggestions[i] ? "white" : "rgba(255,255,255,0.4)";
    const words = text.split(" ");
    let line = "", lines = [];
    for (const word of words) {
      const test = (line + " " + word).trim();
      if (ctx.measureText(test).width > w - 16 && line) { lines.push(line); line = word; }
      else line = test;
    }
    if (line) lines.push(line);
    lines = lines.slice(0, 2);

    const startY = barH / 2 - ((lines.length - 1) * 9);
    lines.forEach((ln, j) => ctx.fillText(ln, x + w / 2, startY + j * 18));
  }

  // small label showing whether predictions came from Gemini or the offline list
  if (suggestionSource) {
    ctx.font = "10px Arial";
    ctx.textAlign = "left";
    ctx.fillStyle = suggestionSource === "gemini" ? "#8f8" : "#fc8";
    ctx.fillText(suggestionSource === "gemini" ? "AI predictions" : "offline predictions", 6, barH - 7);
  }
}

// ---------- ASK THE BACKEND FOR PREDICTIONS ----------
function refreshSuggestions() {
  clearTimeout(predictTimer);
  predictTimer = setTimeout(async () => {
    if (predictController) predictController.abort();   // cancel older request
    predictController = new AbortController();

    try {
      const formData = new FormData();
      formData.append("text", composedText);
      const res = await fetch("/predict", {
        method: "POST",
        body: formData,
        signal: predictController.signal,
      });
      const data = await res.json();
      suggestions = data.suggestions || [];
      suggestionSource = data.source || "";
    } catch (err) {
      if (err.name !== "AbortError") console.error("prediction failed", err);
    }
  }, 250);   // wait a moment so we don't fire on every single letter
}

// ---------- TEXT ----------
function selectCell(cellIndex) {
  // suggestion bar
  if (cellIndex >= 1000) {
    const picked = suggestions[cellIndex - 1000];
    if (!picked) return;
    composedText = picked.toUpperCase() + " ";
    composedTextEl.textContent = composedText;
    refreshSuggestions();
    return;
  }

  const label = GRID_LETTERS[cellIndex];
  if (!label || label === "_") return;

  if (label === "SPACE") composedText += " ";
  else if (label === "DEL") composedText = composedText.slice(0, -1);
  else composedText += label;

  composedTextEl.textContent = composedText || "\u00A0";
  refreshSuggestions();
}

document.getElementById("clearBtn").onclick = () => {
  composedText = "";
  composedTextEl.textContent = "\u00A0";
  refreshSuggestions();
};

// ---------- VOICE BANK RECORDING ----------
const RECORD_SECONDS = 20;   // longer clips = more accurate voice cloning
let activeRecorder = null;   // so a second click can stop recording early

async function updateVoiceBankStatus() {
  try {
    const res = await fetch("/voice_bank");
    const counts = await res.json();
    const emotion = document.getElementById("emotion").value;
    const n = counts[emotion] || 0;
    document.getElementById("recordStatus").textContent =
      n === 0 ? "No clips yet for this emotion."
              : `${n} clip(s) saved for "${emotion}". 2-3 gives the best result.`;
  } catch (err) { /* ignore */ }
}

document.getElementById("emotion").onchange = updateVoiceBankStatus;

document.getElementById("recordBtn").onclick = async () => {
  const statusEl = document.getElementById("recordStatus");
  const btn = document.getElementById("recordBtn");

  // second click = stop early
  if (activeRecorder && activeRecorder.state === "recording") {
    activeRecorder.stop();
    return;
  }

  const emotion = document.getElementById("emotion").value;

  // turn OFF browser "improvements" — they distort the voice and hurt cloning
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
  });

  const options = { audioBitsPerSecond: 128000 };
  if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) options.mimeType = "audio/webm;codecs=opus";

  const recorder = new MediaRecorder(stream, options);
  activeRecorder = recorder;
  const chunks = [];
  const startTime = performance.now();
  let tick, autoStop;

  recorder.ondataavailable = (e) => chunks.push(e.data);
  recorder.onstop = async () => {
    clearInterval(tick);
    clearTimeout(autoStop);
    stream.getTracks().forEach((t) => t.stop());
    activeRecorder = null;
    btn.textContent = "Record Voice Clip (up to 20s)";

    const seconds = (performance.now() - startTime) / 1000;
    if (seconds < 6) {
      statusEl.textContent = `Only ${seconds.toFixed(0)}s recorded — too short. Please record at least 6 seconds.`;
      return;
    }

    const blob = new Blob(chunks, { type: "audio/webm" });
    const formData = new FormData();
    formData.append("emotion", emotion);
    formData.append("file", blob, "clip.webm");

    statusEl.textContent = "Uploading...";
    const res = await fetch("/upload_voice_clip", { method: "POST", body: formData });
    const data = await res.json();
    statusEl.textContent =
      `Saved ${seconds.toFixed(0)}s clip. ${data.clips} clip(s) for "${data.emotion}". 2-3 gives the best result.`;
  };

  recorder.start();
  btn.textContent = "Stop Recording";

  let left = RECORD_SECONDS;
  statusEl.textContent = `Recording... talk naturally (${left}s left)`;
  tick = setInterval(() => {
    left--;
    if (left > 0) statusEl.textContent = `Recording... keep talking (${left}s left)`;
  }, 1000);
  autoStop = setTimeout(() => recorder.state === "recording" && recorder.stop(), RECORD_SECONDS * 1000);
};

// ---------- SPEAK ----------
const ENGINE_NAMES = { xtts: "XTTS-v2", f5: "F5-TTS" };
let speaking = false;   // stops a second click starting another generation

// Asks the backend for speech from ONE engine.
// Returns { audioSrc, seconds } or throws an Error with a readable message.
async function requestSpeech(engine, text, emotion) {
  const formData = new FormData();
  formData.append("text", text);
  formData.append("emotion", emotion);
  formData.append("engine", engine);

  const start = performance.now();
  const res = await fetch("/speak", { method: "POST", body: formData });
  const seconds = (performance.now() - start) / 1000;

  let data;
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok) throw new Error(data.error || `server returned ${res.status}`);

  return { audioSrc: "data:audio/wav;base64," + data.audio_base64, seconds };
}

document.getElementById("speakBtn").onclick = async () => {
  const statusEl = document.getElementById("speakStatus");
  const emotion = document.getElementById("emotion").value;
  const engine = document.getElementById("engine").value;
  const singleAudio = document.getElementById("outputAudio");
  const compareBox = document.getElementById("compareBox");

  if (speaking) return;
  if (!composedText.trim()) {
    statusEl.textContent = "Compose a sentence first.";
    return;
  }

  speaking = true;
  const text = composedText;   // remember the text now, in case it changes while we wait
  try {
    if (engine !== "both") {
      // --- one engine, one player ---
      compareBox.hidden = true;
      singleAudio.hidden = false;
      statusEl.textContent = `Generating speech with ${ENGINE_NAMES[engine]}...`;
      try {
        const { audioSrc, seconds } = await requestSpeech(engine, text, emotion);
        singleAudio.src = audioSrc;
        singleAudio.play();
        statusEl.textContent = `Done in ${seconds.toFixed(1)}s (${ENGINE_NAMES[engine]}).`;
      } catch (err) {
        statusEl.textContent = "Error: " + err.message;
      }
      return;
    }

    // --- "Compare both": XTTS first, THEN F5 ---
    // One after the other, not at the same time: both models share one Colab GPU,
    // so running them together would make both slower and the timings unfair.
    singleAudio.hidden = true;
    compareBox.hidden = false;
    const players = {
      xtts: { audio: document.getElementById("audioXtts"), time: document.getElementById("timeXtts") },
      f5:   { audio: document.getElementById("audioF5"),   time: document.getElementById("timeF5") },
    };
    for (const p of Object.values(players)) {
      p.audio.removeAttribute("src");
      p.time.textContent = "waiting...";
    }

    const report = [];
    for (const eng of ["xtts", "f5"]) {
      statusEl.textContent = `Generating with ${ENGINE_NAMES[eng]}...`;
      players[eng].time.textContent = "generating...";
      try {
        const { audioSrc, seconds } = await requestSpeech(eng, text, emotion);
        players[eng].audio.src = audioSrc;
        players[eng].time.textContent = `took ${seconds.toFixed(1)}s`;
        report.push(`${ENGINE_NAMES[eng]} ${seconds.toFixed(1)}s`);
      } catch (err) {
        // one engine failing shouldn't stop the other from being tried
        players[eng].time.textContent = "failed: " + err.message;
        report.push(`${ENGINE_NAMES[eng]} failed`);
      }
    }
    statusEl.textContent = "Compare done: " + report.join(", ") + ". Press play on each to listen.";
  } finally {
    speaking = false;
  }
};

