// Single-switch row-column scanning. PURE: no DOM, no timers; the caller passes the time in,
// so every step can be tested. app.js draws the grid and calls tick() / press().
import { pointerKey, choosePrediction } from "./compose.js";

// Letters by how often they appear in English (E T A O I N first), 7 per row:
// common letters need fewer switch presses and less waiting.
export const SCAN_LETTER_ROWS = [
  ["E", "T", "A", "O", "I", "N", "S"],
  ["H", "R", "D", "L", "C", "U", "M"],
  ["W", "F", "G", "Y", "P", "B", "V"],
  ["K", "J", "X", "Q", "Z"],
];

// key cells use the SAME labels as the pointer grid ("SPACE", "DEL") so compose.js handles them
export const SCAN_CONTROLS = [
  { kind: "key", key: "SPACE", label: "SPACE" },
  { kind: "key", key: "DEL", label: "← DELETE" },
  { kind: "speak", label: "SPEAK" },
  { kind: "clear", label: "CLEAR" },
];

export const PREDICTION_COUNT = 3;

// Row 0 = the 3 predictions (reachable first), then the letters, then the controls.
// The shape never changes; an empty prediction is just skipped while scanning.
export function buildScanRows(predictions = []) {
  const predictionRow = Array.from({ length: PREDICTION_COUNT }, (_, index) =>
    ({ kind: "prediction", index, label: predictions[index] || "" }));
  const letterRows = SCAN_LETTER_ROWS.map((row) => row.map((key) => ({ kind: "key", key, label: key })));
  return [predictionRow, ...letterRows, SCAN_CONTROLS.map((c) => ({ ...c }))];
}

const usable = (cell) => Boolean(cell && cell.label);

// The selected cell changes the sentence with compose.js (same functions as the pointer grid).
// command: "speak" or "clear" for app.js to run (they don't change the sentence here).
export function applyScanSelection(sentence, cell) {
  if (!usable(cell)) return { sentence, command: null };
  if (cell.kind === "prediction") return { sentence: choosePrediction(sentence, cell.label), command: null };
  if (cell.kind === "key") return { sentence: pointerKey(sentence, cell.key), command: null };
  if (cell.kind === "speak" || cell.kind === "clear") return { sentence, command: cell.kind };
  return { sentence, command: null };
}

// Settings saved in localStorage: always clamped to safe ranges (a bad saved value can't break it).
export const SCAN_LIMITS = {
  stepMs: { min: 400, max: 2500, default: 1000 },
  startDelayMs: { min: 0, max: 5000, default: 1500 },
};

export function normalizeScanSettings(saved) {
  const s = saved && typeof saved === "object" ? saved : {};
  const clamp = (value, { min, max, default: d }) => {
    const n = Number(value);
    return Number.isFinite(n) ? Math.min(max, Math.max(min, Math.round(n))) : d;
  };
  return {
    stepMs: clamp(s.stepMs, SCAN_LIMITS.stepMs),
    startDelayMs: clamp(s.startDelayMs, SCAN_LIMITS.startDelayMs),
    clickSound: s.clickSound === true,
  };
}

// Phases:
//   "idle"    scanning is off
//   "waiting" after a selection (or start/resume): nothing lit, presses ignored, so the user can relax
//   "rows"    rows light up one at a time; a press picks the lit row
//   "cols"    cells of that row light up one at a time; a press picks the lit cell.
//             One full pass with no press -> back to "rows" (a wrong row is never a trap).
export class Scanner {
  constructor({ stepMs = 1000, startDelayMs = 1500, rows = buildScanRows() } = {}) {
    this.stepMs = stepMs;
    this.startDelayMs = startDelayMs;
    this.rows = rows;
    this.phase = "idle";
    this.row = -1;
    this.col = -1;
    this.paused = false;
    this.picked = null;          // {row, col} of the last selection, shown while "waiting"
    this.nextAt = Infinity;      // time of the next step
  }

  start(now) {
    this.picked = null;
    this._wait(now);
  }

  stop() {
    this.phase = "idle";
    this.row = this.col = -1;
    this.picked = null;
    this.nextAt = Infinity;
  }

  // Pausing stops everything; resuming starts again from the top after the start delay.
  setPaused(paused, now) {
    if (paused === this.paused) return;
    this.paused = paused;
    if (this.phase === "idle") return;
    if (paused) {
      this.row = this.col = -1;
      this.nextAt = Infinity;
    } else {
      this.picked = null;
      this._wait(now);
    }
  }

  // New predictions arrive while scanning: same shape, so the lit row stays put.
  // If the lit cell became empty, the column pass moves on at the next step.
  setRows(rows) {
    this.rows = rows;
  }

  // Advances at most one step. Returns true if what is lit changed (the page redraws).
  tick(now) {
    if (this.paused || this.phase === "idle" || now < this.nextAt) return false;
    if (this.phase === "waiting") {
      this.picked = null;
      this._toRows(this._nextRow(-1));
    } else if (this.phase === "rows") {
      this.row = this._nextRow(this.row);
    } else {
      const next = this._nextCol(this.col);
      if (next === -1) this._toRows(this._nextRow(-1));     // full pass, no press: back to the rows
      else this.col = next;
    }
    this.nextAt = now + this.stepMs;
    return true;
  }

  // The switch was pressed. Returns null (ignored), {type:"row"} or {type:"cell", cell}.
  press(now) {
    if (this.paused || this.phase === "idle" || this.phase === "waiting") return null;
    if (this.phase === "rows") {
      const col = this._nextCol(-1);
      if (col === -1) return null;                           // row emptied meanwhile
      this.phase = "cols";
      this.col = col;
      this.nextAt = now + this.stepMs;
      return { type: "row", row: this.row };
    }
    const cell = this.rows[this.row]?.[this.col];
    if (!usable(cell)) return null;
    const result = { type: "cell", row: this.row, col: this.col, cell };
    this.picked = { row: this.row, col: this.col };
    this._wait(now);
    return result;
  }

  // What the page should show.
  view() {
    return { phase: this.phase, row: this.row, col: this.col, paused: this.paused, picked: this.picked };
  }

  _wait(now) {
    this.phase = "waiting";
    this.row = this.col = -1;
    this.nextAt = now + this.startDelayMs;
  }

  _toRows(row) {
    this.phase = "rows";
    this.row = row;
    this.col = -1;
  }

  // next row (wrapping) that has something to pick; letter rows always do
  _nextRow(from) {
    const n = this.rows.length;
    for (let i = 1; i <= n; i++) {
      const r = (from + i + n) % n;
      if (this.rows[r].some(usable)) return r;
    }
    return -1;
  }

  // next usable cell in the current row after `from`, or -1 at the end of the pass (no wrap)
  _nextCol(from) {
    const cells = this.rows[this.row] || [];
    for (let c = from + 1; c < cells.length; c++) if (usable(cells[c])) return c;
    return -1;
  }
}

// WebSocket message -> "DOWN" / "UP", or null for anything else.
export function parseSwitchMessage(data) {
  try {
    const msg = typeof data === "string" ? JSON.parse(data) : data;
    return msg && (msg.state === "DOWN" || msg.state === "UP") ? msg.state : null;
  } catch {
    return null;
  }
}

// Text and colour of the "Switch:" pill, from /switch/status and whether the websocket is open.
export function switchPillText(status, socketOpen) {
  if (status && status.enabled && status.connected && socketOpen) {
    return { text: `Switch: connected on ${status.port}`, tone: "ok" };
  }
  if (status && status.enabled) {
    return { text: `Switch: not connected (${status.port}) — using spacebar`, tone: "warn" };
  }
  if (!status || !socketOpen) return { text: "Switch: not connected — using spacebar", tone: "warn" };
  return { text: "Switch: using spacebar", tone: "neutral" };
}
