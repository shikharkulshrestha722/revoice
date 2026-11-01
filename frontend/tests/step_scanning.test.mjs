// Two-switch step scanning: NEXT moves the light one step, SELECT picks; no timer.
import test from "node:test";
import assert from "node:assert/strict";
import { Scanner, buildScanRows, applyScanSelection, normalizeScanSettings, scanModeFor,
         scanKeyAction, scanSpaceAction, BACK_CELL } from "../scanning.js";
import { emptySentence } from "../compose.js";

const PREDICTIONS = ["I NEED WATER", "I NEED HELP", "I AM COLD"];

function stepScanner(predictions = PREDICTIONS) {
  const s = new Scanner({ stepMs: 1000, startDelayMs: 1500, rows: buildScanRows(predictions), mode: "step" });
  s.start(0);
  return s;
}

test("1 switch = auto-scan, 2 switches = step scan; the setting is kept and defaults to 1", () => {
  assert.equal(scanModeFor(1), "auto");
  assert.equal(scanModeFor(2), "step");
  assert.equal(normalizeScanSettings({ switches: 2 }).switches, 2);
  assert.equal(normalizeScanSettings({ switches: "2" }).switches, 2);
  assert.equal(normalizeScanSettings({ switches: 3 }).switches, 1);
  assert.equal(normalizeScanSettings(undefined).switches, 1);
});

test("step scan starts on the first row at once and never moves by itself", () => {
  const s = stepScanner();
  assert.deepEqual([s.view().phase, s.view().row], ["rows", 0]);
  assert.equal(s.tick(100000), false);
  assert.equal(s.view().row, 0);
});

test("NEXT moves one row; SELECT picks it; NEXT moves along the cells; SELECT picks a letter", () => {
  const s = stepScanner();
  assert.equal(s.next(0), true);                      // row 1: E T A O I N S
  assert.equal(s.press(0).type, "row");
  assert.deepEqual([s.view().phase, s.view().col], ["cols", 0]);
  s.next(0); s.next(0);                               // E -> T -> A
  const picked = s.press(0);
  assert.equal(picked.cell.key, "A");
  assert.equal(applyScanSelection(emptySentence(), picked.cell).sentence.text, "A");
  // straight back to the first row (no waiting: nothing moves by itself)
  assert.deepEqual([s.view().phase, s.view().row], ["rows", 0]);
});

test("every row ends with ↩ ROWS, which goes back to choosing a row (same row lit)", () => {
  const s = stepScanner();
  s.next(0); s.next(0);                               // row 2: H R D L C U M
  s.press(0);
  for (let i = 0; i < 7; i++) s.next(0);              // past the 7 letters
  assert.equal(s.displayRows()[2][s.view().col], BACK_CELL);
  assert.deepEqual(s.press(0), { type: "back", row: 2 });
  assert.deepEqual([s.view().phase, s.view().row], ["rows", 2]);
});

test("NEXT after ↩ ROWS wraps to the first cell; rows wrap too", () => {
  const s = stepScanner();
  s.press(0);                                         // predictions row
  for (let i = 0; i < 3; i++) s.next(0);              // 3 predictions -> ↩ ROWS
  assert.equal(s.view().col, 3);
  s.next(0);
  assert.equal(s.view().col, 0);
  s.press(0);                                         // picks prediction 0
  for (let i = 0; i < 6; i++) s.next(0);              // 6 rows: back to row 0
  assert.equal(s.view().row, 0);
});

test("empty predictions are skipped by NEXT in step scan too", () => {
  const s = stepScanner(["ONLY ONE", "", ""]);
  s.press(0);
  assert.equal(s.view().col, 0);
  s.next(0);
  assert.equal(s.displayRows()[0][s.view().col], BACK_CELL);    // straight to ↩ ROWS
});

test("NEXT does nothing in auto mode, while paused, or with scanning off", () => {
  const auto = new Scanner({ rows: buildScanRows(PREDICTIONS) });
  auto.start(0);
  auto.tick(1500);
  assert.equal(auto.next(1500), false);
  const s = stepScanner();
  s.setPaused(true, 0);
  assert.equal(s.next(0), false);
  s.stop();
  assert.equal(s.next(0), false);
});

test("resume after a pause (e.g. SPEAK) starts on the first row again", () => {
  const s = stepScanner();
  s.next(0); s.next(0);
  s.setPaused(true, 0);
  s.setPaused(false, 10);
  assert.deepEqual([s.view().phase, s.view().row], ["rows", 0]);
});

test("switching mode restarts from the top; ↩ ROWS only in step mode", () => {
  const s = new Scanner({ rows: buildScanRows(PREDICTIONS) });
  s.start(0);
  assert.equal(s.displayRows()[1].length, 7);
  s.setMode("step", 5);
  assert.deepEqual([s.view().phase, s.view().row, s.view().mode], ["rows", 0, "step"]);
  assert.equal(s.displayRows()[1].length, 8);
  s.setMode("auto", 5);
  assert.equal(s.view().phase, "waiting");
  assert.equal(s.rows[1].length, 7, "the real rows are never changed");
});

const button = { tagName: "BUTTON" };
const textBox = { tagName: "TEXTAREA" };

test("keys: Space = SELECT; with 2 switches Enter, keypad Enter and → = NEXT", () => {
  const key = (code, extra = {}) => scanKeyAction({ active: true, guidedOpen: false, code, repeat: false,
                                                    target: button, switches: 2, ...extra });
  assert.equal(key("Space"), "select");
  assert.equal(key("Enter"), "next");
  assert.equal(key("NumpadEnter"), "next");
  assert.equal(key("ArrowRight"), "next");
  assert.equal(key("ArrowLeft"), "pass");
  assert.equal(key("Enter", { repeat: true }), "block");       // held key = one press
});

test("keys: with 1 switch Enter and → are left alone; text boxes and guided banking always are", () => {
  const key = (code, extra = {}) => scanKeyAction({ active: true, guidedOpen: false, code, repeat: false,
                                                    target: button, switches: 1, ...extra });
  assert.equal(key("Enter"), "pass");
  assert.equal(key("ArrowRight"), "pass");
  assert.equal(key("Space"), "select");
  assert.equal(key("Enter", { switches: 2, target: textBox }), "pass");
  assert.equal(key("Enter", { switches: 2, guidedOpen: true }), "pass");
  assert.equal(key("Enter", { switches: 2, active: false }), "pass");
});

test("scanSpaceAction keeps its old answers", () => {
  const base = { active: true, guidedOpen: false, code: "Space", repeat: false, target: button };
  assert.equal(scanSpaceAction(base), "press");
  assert.equal(scanSpaceAction({ ...base, repeat: true }), "block");
  assert.equal(scanSpaceAction({ ...base, code: "Enter" }), "pass");
});
