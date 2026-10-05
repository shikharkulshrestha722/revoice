// ============================================================
// ReVoice — Frontend Logic
// ============================================================
// 1. Turn on the webcam (mirrored, like a selfie camera)
// 2. Track a pointer: your index fingertip (Finger mode) or a laser dot
// 3. Smooth the pointer and select letters by holding still (dwell)
// 4. Record voice bank clips and speak via the backend -> Colab
// ============================================================

import { PointerTracker, DwellSelector, detectLaserBlob } from "./tracking.js";
import { analyzeLevels, PROBLEM_MESSAGES } from "./audio_checks.js";
import { selectionSavings } from "./selection_stats.js";
import { metricsLines, moreSimilarEngine } from "./metrics_format.js";
import { mixToMono, trimSamples, encodeWav16, waveformPeaks, speechChunks, speechSecondsInRange,
         hasAllowedExtension } from "./audio_prep.js";

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

// ---------- MOTION ----------
// People who set "reduce motion" on their computer get no decorative animation.
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const scrollMotion = () => (reducedMotion.matches ? "auto" : "smooth");

// ---------- TOASTS ----------
// Small notices in the bottom-right corner. They are EXTRA: the normal status
// text is still shown too. Success notices go away after 4 s; errors stay until
// the caregiver closes them, so an error can never be missed.
function showToast(message, type = "success") {
  const box = document.getElementById("toasts");
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.setAttribute("role", type === "error" ? "alert" : "status");
  const text = document.createElement("span");
  text.textContent = message;
  const close = document.createElement("button");
  close.className = "toast-close";
  close.setAttribute("aria-label", "Dismiss");
  close.textContent = "\u00D7";
  close.onclick = () => toast.remove();
  toast.append(text, close);
  box.appendChild(toast);
  if (type !== "error") setTimeout(() => toast.remove(), 4000);
  // keep at most 4 on screen: drop the oldest NON-error first
  const all = [...box.children];
  if (all.length > 4) (all.find((t) => !t.classList.contains("toast-error")) || all[0]).remove();
}

// ---------- THEME (light / dark) ----------
// Default: follow the system setting. Pressing the header button picks one
// and remembers it (index.html applies the saved choice before the page draws).
const THEME_KEY = "revoice-theme";
const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
const themeToggle = document.getElementById("themeToggle");

function currentTheme() {
  const chosen = document.documentElement.dataset.theme;
  if (chosen === "light" || chosen === "dark") return chosen;
  return systemDark.matches ? "dark" : "light";
}

function updateThemeToggle() {
  const dark = currentTheme() === "dark";
  themeToggle.querySelector(".icon").textContent = dark ? "☀" : "☾";   // sun / moon
  themeToggle.querySelector(".label").textContent = dark ? "Light" : "Dark";
  themeToggle.setAttribute("aria-pressed", String(dark));
  themeToggle.setAttribute("aria-label", `Switch to ${dark ? "light" : "dark"} theme`);
  document.dispatchEvent(new Event("revoice-themechange"));   // the camera overlay re-reads its colours
}

function setTheme(theme) {
  const apply = () => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* not saved, still switches */ }
    updateThemeToggle();
  };
  // a quick cross-fade between the old and new look (browser "view transition");
  // instant if the browser can't, or if reduced motion is on
  if (document.startViewTransition && !reducedMotion.matches) document.startViewTransition(apply);
  else apply();
}

themeToggle.onclick = () => setTheme(currentTheme() === "dark" ? "light" : "dark");
systemDark.addEventListener("change", updateThemeToggle);   // system switched while open
updateThemeToggle();

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
// (users + voice bank are loaded at the very end of this file)

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
    drawSelectionFlash(now);
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
// Colours come from the CSS theme variables (--canvas-* in style.css), so the
// overlay matches light/dark mode. They're read ONCE here and again only when the
// theme changes -- never inside the frame loop, which must stay fast.
const overlayColors = {};
const OVERLAY_FONT = '"Atkinson Hyperlegible", "Segoe UI", Arial, sans-serif';
const labelChipWidths = {};      // label -> chip width (measured once)
// the nicer font may finish loading after the first frames: measure again then
document.fonts?.ready.then(() => { for (const k in labelChipWidths) delete labelChipWidths[k]; });

function readOverlayColors() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  Object.assign(overlayColors, {
    chip: v("--canvas-chip"), chipText: v("--canvas-chip-text"), bar: v("--canvas-bar"),
    grid: v("--canvas-grid"), gridOutline: v("--canvas-grid-outline"), active: v("--canvas-active"),
    pointer: v("--canvas-pointer"), flash: v("--canvas-flash"), mutedText: v("--canvas-muted-text"),
    success: v("--success"), warning: v("--warning"),
    waveKept: v("--accent"), waveCut: v("--border-strong"),
  });
}
readOverlayColors();
document.addEventListener("revoice-themechange", () => {
  readOverlayColors();
  // upload waveforms are drawn once (not every frame), so redraw them in the new colours
  for (const item of upload.items) if (item.peaks) drawWaveform(item);
});

// a thin grid line that shows up over bright AND dark video: dark outline, light line
function strokeCell(x, y, w, h) {
  ctx.lineWidth = 3;
  ctx.strokeStyle = overlayColors.gridOutline;
  ctx.strokeRect(x, y, w, h);
  ctx.lineWidth = 1;
  ctx.strokeStyle = overlayColors.grid;
  ctx.strokeRect(x, y, w, h);
}

function drawGrid(activeCell) {
  const barH = SETTINGS.suggestionBarHeight;
  const cellW = overlay.width / GRID_COLS;
  const cellH = (overlay.height - barH) / GRID_ROWS;

  ctx.font = `bold 20px ${OVERLAY_FONT}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  for (let row = 0; row < GRID_ROWS; row++) {
    for (let col = 0; col < GRID_COLS; col++) {
      const idx = row * GRID_COLS + col;
      const x = col * cellW, y = barH + row * cellH;
      const label = GRID_LETTERS[idx];

      if (idx === activeCell && label !== "_") {
        ctx.fillStyle = overlayColors.active;
        ctx.fillRect(x, y, cellW, cellH);
      }

      strokeCell(x, y, cellW, cellH);

      if (label && label !== "_") {
        // solid "chip" behind the letter: readable over any camera background.
        // Chip width fits the label (SPACE/DEL are wider); measured once, then cached.
        const chipW = labelChipWidths[label] ??= Math.max(64, ctx.measureText(label).width + 20);
        ctx.fillStyle = overlayColors.chip;
        ctx.fillRect(x + cellW / 2 - chipW / 2, y + cellH / 2 - 15, chipW, 30);
        ctx.fillStyle = overlayColors.chipText;
        ctx.fillText(label, x + cellW / 2, y + cellH / 2 + 1);
      }
    }
  }
}

function drawPointer(p, progress) {
  // pointer dot, with a dark edge so it shows over bright video too
  ctx.beginPath();
  ctx.arc(p.x, p.y, 7, 0, 2 * Math.PI);
  ctx.fillStyle = overlayColors.pointer;
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = overlayColors.gridOutline;
  ctx.stroke();

  // dwell progress ring: fills up as you hold still (dark track underneath for contrast)
  const end = -Math.PI / 2 + progress * 2 * Math.PI;
  ctx.beginPath();
  ctx.arc(p.x, p.y, 18, -Math.PI / 2, end);
  // soft glow that grows as the ring fills (a wide, faint stroke: cheap, no blur filter)
  if (progress > 0) {
    ctx.globalAlpha = 0.35 * progress;
    ctx.strokeStyle = overlayColors.pointer;
    ctx.lineWidth = 6 + 10 * progress;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  ctx.strokeStyle = overlayColors.gridOutline;
  ctx.lineWidth = 7;
  ctx.stroke();
  ctx.strokeStyle = overlayColors.pointer;
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

  ctx.fillStyle = overlayColors.bar;
  ctx.fillRect(0, 0, overlay.width, barH);

  ctx.font = `bold 16px ${OVERLAY_FONT}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  for (let i = 0; i < n; i++) {
    const x = i * w;
    const isActive = activeCell === 1000 + i;
    const text = suggestions[i] || "...";

    if (isActive) {
      ctx.fillStyle = overlayColors.active;
      ctx.fillRect(x, 0, w, barH);
    }

    strokeCell(x, 0, w, barH);

    // wrap long phrases onto two lines
    ctx.fillStyle = suggestions[i] ? overlayColors.chipText : overlayColors.mutedText;
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
    ctx.font = `bold 11px ${OVERLAY_FONT}`;
    ctx.textAlign = "left";
    ctx.fillStyle = suggestionSource === "gemini" ? overlayColors.success : overlayColors.warning;
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

// ---------- SELECTION COUNTER ----------
// Counts every pointer selection for the current sentence, and compares it
// with spelling the sentence letter by letter (see selection_stats.js).
// Each sentence gets a random id so the log counts it once, even if it is
// spoken twice or with "Compare both".
let selectionCount = 0;
let utteranceId = crypto.randomUUID();

function updateSelectionStats() {
  const el = document.getElementById("selectionStats");
  const s = selectionSavings(selectionCount, composedText);
  if (!s.letterByLetter) {
    el.textContent = `Selections used: ${selectionCount}`;
    return;
  }
  el.textContent =
    `Selections used: ${s.selections} · letter by letter: ${s.letterByLetter} ` +
    `(${s.letters} letters + ${s.spaces} spaces) · saved by prediction: ${s.savedPct}%`;
}

// ---------- TEXT ----------
// The selected cell lights up briefly, so the patient SEES that it was chosen.
const FLASH_MS = 280;
let flash = null;                 // { cell, start } of the last selection

function cellRect(cell) {
  const barH = SETTINGS.suggestionBarHeight;
  if (cell >= 1000) {
    const w = overlay.width / SETTINGS.suggestionCount;
    return [(cell - 1000) * w, 0, w, barH];
  }
  const cellW = overlay.width / GRID_COLS, cellH = (overlay.height - barH) / GRID_ROWS;
  return [(cell % GRID_COLS) * cellW, barH + Math.floor(cell / GRID_COLS) * cellH, cellW, cellH];
}

function drawSelectionFlash(now) {
  if (!flash) return;
  const elapsed = now - flash.start;
  if (elapsed > FLASH_MS) { flash = null; return; }
  // fades out normally; with reduced motion it's a steady highlight (still clear feedback)
  ctx.globalAlpha = reducedMotion.matches ? 1 : 1 - elapsed / FLASH_MS;
  ctx.fillStyle = overlayColors.flash;
  ctx.fillRect(...cellRect(flash.cell));
  ctx.globalAlpha = 1;
}

function selectCell(cellIndex) {
  flash = { cell: cellIndex, start: performance.now() };
  // suggestion bar
  if (cellIndex >= 1000) {
    const picked = suggestions[cellIndex - 1000];
    if (!picked) { flash = null; return; }
    selectionCount++;
    composedText = picked.toUpperCase() + " ";
    composedTextEl.textContent = composedText;
    updateSelectionStats();
    refreshSuggestions();
    return;
  }

  const label = GRID_LETTERS[cellIndex];
  if (!label || label === "_") { flash = null; return; }
  selectionCount++;

  if (label === "SPACE") composedText += " ";
  else if (label === "DEL") composedText = composedText.slice(0, -1);
  else composedText += label;

  composedTextEl.textContent = composedText || "\u00A0";
  updateSelectionStats();
  refreshSuggestions();
}

document.getElementById("clearBtn").onclick = () => {
  composedText = "";
  composedTextEl.textContent = "\u00A0";
  selectionCount = 0;                     // new sentence: start counting again
  utteranceId = crypto.randomUUID();
  updateSelectionStats();
  refreshSuggestions();
};

// ---------- USER PROFILES ----------
// Each user has their own saved voice (recordings) or a stock voice.
// These controls are for the CAREGIVER, so mouse-only is fine here.
let profiles = [];
let activeProfile = null;     // the selected user, or null if there are none

async function loadProfiles() {
  try {
    const res = await fetch("/profiles");
    const data = await res.json();
    profiles = data.profiles || [];
    activeProfile = profiles.find((p) => p.id === data.active) || null;
  } catch (err) {
    console.error("could not load users", err);
    document.getElementById("profileStatus").textContent = "Could not load users — is the backend running?";
    return;
  }
  renderProfiles();
  // first run: no users yet, so open the "Add user" panel straight away
  if (profiles.length === 0) openAddUserPanel();
}

function renderProfiles() {
  // one button per user, so switching during a demo is a single click
  const box = document.getElementById("profileButtons");
  box.innerHTML = "";
  for (const p of profiles) {
    const btn = document.createElement("button");
    btn.textContent = p.name + (p.voice_type === "stock" ? " (stock voice)" : "");
    if (activeProfile && p.id === activeProfile.id) btn.classList.add("active");
    btn.onclick = () => selectProfile(p.id);
    box.appendChild(btn);
  }

  document.getElementById("deleteUserBtn").hidden = !activeProfile;
  document.getElementById("profileStatus").textContent =
    profiles.length === 0 ? "No users yet — add one to start." : "";

  applyProfileToVoiceControls();
}

// Stock-voice users have nothing to record and can only use XTTS.
function applyProfileToVoiceControls() {
  const isStock = activeProfile?.voice_type === "stock";
  if (guided.profileId && guided.profileId !== activeProfile?.id) closeGuidedPanel();
  if (upload.profileId && upload.profileId !== activeProfile?.id) closeUploadPanel();
  const stockNote = document.getElementById("stockNote");

  document.getElementById("recordControls").hidden = isStock;
  stockNote.hidden = !isStock;
  if (isStock) {
    stockNote.textContent =
      `${activeProfile.name} uses the stock voice "${activeProfile.stock_speaker}". ` +
      "No recording needed. The emotion still changes the speaking style.";
  }

  // F5-TTS is the default engine; stock voices only work with XTTS
  const engineEl = document.getElementById("engine");
  for (const opt of engineEl.options) opt.disabled = isStock && opt.value !== "xtts";
  if (isStock) engineEl.value = "xtts";
  else if (engineEl.dataset.forcedForStock) engineEl.value = "f5";   // back to the default
  engineEl.dataset.forcedForStock = isStock ? "yes" : "";

  updateVoiceBankStatus();
}

async function selectProfile(id) {
  const res = await fetch(`/profiles/${encodeURIComponent(id)}/select`, { method: "POST" });
  if (!res.ok) {
    document.getElementById("profileStatus").textContent = "Could not switch user.";
    return loadProfiles();
  }
  activeProfile = await res.json();
  profiles = profiles.map((p) => (p.id === id ? activeProfile : p));
  renderProfiles();
}

document.getElementById("deleteUserBtn").onclick = async () => {
  if (!activeProfile) return;
  const name = activeProfile.name;
  const ok = confirm(
    `Delete "${name}" and ALL of their voice recordings?\n\nThis cannot be undone.`);
  if (!ok) return;

  const res = await fetch(`/profiles/${encodeURIComponent(activeProfile.id)}`, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  const statusEl = document.getElementById("profileStatus");
  await loadProfiles();
  if (!res.ok) {
    statusEl.textContent = "Delete failed: " + (data.error || res.status);
    showToast(statusEl.textContent, "error");
    return;
  }
  statusEl.textContent = data.colab_cache_cleared
    ? `Deleted "${name}" and their recordings. Colab has forgotten the voice too.`
    : `Deleted "${name}" and their recordings. (Colab was offline; its copy disappears when it restarts.)`;
  showToast(`Deleted "${name}" and their recordings.`);
};

// ---------- ADD USER PANEL ----------
const addUserPanel = document.getElementById("addUserPanel");

function chosenVoiceType() {
  return document.querySelector('input[name="voiceType"]:checked').value;
}

const CONSENT_TEXTS = {
  cloned: "I agree that my voice may be recorded and cloned for ReVoice. I can ask for it to be deleted at any time.",
  upload: "I am this person or their family/legal guardian and have the right to use their recordings to recreate their voice.",
};

function updateAddUserForm() {
  const type = chosenVoiceType();
  document.getElementById("stockRow").hidden = type !== "stock";
  // consent is about copying someone's real voice, so only needed for cloning
  document.getElementById("consentRow").hidden = type === "stock";
  if (CONSENT_TEXTS[type]) document.getElementById("consentText").textContent = CONSENT_TEXTS[type];
}

async function loadStockSpeakers() {
  const select = document.getElementById("stockSpeaker");
  if (select.options.length) return;          // already loaded
  const createBtn = document.getElementById("createUserBtn");
  createBtn.disabled = true;                  // can't create until there's a voice to pick
  document.getElementById("stockSource").textContent = "loading voices...";
  try {
    const res = await fetch("/stock_speakers");
    const data = await res.json();
    for (const name of data.speakers) select.add(new Option(name, name));
    document.getElementById("stockSource").textContent =
      data.source === "colab" ? "" : "(Colab offline — showing a short default list)";
  } catch (err) {
    document.getElementById("stockSource").textContent = "Could not load stock voices.";
  } finally {
    createBtn.disabled = false;
  }
}

function openAddUserPanel() {
  addUserPanel.hidden = false;
  document.getElementById("addUserStatus").textContent = "";
  updateAddUserForm();
  document.getElementById("newName").focus();
}

document.getElementById("addUserBtn").onclick = openAddUserPanel;
document.getElementById("cancelAddBtn").onclick = () => { addUserPanel.hidden = true; };
for (const radio of document.querySelectorAll('input[name="voiceType"]')) {
  radio.onchange = () => {
    updateAddUserForm();
    if (chosenVoiceType() === "stock") loadStockSpeakers();
  };
}

document.getElementById("createUserBtn").onclick = async () => {
  const statusEl = document.getElementById("addUserStatus");
  const name = document.getElementById("newName").value.trim();
  const voiceType = chosenVoiceType();
  const consent = document.getElementById("consent").checked;

  // quick checks here; the backend checks again (it has the final say)
  if (!name) { statusEl.textContent = "Please enter a name."; return; }
  // "upload" = a cloned voice made from old recordings instead of new ones
  const cloned = voiceType === "cloned" || voiceType === "upload";
  if (cloned && !consent) {
    statusEl.textContent = "Please tick the consent box before cloning a voice.";
    return;
  }

  const formData = new FormData();
  formData.append("name", name);
  formData.append("voice_type", cloned ? "cloned" : voiceType);
  formData.append("stock_speaker", voiceType === "stock" ? document.getElementById("stockSpeaker").value : "");
  formData.append("consent_given", cloned && consent ? "true" : "false");

  const res = await fetch("/profiles", { method: "POST", body: formData });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    statusEl.textContent = "Error: " + (data.error || res.status);
    return;
  }

  // reset the form for next time, close it, and show the new (selected) user
  document.getElementById("newName").value = "";
  document.getElementById("consent").checked = false;
  addUserPanel.hidden = true;
  await loadProfiles();
  document.getElementById("profileStatus").textContent =
    voiceType === "cloned" ? `Added ${name}. Now record a voice clip for each emotion.`
    : voiceType === "upload" ? `Added ${name}. Now add their old recordings below.`
    : `Added ${name}.`;
  showToast(`Added ${name}.`);
  if (voiceType === "upload") openUploadPanel();
};

// ---------- MICROPHONE (shared by both recorders) ----------
// turn OFF browser "improvements" — they distort the voice and hurt cloning
function getMicStream() {
  return navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
  });
}

function makeRecorder(stream) {
  const options = { audioBitsPerSecond: 128000 };
  if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) options.mimeType = "audio/webm;codecs=opus";
  return new MediaRecorder(stream, options);
}

// ---------- VOICE BANK RECORDING ----------
const RECORD_SECONDS = 20;   // longer clips = more accurate voice cloning
let activeRecorder = null;   // so a second click can stop recording early

async function updateVoiceBankStatus() {
  const statusEl = document.getElementById("recordStatus");
  if (!activeProfile) {
    statusEl.textContent = "Add a user first.";
    return;
  }
  if (activeProfile.voice_type === "stock") return;
  try {
    const res = await fetch(`/voice_bank?profile_id=${encodeURIComponent(activeProfile.id)}`);
    const counts = await res.json();
    const emotion = document.getElementById("emotion").value;
    const n = counts[emotion] || 0;
    // no clip for this emotion: the server falls back to uploaded recordings, then guided ones
    let fallback = "";
    if (n === 0) {
      const pid = encodeURIComponent(activeProfile.id);
      const up = await (await fetch(`/uploads?profile_id=${pid}`)).json();
      if ((up.clips || []).length) fallback = "the uploaded old recordings";
      else {
        const g = await (await fetch(`/guided/status?profile_id=${pid}`)).json();
        if ((g.references || []).length) fallback = "your guided recordings";
      }
    }
    statusEl.textContent =
      n > 0 ? `${n} clip(s) saved for "${emotion}" (${activeProfile.name}). 2-3 gives the best result.`
      : fallback ? `No "${emotion}" clip yet, so ${fallback} will be used (${activeProfile.name}).`
      : `No clips yet for "${emotion}" (${activeProfile.name}).`;
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
  if (!activeProfile) {
    statusEl.textContent = "Add a user first.";
    return;
  }
  // remember WHO we're recording now, in case the user is switched mid-recording
  const profileId = activeProfile.id;

  const stream = await getMicStream();
  const recorder = makeRecorder(stream);
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
    btn.classList.remove("recording");          // stops the pulsing red dot

    const seconds = (performance.now() - startTime) / 1000;
    if (seconds < 6) {
      statusEl.textContent = `Only ${seconds.toFixed(0)}s recorded — too short. Please record at least 6 seconds.`;
      return;
    }

    const blob = new Blob(chunks, { type: "audio/webm" });
    const formData = new FormData();
    formData.append("emotion", emotion);
    formData.append("profile_id", profileId);
    formData.append("file", blob, "clip.webm");

    statusEl.textContent = "Uploading...";
    const res = await fetch("/upload_voice_clip", { method: "POST", body: formData });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      statusEl.textContent = "Upload failed: " + (data.error || res.status);
      showToast(statusEl.textContent, "error");
      return;
    }
    statusEl.textContent =
      `Saved ${seconds.toFixed(0)}s clip. ${data.clips} clip(s) for "${data.emotion}". 2-3 gives the best result.`;
    showToast(`Voice clip saved for "${data.emotion}".`);
  };

  recorder.start();
  btn.textContent = "Stop Recording";
  btn.classList.add("recording");               // pulsing red dot while recording

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

// Empties a player, so if the next request fails nobody mistakes the
// PREVIOUS sentence's audio for the new one.
function clearPlayer(audio) {
  audio.pause();
  audio.removeAttribute("src");
  audio.load();                 // makes the browser actually drop the old audio
}

// F5 copies the voice from one reference clip plus a transcript of it (made by
// Whisper on Colab). Showing the transcript lets you spot a bad reference:
// if it doesn't match what was said, re-record the clip.
function showRefText(el, refText) {
  el.hidden = !refText;
  el.textContent = refText ? `F5 heard the reference recording as: "${refText}"` : "";
}

// Voice metrics measured on Colab (similarity, pitch, rate, accent), one line each.
// Shows nothing if there are none (e.g. metrics switched off on Colab).
function showMetrics(el, metrics) {
  const lines = metricsLines(metrics);
  el.replaceChildren(...lines.map((line) => {
    const div = document.createElement("div");
    div.textContent = line;
    return div;
  }));
  el.hidden = lines.length === 0;
}

// Asks the backend for speech from ONE engine.
// Returns { audioSrc, seconds, refText, metrics } or throws an Error with a readable message.
async function requestSpeech(engine, text, emotion) {
  const formData = new FormData();
  formData.append("text", text);
  formData.append("emotion", emotion);
  formData.append("engine", engine);
  if (activeProfile) formData.append("profile_id", activeProfile.id);
  formData.append("selections", selectionCount);    // for the generation log
  formData.append("utterance_id", utteranceId);

  const start = performance.now();
  const res = await fetch("/speak", { method: "POST", body: formData });
  const seconds = (performance.now() - start) / 1000;

  let data;
  try { data = await res.json(); } catch { data = {}; }
  if (!res.ok) throw new Error(data.error || `server returned ${res.status}`);

  return { audioSrc: "data:audio/wav;base64," + data.audio_base64, seconds,
           refText: data.ref_text || "", metrics: data.metrics || null };
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
  document.getElementById("speakBusy").hidden = false;    // animated "generating" dots
  const text = composedText;   // remember the text now, in case it changes while we wait
  try {
    if (engine !== "both") {
      // --- one engine, one player ---
      compareBox.hidden = true;
      singleAudio.hidden = false;
      const refTextEl = document.getElementById("refTextSingle");
      const metricsEl = document.getElementById("metricsSingle");
      clearPlayer(singleAudio);
      showRefText(refTextEl, "");
      showMetrics(metricsEl, null);
      statusEl.textContent = `Generating speech with ${ENGINE_NAMES[engine]}...`;
      try {
        const { audioSrc, seconds, refText, metrics } = await requestSpeech(engine, text, emotion);
        singleAudio.src = audioSrc;
        singleAudio.play();
        showRefText(refTextEl, refText);
        showMetrics(metricsEl, metrics);
        statusEl.textContent = `Done in ${seconds.toFixed(1)}s (${ENGINE_NAMES[engine]}).`;
      } catch (err) {
        statusEl.textContent = "Error: " + err.message;
        showToast("Speech failed: " + err.message, "error");
      }
      return;
    }

    // --- "Compare both": XTTS first, THEN F5 ---
    // One after the other, not at the same time: both models share one Colab GPU,
    // so running them together would make both slower and the timings unfair.
    clearPlayer(singleAudio);
    singleAudio.hidden = true;
    compareBox.hidden = false;
    const player = (id) => ({
      audio: document.getElementById("audio" + id), time: document.getElementById("time" + id),
      metrics: document.getElementById("metrics" + id), item: document.getElementById("item" + id),
      badge: document.getElementById("badge" + id),
    });
    const players = { xtts: player("Xtts"), f5: player("F5") };
    for (const p of Object.values(players)) {
      clearPlayer(p.audio);
      showMetrics(p.metrics, null);
      p.item.classList.remove("winner");
      p.badge.hidden = true;
      p.time.textContent = "waiting...";
    }
    const metricsByEngine = {};
    const refTextF5 = document.getElementById("refTextF5");
    showRefText(refTextF5, "");

    const report = [];
    for (const eng of ["xtts", "f5"]) {
      statusEl.textContent = `Generating with ${ENGINE_NAMES[eng]}...`;
      players[eng].time.textContent = "generating...";
      try {
        const { audioSrc, seconds, refText, metrics } = await requestSpeech(eng, text, emotion);
        players[eng].audio.src = audioSrc;
        players[eng].time.textContent = `took ${seconds.toFixed(1)}s`;
        if (eng === "f5") showRefText(refTextF5, refText);
        showMetrics(players[eng].metrics, metrics);
        metricsByEngine[eng] = metrics;
        report.push(`${ENGINE_NAMES[eng]} ${seconds.toFixed(1)}s`);
      } catch (err) {
        // one engine failing shouldn't stop the other from being tried
        players[eng].time.textContent = "failed: " + err.message;
        report.push(`${ENGINE_NAMES[eng]} failed`);
        showToast(`${ENGINE_NAMES[eng]} failed: ${err.message}`, "error");
      }
    }
    // highlight the engine whose voice was measured as closer to the real person
    const winner = moreSimilarEngine(metricsByEngine);
    if (winner) {
      players[winner].item.classList.add("winner");
      players[winner].badge.hidden = false;
    }
    statusEl.textContent = "Compare done: " + report.join(", ") + ". Press play on each to listen.";
    if (winner) showToast(`Compare done: ${ENGINE_NAMES[winner]} sounded more like the real voice.`);
  } finally {
    speaking = false;
    document.getElementById("speakBusy").hidden = true;
  }
};


// ---------- GUIDED VOICE BANKING ----------
// The person reads one sentence at a time. Each take is uploaded with the
// sentence id, so the server stores it next to its EXACT words.
// Set up by the person banking their voice (or a caregiver), so mouse +
// keyboard is fine here.
const GUIDED_MAX_SECONDS = 30;   // stop automatically after this (long sentences, slow speech)
const guided = {
  sentences: [],        // [{id, text}] from the server
  sessionSize: 50,      // sentences per sitting (from the server)
  status: null,         // progress from the server
  index: 0,             // which sentence is on screen
  stream: null,         // microphone, kept open while the panel is open (no delay before each take)
  recorder: null,
  profileId: null,      // who we're recording; fixed while the panel is open
  lastTakeUrl: null,    // the take just recorded, for instant playback
};
const guidedEl = (id) => document.getElementById(id);

async function openGuidedPanel() {
  if (!activeProfile || activeProfile.voice_type !== "cloned") return;
  guided.profileId = activeProfile.id;
  guidedEl("guidedPanel").hidden = false;
  guidedEl("guidedStatus").textContent = "Loading...";
  guidedEl("guidedPanel").scrollIntoView({ behavior: scrollMotion() });

  try {
    if (!guided.sentences.length) {
      const data = await (await fetch("/guided/sentences")).json();
      guided.sentences = data.sentences;
      guided.sessionSize = data.session_size || 50;
    }
    await refreshGuidedStatus();
    // resume where they left off: the first sentence not yet recorded
    const resumeAt = guided.sentences.findIndex((s) => s.id === guided.status.next_id);
    guided.index = resumeAt >= 0 ? resumeAt : guided.sentences.length - 1;
    guided.stream = await getMicStream();
    guidedEl("guidedStatus").textContent = guided.status.count
      ? `Welcome back, ${activeProfile.name}. Carrying on where you left off.`
      : "Press Record (or Space), read the sentence, then press Stop.";
  } catch (err) {
    console.error(err);
    guidedEl("guidedStatus").textContent = "Could not start: " + (err.message || err.name) +
      " (allow the microphone and make sure the backend is running).";
  }
  showGuidedSentence();
}

function closeGuidedPanel() {
  if (guided.recorder && guided.recorder.state === "recording") {
    guided.recorder.onstop = null;          // closing = throw this take away
    guided.recorder.stop();
  }
  if (guided.stream) guided.stream.getTracks().forEach((t) => t.stop());
  guided.stream = null;
  guided.recorder = null;
  guided.profileId = null;
  guidedEl("guidedPanel").hidden = true;
  updateVoiceBankStatus();                  // references may have changed
}

async function refreshGuidedStatus() {
  const res = await fetch(`/guided/status?profile_id=${encodeURIComponent(guided.profileId)}`);
  guided.status = await res.json();
  if (!res.ok) throw new Error(guided.status.error || res.status);
  showGuidedProgress();
}

function showGuidedProgress() {
  const st = guided.status;
  const minutes = st.total_seconds / 60;
  guidedEl("guidedBar").style.width = `${(100 * st.count) / st.total_sentences}%`;
  guidedEl("guidedCounts").textContent =
    `${st.count} / ${st.total_sentences} recorded · ${minutes.toFixed(1)} minutes of speech` +
    (st.skipped.length ? ` · ${st.skipped.length} skipped` : "") +
    " (fine-tuning needs 15-30 minutes; voice references need about half a minute)";
  renderGuidedSessions();
  guidedEl("guidedExportBtn").disabled = st.count === 0;
  guidedEl("guidedRefsBtn").disabled = st.count === 0;

  const refSeconds = st.references.reduce((sum, id) => sum + (st.recorded[id]?.duration_s || 0), 0);
  guidedEl("guidedRefsStatus").textContent = st.references.length
    ? `Using ${st.references.length} recordings (${refSeconds.toFixed(0)}s) as voice references ` +
      "for any emotion that has no clip of its own."
    : "Tip: after 8-10 sentences, use them as voice references so speech works without separate clips.";
}

// ----- sessions: the list is split into blocks of ~50 so it can be done over several sittings -----
const sessionOf = (index) => Math.floor(index / guided.sessionSize);
const sessionCount = () => Math.ceil(guided.sentences.length / guided.sessionSize);
const sessionRange = (n) => [n * guided.sessionSize,
                             Math.min(guided.sentences.length, (n + 1) * guided.sessionSize)];

function sessionProgress(n) {
  const [from, to] = sessionRange(n);
  let done = 0;
  for (let i = from; i < to; i++) if (guided.status?.recorded[guided.sentences[i].id]) done++;
  return { done, total: to - from };
}

function renderGuidedSessions() {
  const select = guidedEl("guidedSession");
  if (!guided.sentences.length) return;
  select.innerHTML = "";
  for (let n = 0; n < sessionCount(); n++) {
    const [from, to] = sessionRange(n);
    const { done, total } = sessionProgress(n);
    const tick = done === total ? " ✓" : "";
    select.add(new Option(`${n + 1} (sentences ${from + 1}-${to}) ${done}/${total}${tick}`, n));
  }
  const current = sessionOf(guided.index);
  select.value = current;
  const { done, total } = sessionProgress(current);
  guidedEl("guidedSessionInfo").textContent =
    `Session ${current + 1} of ${sessionCount()}: ${done} of ${total} done`;
}

// picking a session jumps to its first unrecorded sentence
guidedEl("guidedSession").onchange = (e) => {
  const [from, to] = sessionRange(Number(e.target.value));
  let target = from;
  for (let i = from; i < to; i++) {
    if (!guided.status.recorded[guided.sentences[i].id]) { target = i; break; }
  }
  goToGuided(target);
};

function showGuidedSentence() {
  const s = guided.sentences[guided.index];
  if (!s) return;
  const done = guided.status?.recorded[s.id];
  guidedEl("guidedSentence").textContent = s.text;
  guidedEl("guidedSentence").classList.toggle("recorded", !!done);
  guidedEl("guidedSentenceId").textContent =
    `Sentence ${guided.index + 1} of ${guided.sentences.length}` +
    (done ? ` · recorded (${done.duration_s.toFixed(1)}s) — press Record to redo` : "");
  guidedEl("guidedRecordBtn").innerHTML = done ? "&#9679; Redo" : "&#9679; Record";
  guidedEl("guidedPlayBtn").disabled = !done;
  guidedEl("guidedPrevBtn").disabled = guided.index === 0;
  guided.lastTakeUrl = null;
  renderGuidedSessions();
}

function goToGuided(index) {
  if (guided.recorder && guided.recorder.state === "recording") return;   // finish the take first
  guided.index = Math.max(0, Math.min(guided.sentences.length - 1, index));
  guidedEl("guidedStatus").textContent = "";
  guidedEl("guidedStatus").classList.remove("warn");
  showGuidedSentence();
}

// after a good take: go to the next sentence still to do IN THIS SESSION;
// when the session is finished, suggest a break and move on
function goToNextGuided() {
  const st = guided.status;
  const current = sessionOf(guided.index);
  const [, end] = sessionRange(current);
  for (let i = guided.index + 1; i < end; i++) {
    const id = guided.sentences[i].id;
    if (!st.recorded[id] && !st.skipped.includes(id)) return goToGuided(i);
  }

  if (st.next_id === null) {
    guidedEl("guidedStatus").textContent = "All sentences recorded. Thank you! You can download the dataset now.";
    renderGuidedSessions();
    return;
  }
  const { done, total } = sessionProgress(current);
  const next = guided.sentences.findIndex((s) => s.id === st.next_id);
  goToGuided(next);
  guidedEl("guidedStatus").textContent = done === total
    ? `Session ${current + 1} finished! A good moment for a break. Carry on whenever you're ready.`
    : `End of session ${current + 1} (${total - done} skipped). Continuing with the next sentence to do.`;
}

// Decodes the take and checks its loudness (see audio_checks.js)
async function checkTake(blob) {
  try {
    const ctx = new AudioContext();
    const audio = await ctx.decodeAudioData(await blob.arrayBuffer());
    ctx.close();
    return analyzeLevels(audio.getChannelData(0)).problems;
  } catch (err) {
    console.warn("could not check recording level", err);
    return [];   // can't check = don't block the person
  }
}

function toggleGuidedRecording() {
  if (!guided.stream) return;
  const btn = guidedEl("guidedRecordBtn");
  const statusEl = guidedEl("guidedStatus");

  // second press = stop
  if (guided.recorder && guided.recorder.state === "recording") {
    guided.recorder.stop();
    return;
  }

  const sentence = guided.sentences[guided.index];
  const profileId = guided.profileId;
  const recorder = makeRecorder(guided.stream);
  guided.recorder = recorder;
  const chunks = [];
  const startTime = performance.now();
  const autoStop = setTimeout(() => recorder.state === "recording" && recorder.stop(), GUIDED_MAX_SECONDS * 1000);

  recorder.ondataavailable = (e) => chunks.push(e.data);
  recorder.onstop = async () => {
    clearTimeout(autoStop);
    btn.classList.remove("recording");
    guidedEl("guidedSentence").classList.remove("recording");
    const seconds = (performance.now() - startTime) / 1000;
    if (seconds < 0.7) {
      statusEl.textContent = "That was too short. Press Record, read the sentence, then Stop.";
      showGuidedSentence();
      return;
    }

    const blob = new Blob(chunks, { type: "audio/webm" });
    const problems = await checkTake(blob);

    statusEl.textContent = "Saving...";
    const formData = new FormData();
    formData.append("sentence_id", sentence.id);
    formData.append("duration_s", seconds.toFixed(2));
    formData.append("profile_id", profileId);
    formData.append("file", blob, `${sentence.id}.webm`);
    const res = await fetch("/guided/record", { method: "POST", body: formData });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      statusEl.textContent = "Save failed: " + (data.error || res.status);
      showToast(statusEl.textContent, "error");
      showGuidedSentence();
      return;
    }
    guided.status = data;
    showGuidedProgress();

    if (problems.length) {
      // keep the take (it's saved) but stay here so they can redo it
      showGuidedSentence();
      guided.lastTakeUrl = URL.createObjectURL(blob);
      statusEl.textContent = "Saved, but: " + problems.map((p) => PROBLEM_MESSAGES[p]).join(" ");
      statusEl.classList.add("warn");
    } else {
      statusEl.textContent = `Saved (${seconds.toFixed(1)}s).`;
      statusEl.classList.remove("warn");
      setTimeout(goToNextGuided, 500);      // short pause, then the next sentence
    }
  };

  recorder.start();
  btn.innerHTML = "&#9632; Stop";
  btn.classList.add("recording");
  guidedEl("guidedSentence").classList.add("recording");
  statusEl.classList.remove("warn");
  statusEl.textContent = "Recording... read the sentence, then press Stop (or Space).";
}

function playGuided() {
  const s = guided.sentences[guided.index];
  const audioEl = guidedEl("guidedAudio");
  if (guided.lastTakeUrl) audioEl.src = guided.lastTakeUrl;
  else if (guided.status?.recorded[s.id]) {
    // the time stamp stops the browser playing an old take after a redo
    audioEl.src = `/guided/audio/${s.id}?profile_id=${encodeURIComponent(guided.profileId)}&t=${Date.now()}`;
  } else return;
  audioEl.play();
}

async function skipGuided() {
  if (guided.recorder && guided.recorder.state === "recording") return;
  const s = guided.sentences[guided.index];
  if (!guided.status.recorded[s.id]) {
    const formData = new FormData();
    formData.append("sentence_id", s.id);
    formData.append("profile_id", guided.profileId);
    const res = await fetch("/guided/skip", { method: "POST", body: formData });
    if (res.ok) { guided.status = await res.json(); showGuidedProgress(); }
  }
  goToGuided(guided.index + 1);
}

guidedEl("openGuidedBtn").onclick = openGuidedPanel;
guidedEl("guidedCloseBtn").onclick = closeGuidedPanel;
guidedEl("guidedRecordBtn").onclick = toggleGuidedRecording;
guidedEl("guidedPlayBtn").onclick = playGuided;
guidedEl("guidedSkipBtn").onclick = skipGuided;
guidedEl("guidedPrevBtn").onclick = () => goToGuided(guided.index - 1);

guidedEl("guidedExportBtn").onclick = () => {
  // the server replies with a zip "attachment", so this downloads instead of navigating away
  window.location.href = `/profiles/${encodeURIComponent(guided.profileId)}/export`;
};

guidedEl("guidedRefsBtn").onclick = async () => {
  const formData = new FormData();
  formData.append("profile_id", guided.profileId);
  const res = await fetch("/guided/references", { method: "POST", body: formData });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { guidedEl("guidedRefsStatus").textContent = "Error: " + (data.error || res.status); return; }
  guided.status = data;
  showGuidedProgress();
};

// keyboard shortcuts, only while the panel is open and you're not typing in a box
document.addEventListener("keydown", (e) => {
  const typing = e.target instanceof Element && e.target.matches("input, select, textarea");
  if (guidedEl("guidedPanel").hidden || typing) return;
  if (e.code === "Space") { e.preventDefault(); toggleGuidedRecording(); }
  else if (e.key === "ArrowRight") skipGuided();
  else if (e.key === "ArrowLeft") goToGuided(guided.index - 1);
  else if (e.key.toLowerCase() === "p") playGuided();
});

// ---------- UPLOAD OLD RECORDINGS ----------
// For someone who has already lost their voice. Everything happens in the browser:
//   1. decode the file (mp3, voice note, video...) with the Web Audio API
//   2. the caregiver drags two handles on the waveform to keep only the person's voice
//   3. that part is mixed to mono, resampled to 24 kHz and saved as a WAV
// Only that clean WAV is uploaded. Caregiver screen, so mouse-only is fine.
const UPLOAD_RATE = 24000;            // sample rate of the uploaded WAV (what the voice models use)
const MAX_CLIP_SECONDS = 120;         // same limits as the server
const MIN_CLIP_SECONDS = 1;
const MAX_FILE_MB = 200;              // bigger files (long videos) would use too much memory
const upload = {
  profileId: null,    // who the recordings belong to (fixed while the panel is open)
  items: [],          // one per chosen file
  audioCtx: null,     // used to decode and to preview
  previewing: null,   // the sound currently playing
};
const uploadEl = (id) => document.getElementById(id);

function openUploadPanel() {
  if (!activeProfile || activeProfile.voice_type !== "cloned") return;
  upload.profileId = activeProfile.id;
  upload.audioCtx = upload.audioCtx || new AudioContext();
  uploadEl("uploadPanel").hidden = false;
  uploadEl("uploadStatus").textContent = "";
  uploadEl("uploadPanel").scrollIntoView({ behavior: scrollMotion() });
  refreshSavedUploads();
}

function closeUploadPanel() {
  stopPreview();
  upload.items = [];
  uploadEl("uploadList").replaceChildren();
  uploadEl("uploadFiles").value = "";
  upload.profileId = null;
  uploadEl("uploadPanel").hidden = true;
  updateVoiceBankStatus();
}

uploadEl("openUploadBtn").onclick = openUploadPanel;
uploadEl("uploadCloseBtn").onclick = closeUploadPanel;

// ----- 1. choosing files -----
uploadEl("uploadFiles").onchange = (e) => {
  for (const file of e.target.files) addUploadItem(file);
  e.target.value = "";            // so the same file can be chosen again later
};

async function addUploadItem(file) {
  const item = { file, name: file.name, status: "decoding", start: 0, end: 0, include: true };
  upload.items.push(item);
  buildUploadCard(item);

  if (!hasAllowedExtension(file.name)) {
    return setItemError(item, "Not a supported file type. Use mp3, m4a, wav, ogg, opus, webm or mp4.");
  }
  if (file.size > MAX_FILE_MB * 1024 * 1024) {
    return setItemError(item, `File is larger than ${MAX_FILE_MB} MB. Cut it shorter first.`);
  }
  try {
    // the browser's own decoder: handles mp3, aac/m4a/mp4, ogg/opus, webm, wav
    item.buffer = await upload.audioCtx.decodeAudioData(await file.arrayBuffer());
  } catch (err) {
    console.warn("decode failed", file.name, err);
    return setItemError(item, "This browser couldn't read the audio in this file (unsupported codec or " +
                              "damaged file). Try Chrome, or convert it to mp3/wav first.");
  }
  const channels = [];
  for (let c = 0; c < item.buffer.numberOfChannels; c++) channels.push(item.buffer.getChannelData(c));
  item.mono = mixToMono(channels);
  item.rate = item.buffer.sampleRate;
  item.duration = item.buffer.duration;
  item.end = Math.min(item.duration, MAX_CLIP_SECONDS);
  item.chunks = speechChunks(item.mono, item.rate);        // for "how much is speech"
  item.status = item.duration < MIN_CLIP_SECONDS ? "error" : "ready";
  if (item.status === "error") return setItemError(item, "This recording is shorter than 1 second.");
  drawUploadCard(item);
}

// ----- 2. one card per file: waveform + trim handles + preview -----
function buildUploadCard(item) {
  const card = document.createElement("div");
  card.className = "upload-card";
  card.innerHTML = `
    <div class="controls-row">
      <strong class="upload-name"></strong>
      <span class="hint upload-state">reading the file...</span>
      <label><input type="checkbox" class="upload-include" checked /> include</label>
      <button class="upload-remove">Remove</button>
    </div>
    <canvas width="760" height="80" hidden></canvas>
    <div class="controls-row upload-trim-row" hidden>
      <button class="upload-preview">&#9654; Preview</button>
      <span class="upload-trim"></span>
    </div>`;
  card.querySelector(".upload-name").textContent = item.name;     // textContent: file names are not HTML
  card.querySelector(".upload-include").onchange = (e) => { item.include = e.target.checked; };
  card.querySelector(".upload-remove").onclick = () => {
    stopPreview();
    upload.items = upload.items.filter((i) => i !== item);
    card.remove();
  };
  card.querySelector(".upload-preview").onclick = () => previewItem(item);
  item.card = card;
  item.canvas = card.querySelector("canvas");
  attachTrimHandles(item);
  uploadEl("uploadList").appendChild(card);
}

function setItemError(item, message) {
  item.status = "error";
  item.card.classList.add("error");
  item.card.querySelector(".upload-state").textContent = message;
  item.card.querySelector(".upload-include").checked = item.include = false;
}

function drawUploadCard(item) {
  const card = item.card;
  card.querySelector("canvas").hidden = false;
  card.querySelector(".upload-trim-row").hidden = false;
  card.querySelector(".upload-state").textContent =
    item.status === "saved" ? "saved ✓" : `${item.duration.toFixed(1)} s recording`;
  drawWaveform(item);
  updateTrimText(item);
}

function drawWaveform(item) {
  const c = item.canvas, g = c.getContext("2d");
  const W = c.width, H = c.height;
  item.peaks = item.peaks || waveformPeaks(item.mono, W);
  const x = (t) => (t / item.duration) * W;
  g.clearRect(0, 0, W, H);
  // the part that will be KEPT is green, the rest is grey
  for (let px = 0; px < W; px++) {
    const t = (px / W) * item.duration;
    const [min, max] = item.peaks[px];
    g.fillStyle = t >= item.start && t <= item.end ? overlayColors.waveKept : overlayColors.waveCut;
    g.fillRect(px, H / 2 - max * H / 2, 1, Math.max(1, (max - min) * H / 2));
  }
  g.fillStyle = "rgba(128, 128, 128, 0.12)";       // shade the parts that will be cut
  g.fillRect(0, 0, x(item.start), H);
  g.fillRect(x(item.end), 0, W - x(item.end), H);
  // the two handles
  for (const t of [item.start, item.end]) {
    g.fillStyle = overlayColors.waveKept;
    g.fillRect(x(t) - 2, 0, 4, H);
    g.fillRect(x(t) - 7, H / 2 - 12, 14, 24);
  }
}

function updateTrimText(item) {
  const length = item.end - item.start;
  item.speech = speechSecondsInRange(item.chunks, item.start, item.end);
  const tooLong = length > MAX_CLIP_SECONDS ? ` <span class="warn-text">max ${MAX_CLIP_SECONDS} s per clip</span>` : "";
  item.card.querySelector(".upload-trim").innerHTML =
    `keep ${item.start.toFixed(1)} s → ${item.end.toFixed(1)} s ` +
    `(<b>${length.toFixed(1)} s</b>, about ${item.speech.toFixed(1)} s of speech)${tooLong}`;
}

// drag the handle nearest to where you press; clicks anywhere move the nearest handle
function attachTrimHandles(item) {
  const c = item.canvas;
  let dragging = null;
  const timeAt = (e) => {
    const r = c.getBoundingClientRect();
    return Math.max(0, Math.min(item.duration, ((e.clientX - r.left) / r.width) * item.duration));
  };
  const move = (e) => {
    const t = timeAt(e);
    if (dragging === "start") item.start = Math.min(t, item.end - MIN_CLIP_SECONDS);
    else item.end = Math.max(t, item.start + MIN_CLIP_SECONDS);
    item.start = Math.max(0, item.start);
    item.end = Math.min(item.duration, item.end);
    drawWaveform(item);
    updateTrimText(item);
  };
  c.onpointerdown = (e) => {
    if (item.status !== "ready") return;
    const t = timeAt(e);
    dragging = Math.abs(t - item.start) <= Math.abs(t - item.end) ? "start" : "end";
    try { c.setPointerCapture(e.pointerId); } catch { /* keeps working, just without capture */ }
    move(e);
  };
  c.onpointermove = (e) => { if (dragging) move(e); };
  c.onpointerup = () => { dragging = null; };
}

function stopPreview() {
  if (upload.previewing) { try { upload.previewing.stop(); } catch { /* already stopped */ } }
  upload.previewing = null;
}

function previewItem(item) {
  stopPreview();
  if (!item.buffer) return;
  upload.audioCtx.resume();
  const src = upload.audioCtx.createBufferSource();
  src.buffer = item.buffer;
  src.connect(upload.audioCtx.destination);
  src.start(0, item.start, item.end - item.start);      // play only the kept part
  upload.previewing = src;
}

// ----- 3. save: trim -> mono -> 24 kHz -> WAV -> upload -----
async function toUploadWav(item) {
  const part = trimSamples(item.mono, item.rate, item.start, item.end);
  // let the browser resample properly (it filters out frequencies that would distort)
  const frames = Math.max(1, Math.round((part.length / item.rate) * UPLOAD_RATE));
  const offline = new OfflineAudioContext(1, frames, UPLOAD_RATE);
  const buf = offline.createBuffer(1, part.length, item.rate);
  buf.copyToChannel(part, 0);
  const src = offline.createBufferSource();
  src.buffer = buf;
  src.connect(offline.destination);
  src.start();
  const rendered = await offline.startRendering();
  return new Blob([encodeWav16(rendered.getChannelData(0), UPLOAD_RATE)], { type: "audio/wav" });
}

uploadEl("uploadSaveBtn").onclick = async () => {
  const statusEl = uploadEl("uploadStatus");
  const consent = uploadEl("uploadConsent").checked;
  const consentName = uploadEl("uploadConsentName").value.trim();
  const toSave = upload.items.filter((i) => i.status === "ready" && i.include);

  if (!toSave.length) { statusEl.textContent = "Choose at least one readable file (and keep 'include' ticked)."; return; }
  if (!consent || !consentName) { statusEl.textContent = "Please tick the consent box and enter your name."; return; }
  const tooLong = toSave.find((i) => i.end - i.start > MAX_CLIP_SECONDS);
  if (tooLong) { statusEl.textContent = `"${tooLong.name}": keep ${MAX_CLIP_SECONDS} s or less.`; return; }

  uploadEl("uploadSaveBtn").disabled = true;
  let saved = 0;
  for (const item of toSave) {
    item.card.querySelector(".upload-state").textContent = "saving...";
    try {
      const formData = new FormData();
      formData.append("profile_id", upload.profileId);
      formData.append("original_name", item.name);
      formData.append("start_s", item.start.toFixed(3));
      formData.append("end_s", item.end.toFixed(3));
      formData.append("speech_s", item.speech.toFixed(2));
      formData.append("consent", "true");
      formData.append("consented_by", consentName);
      formData.append("file", await toUploadWav(item), "clip.wav");
      const res = await fetch("/uploads", { method: "POST", body: formData });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `server returned ${res.status}`);
      item.status = "saved";
      item.card.classList.add("saved");
      item.card.querySelector(".upload-state").textContent = "saved ✓";
      saved++;
      showSavedUploads(data);
    } catch (err) {
      item.card.querySelector(".upload-state").textContent = "not saved: " + err.message;
    }
  }
  uploadEl("uploadSaveBtn").disabled = false;
  statusEl.textContent = `Saved ${saved} of ${toSave.length} clip(s).`;
  if (saved === toSave.length) showToast(`Saved ${saved} recording(s).`);
  else showToast(`${toSave.length - saved} recording(s) could not be saved. See the list above.`, "error");
};

// ----- the clips already saved for this person -----
async function refreshSavedUploads() {
  const res = await fetch(`/uploads?profile_id=${encodeURIComponent(upload.profileId)}`);
  if (res.ok) showSavedUploads(await res.json());
}

function showSavedUploads(data) {
  const box = uploadEl("uploadSaved");
  box.replaceChildren(...data.clips.map((clip) => {
    const row = document.createElement("div");
    row.className = "upload-saved-row";
    row.textContent = `${clip.original_name} — ${clip.duration_s.toFixed(1)} s ` +
      `(about ${clip.speech_s.toFixed(1)} s of speech) · consent: ${clip.consented_by}, ` +
      `${new Date(clip.consented_at).toLocaleDateString()} `;      // shown in local time
    const del = document.createElement("button");
    del.textContent = "Delete";
    del.onclick = async () => {
      if (!confirm(`Delete "${clip.original_name}" from ${activeProfile?.name || "this user"}?`)) return;
      const r = await fetch(`/uploads/${clip.id}?profile_id=${encodeURIComponent(upload.profileId)}`,
                            { method: "DELETE" });
      if (r.ok) showSavedUploads(await r.json());
    };
    row.appendChild(del);
    return row;
  }));
  if (!data.clips.length) box.textContent = "None yet.";

  const totalEl = uploadEl("uploadTotal");
  totalEl.textContent = data.clips.length
    ? `${data.clips.length} clip(s), ${data.total_seconds.toFixed(1)} s in total, ` +
      `about ${data.total_speech_seconds.toFixed(1)} s of speech.`
    : "";
  if (data.clips.length && !data.enough_speech) {
    const warn = document.createElement("span");
    warn.className = "warn-text";
    warn.textContent = ` Only ${data.total_speech_seconds.toFixed(0)} s of speech: at least ` +
      `${data.min_total_speech_seconds} s is recommended, or the cloned voice may not sound like them.`;
    totalEl.appendChild(warn);
  }
}

// ---------- START ----------
// Runs last, so everything above is defined before we load the users.
loadProfiles();
updateSelectionStats();
