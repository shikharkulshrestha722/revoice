// Row-column scanning with 1 or 2 switches. PURE: no DOM, no timers; the caller passes the time
// in, so every step can be tested. app.js draws the grid and calls tick() / press() / next().
//   1 switch ("auto" mode): the light moves by itself; SELECT picks.
//   2 switches ("step" mode): no timer; NEXT moves the light one step, SELECT picks.
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

// Step mode only: the last cell of every row goes back to choosing a row (a wrong row is never a trap).
export const BACK_CELL = { kind: "back", label: "↩ ROWS" };

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
    switches: Number(s.switches) === 2 ? 2 : 1,      // 1 = auto-scan, 2 = step scan (NEXT + SELECT)
  };
}

// 1 switch -> "auto" (timed) scanning, 2 switches -> "step" scanning.
export function scanModeFor(switches) {
  return switches === 2 ? "step" : "auto";
}

// Phases:
//   "idle"    scanning is off
//   "waiting" after a selection (or start/resume): nothing lit, presses ignored, so the user can relax
//             (auto mode only: in step mode nothing moves by itself, so there is nothing to wait for)
//   "rows"    rows light up one at a time; a press picks the lit row
//   "cols"    cells of that row light up one at a time; a press picks the lit cell.
//             Auto: one full pass with no press -> back to "rows". Step: the row ends with ↩ ROWS.
export class Scanner {
  constructor({ stepMs = 1000, startDelayMs = 1500, rows = buildScanRows(), mode = "auto" } = {}) {
    this.stepMs = stepMs;
    this.startDelayMs = startDelayMs;
    this.rows = rows;
    this.mode = mode;            // "auto" (1 switch) or "step" (2 switches)
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

  // 1 or 2 switches changed in the settings: start again from the top in the new mode.
  setMode(mode, now) {
    this.mode = mode === "step" ? "step" : "auto";
    if (this.phase !== "idle" && !this.paused) this.start(now);
  }

  // The rows as the page draws them: in step mode every row ends with ↩ ROWS.
  displayRows() {
    return this.rows.map((row) => (this.mode === "step" ? [...row, BACK_CELL] : row));
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
  // Step mode never moves by itself.
  tick(now) {
    if (this.mode === "step" || this.paused || this.phase === "idle" || now < this.nextAt) return false;
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

  // SELECT was pressed. Returns null (ignored), {type:"row"}, {type:"back"} or {type:"cell", cell}.
  press(now) {
    if (this.paused || this.phase === "idle" || this.phase === "waiting") return null;
    if (this.phase === "rows") {
      const col = this._nextCol(-1);
      if (col === -1) return null;                           // row emptied meanwhile
      this.phase = "cols";
      this.col = col;
      this.picked = null;
      this.nextAt = this.mode === "step" ? Infinity : now + this.stepMs;
      return { type: "row", row: this.row };
    }
    const cell = this._cells(this.row)[this.col];
    if (!usable(cell)) return null;
    if (cell.kind === "back") {                              // step mode: choose another row
      this._toRows(this.row);
      return { type: "back", row: this.row };
    }
    const result = { type: "cell", row: this.row, col: this.col, cell };
    this.picked = { row: this.row, col: this.col };
    this._wait(now);
    return result;
  }

  // NEXT was pressed (step mode only): the light moves one step, wrapping round.
  // Returns true if what is lit changed.
  next(now) {
    if (this.mode !== "step" || this.paused || this.phase === "idle" || this.phase === "waiting") return false;
    this.picked = null;
    if (this.phase === "rows") {
      this.row = this._nextRow(this.row);
    } else {
      const col = this._nextCol(this.col);
      this.col = col === -1 ? this._nextCol(-1) : col;      // after ↩ ROWS: back to the first cell
    }
    return true;
  }

  // What the page should show.
  view() {
    return { phase: this.phase, row: this.row, col: this.col, paused: this.paused, picked: this.picked,
             mode: this.mode };
  }

  // Auto: nothing lit for the start delay. Step: straight back to the first row (it doesn't move).
  _wait(now) {
    if (this.mode === "step") {
      this._toRows(this._nextRow(-1));
      this.nextAt = Infinity;
      return;
    }
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

  // cells of a row, with ↩ ROWS at the end in step mode
  _cells(row) {
    const cells = this.rows[row] || [];
    return this.mode === "step" ? [...cells, BACK_CELL] : cells;
  }

  // next usable cell in the current row after `from`, or -1 at the end of the pass (no wrap)
  _nextCol(from) {
    const cells = this._cells(this.row);
    for (let c = from + 1; c < cells.length; c++) if (usable(cells[c])) return c;
    return -1;
  }
}

// WebSocket message -> "DOWN" / "UP" (SELECT switch), "NEXT_DOWN" / "NEXT_UP" (NEXT switch),
// or null for anything else.
const SWITCH_STATES = new Set(["DOWN", "UP", "NEXT_DOWN", "NEXT_UP"]);

export function parseSwitchMessage(data) {
  try {
    const msg = typeof data === "string" ? JSON.parse(data) : data;
    return msg && SWITCH_STATES.has(msg.state) ? msg.state : null;
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

// Space while scanning. Wherever focus is (the Input dropdown, Pause, a slider...), Space is the
// switch: "press" = select, "block" = held-down repeat, just swallow it. "pass" = leave Space
// alone: scanning off, guided banking open (Space records there) or a caregiver typing in a
// text box. app.js calls preventDefault for anything but "pass", so Space never opens a
// select, clicks a focused button or scrolls the page.
const TEXT_INPUT_TYPES = new Set(["text", "search", "email", "url", "tel", "password", "number"]);

export function isTextEntry(target) {
  if (!target) return false;
  const tag = String(target.tagName || "").toUpperCase();
  if (tag === "TEXTAREA") return true;
  if (tag === "INPUT") return TEXT_INPUT_TYPES.has(String(target.type || "text").toLowerCase());
  return target.isContentEditable === true;
}

export function scanSpaceAction({ active, guidedOpen, code, repeat, target }) {
  const action = scanKeyAction({ active, guidedOpen, code, repeat, target, switches: 1 });
  return action === "select" ? "press" : action;
}

// All scanning keys: Space = SELECT; with 2 switches also Enter and → = NEXT (the wireless
// ESP32 switch types Space and Enter). Same rules as Space above. Returns "select", "next",
// "block" (a held key repeating: swallow it) or "pass" (leave the key alone).
const NEXT_KEYS = new Set(["Enter", "NumpadEnter", "ArrowRight"]);

export function scanKeyAction({ active, guidedOpen, code, repeat, target, switches = 1 }) {
  if (!active || guidedOpen || isTextEntry(target)) return "pass";
  const action = code === "Space" ? "select" : switches === 2 && NEXT_KEYS.has(code) ? "next" : null;
  if (!action) return "pass";
  return repeat ? "block" : action;           // holding a key down is ONE press
}
