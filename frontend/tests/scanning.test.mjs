// Row-column scanning state machine: time is passed in, so no real waiting.
import test from "node:test";
import assert from "node:assert/strict";
import { Scanner, buildScanRows, applyScanSelection, normalizeScanSettings, parseSwitchMessage,
         switchPillText, SCAN_LETTER_ROWS, scanSpaceAction, isTextEntry } from "../scanning.js";
import { emptySentence } from "../compose.js";
import { selectionSavings } from "../selection_stats.js";

const STEP = 1000, DELAY = 1500;
const PREDICTIONS = ["I NEED WATER", "I NEED HELP", "I AM COLD"];
const ROW = { predictions: 0, etaoins: 1, hrdlcum: 2, wfgypbv: 3, kjxqz: 4, controls: 5 };

// started scanner, already past the start delay: row 0 is lit at time `t`
function ready(predictions = PREDICTIONS) {
  const s = new Scanner({ stepMs: STEP, startDelayMs: DELAY, rows: buildScanRows(predictions) });
  s.start(0);
  s.tick(DELAY);
  return { s, t: DELAY };
}

// lets n steps pass; returns the new time
function steps(s, t, n) {
  for (let i = 0; i < n; i++) { t += STEP; s.tick(t); }
  return t;
}

// row r, then column c: returns the picked cell
function pick(s, t, r, c) {
  t = steps(s, t, (r - s.view().row + s.rows.length) % s.rows.length);
  assert.equal(s.press(t).type, "row");
  t = steps(s, t, c);
  const res = s.press(t);
  assert.equal(res.type, "cell");
  return { cell: res.cell, t };
}

test("layout: predictions first, letters by frequency (all 26 once), controls last", () => {
  const rows = buildScanRows(PREDICTIONS);
  assert.deepEqual(rows[0].map((c) => c.label), PREDICTIONS);
  assert.deepEqual(rows[1].slice(0, 6).map((c) => c.key), ["E", "T", "A", "O", "I", "N"]);
  const letters = SCAN_LETTER_ROWS.flat();
  assert.equal(letters.length, 26);
  assert.equal(new Set(letters).size, 26);
  assert.deepEqual(rows.at(-1).map((c) => c.kind), ["key", "key", "speak", "clear"]);
  assert.deepEqual(rows.at(-1).slice(0, 2).map((c) => c.key), ["SPACE", "DEL"]);
});

test("start delay: nothing is lit and presses are ignored until it ends", () => {
  const s = new Scanner({ stepMs: STEP, startDelayMs: DELAY, rows: buildScanRows(PREDICTIONS) });
  s.start(0);
  assert.equal(s.view().phase, "waiting");
  assert.equal(s.press(500), null);
  assert.equal(s.tick(DELAY - 1), false);
  assert.equal(s.tick(DELAY), true);
  assert.deepEqual([s.view().phase, s.view().row], ["rows", 0]);
});

test("rows light up one at a time and wrap around", () => {
  const { s, t } = ready();
  const seen = [s.view().row];
  let now = t;
  for (let i = 0; i < 6; i++) { now += STEP; s.tick(now); seen.push(s.view().row); }
  assert.deepEqual(seen, [0, 1, 2, 3, 4, 5, 0]);
  assert.equal(s.tick(now + STEP - 1), false, "no step before stepMs has passed");
});

test("row -> column -> select picks the lit cell, then waits again", () => {
  const { s, t } = ready();
  let now = steps(s, t, 1);                         // row 1 lit (E T A O I N S)
  assert.deepEqual(s.press(now), { type: "row", row: 1 });
  assert.deepEqual([s.view().phase, s.view().row, s.view().col], ["cols", 1, 0]);
  now = steps(s, now, 2);                           // E -> T -> A
  const res = s.press(now);
  assert.equal(res.cell.key, "A");
  assert.deepEqual(s.view().picked, { row: 1, col: 2 });
  assert.equal(s.view().phase, "waiting");
  assert.equal(s.press(now + 100), null, "a second press right after is ignored");
  s.tick(now + DELAY);
  assert.deepEqual([s.view().phase, s.view().row, s.view().picked], ["rows", 0, null]);
});

test("no press during one full column pass -> back to row scanning", () => {
  const { s, t } = ready();
  let now = steps(s, t, ROW.kjxqz);
  s.press(now);                                     // K J X Q Z: 5 cells
  now = steps(s, now, 4);
  assert.equal(s.view().col, 4);                   // Z, the last one
  now = steps(s, now, 1);
  assert.deepEqual([s.view().phase, s.view().row, s.view().col], ["rows", 0, -1]);
});

test("empty predictions are skipped (row and cells)", () => {
  const none = ready([]);
  assert.equal(none.s.view().row, ROW.etaoins, "no predictions: start at the letters");
  const partial = ready(["", "YES PLEASE", ""]);
  assert.equal(partial.s.press(partial.t).type, "row");
  assert.equal(partial.s.view().col, 1, "first cell with a phrase");
  steps(partial.s, partial.t, 1);
  assert.equal(partial.s.view().phase, "rows", "only one phrase: one step and the pass is over");
});

test("prediction row: picking a phrase replaces the sentence with one selection", () => {
  const { s, t } = ready();
  const { cell } = pick(s, t, ROW.predictions, 1);
  const { sentence, command } = applyScanSelection({ text: "I NE", selections: 4, typed: false }, cell);
  assert.equal(command, null);
  assert.deepEqual(sentence, { text: "I NEED HELP ", selections: 5, typed: false });
});

test("controls: SPACE, DELETE change the sentence; SPEAK and CLEAR are commands", () => {
  const rows = buildScanRows();
  const [space, del, speak, clear] = rows.at(-1);
  let st = { text: "HI", selections: 2, typed: false };
  st = applyScanSelection(st, space).sentence;
  assert.deepEqual(st, { text: "HI ", selections: 3, typed: false });
  st = applyScanSelection(st, del).sentence;
  assert.deepEqual(st, { text: "HI", selections: 4, typed: false });
  assert.deepEqual(applyScanSelection(st, speak), { sentence: st, command: "speak" });
  assert.deepEqual(applyScanSelection(st, clear), { sentence: st, command: "clear" });
});

test("spelling with the scanner counts selections like the pointer grid", () => {
  const { s, t } = ready();
  let sentence = emptySentence(), now = t;
  for (const [r, c] of [[ROW.hrdlcum, 0], [ROW.etaoins, 4]]) {       // H, I
    const res = pick(s, now, r, c);
    sentence = applyScanSelection(sentence, res.cell).sentence;
    now = res.t + DELAY;
    s.tick(now);
  }
  assert.deepEqual(sentence, { text: "HI", selections: 2, typed: false });
  assert.equal(selectionSavings(sentence.selections, sentence.text).savedPct, 0);
});

test("pause stops scanning; resume starts from the top after the start delay", () => {
  const { s, t } = ready();
  let now = steps(s, t, 2);
  s.press(now);                                     // in the middle of a column pass
  s.setPaused(true, now);
  assert.equal(s.view().paused, true);
  assert.equal(s.tick(now + 10 * STEP), false);
  assert.equal(s.press(now + 10 * STEP), null);
  now += 20 * STEP;
  s.setPaused(false, now);
  assert.equal(s.view().phase, "waiting");
  assert.equal(s.press(now + 1), null);
  s.tick(now + DELAY);
  assert.deepEqual([s.view().phase, s.view().row], ["rows", 0]);
});

test("stop turns everything off", () => {
  const { s, t } = ready();
  s.stop();
  assert.equal(s.view().phase, "idle");
  assert.equal(s.tick(t + 5 * STEP), false);
  assert.equal(s.press(t + 5 * STEP), null);
});

test("new predictions while scanning keep the lit row; an emptied lit cell can't be picked", () => {
  const { s, t } = ready();
  s.press(t);                                       // prediction row, first phrase lit
  s.setRows(buildScanRows([]));
  assert.equal(s.press(t + 10), null);
  s.tick(t + STEP);
  assert.equal(s.view().phase, "rows", "nothing left in that row: back to the rows");
});

test("speed and start delay changes apply from the next step", () => {
  const { s, t } = ready();
  s.stepMs = 500;
  s.tick(t + STEP);                                 // the step already planned
  assert.equal(s.tick(t + STEP + 499), false);
  assert.equal(s.tick(t + STEP + 500), true);
});

test("settings are clamped and default safely", () => {
  assert.deepEqual(normalizeScanSettings(null), { stepMs: 1000, startDelayMs: 1500, clickSound: false, switches: 1 });
  assert.deepEqual(normalizeScanSettings({ stepMs: 50, startDelayMs: 99999, clickSound: true }),
                   { stepMs: 400, startDelayMs: 5000, clickSound: true, switches: 1 });
  assert.deepEqual(normalizeScanSettings({ stepMs: "abc", startDelayMs: "800", clickSound: "yes" }),
                   { stepMs: 1000, startDelayMs: 800, clickSound: false, switches: 1 });
  assert.equal(normalizeScanSettings({ stepMs: 2600 }).stepMs, 2500);
});

test("switch messages: DOWN / UP (SELECT) and NEXT_DOWN / NEXT_UP (NEXT) only", () => {
  assert.equal(parseSwitchMessage('{"state":"DOWN","t":1.5}'), "DOWN");
  assert.equal(parseSwitchMessage('{"state":"UP","t":1.5}'), "UP");
  assert.equal(parseSwitchMessage('{"state":"NEXT_DOWN","t":1.5}'), "NEXT_DOWN");
  assert.equal(parseSwitchMessage('{"state":"NEXT_UP","t":1.5}'), "NEXT_UP");
  assert.equal(parseSwitchMessage('{"state":"NEXT"}'), null);
  assert.equal(parseSwitchMessage('{"state":"down"}'), null);
  assert.equal(parseSwitchMessage("not json"), null);
  assert.equal(parseSwitchMessage("null"), null);
});

test("status pill text", () => {
  const on = { enabled: true, connected: true, port: "COM3" };
  assert.deepEqual(switchPillText(on, true), { text: "Switch: connected on COM3", tone: "ok" });
  assert.equal(switchPillText({ ...on, connected: false }, true).text,
               "Switch: not connected (COM3) — using spacebar");
  assert.equal(switchPillText(on, false).tone, "warn", "server link lost");
  assert.deepEqual(switchPillText({ enabled: false }, true), { text: "Switch: using spacebar", tone: "neutral" });
  assert.equal(switchPillText(null, false).text, "Switch: not connected — using spacebar");
});

// Space while scanning: stand-ins for the focused element (tagName/type/isContentEditable)
const focused = (tagName, extra = {}) => ({ tagName, ...extra });
const space = (target, extra = {}) => ({ active: true, guidedOpen: false, code: "Space", repeat: false, target, ...extra });

test("Space is the switch even when a control has focus (select, button, slider, checkbox)", () => {
  for (const target of [focused("SELECT"), focused("BUTTON"), focused("INPUT", { type: "range" }),
                        focused("INPUT", { type: "checkbox" }), focused("DIV"), focused("BODY"), null]) {
    assert.equal(scanSpaceAction(space(target)), "press", target?.tagName ?? "nothing focused");
  }
});

test("Space types normally in text boxes so caregivers can type", () => {
  for (const target of [focused("TEXTAREA"), focused("INPUT"), focused("INPUT", { type: "text" }),
                        focused("INPUT", { type: "search" }), focused("DIV", { isContentEditable: true })]) {
    assert.equal(isTextEntry(target), true);
    assert.equal(scanSpaceAction(space(target)), "pass");
  }
  assert.equal(isTextEntry(focused("SELECT")), false);
  assert.equal(isTextEntry(focused("INPUT", { type: "range" })), false);
});

test("held-down Space is one press; other keys, scanning off and guided banking are left alone", () => {
  assert.equal(scanSpaceAction(space(focused("BUTTON"), { repeat: true })), "block");   // swallowed, no 2nd press
  assert.equal(scanSpaceAction(space(focused("SELECT"), { code: "Enter" })), "pass");
  assert.equal(scanSpaceAction(space(focused("SELECT"), { active: false })), "pass"); // pointer modes unchanged
  assert.equal(scanSpaceAction(space(focused("BODY"), { guidedOpen: true })), "pass"); // Space records there
});

test("Space while paused still reaches the scanner, which ignores it (Pause behaviour unchanged)", () => {
  const { s } = ready();
  s.setPaused(true, 5000);
  assert.equal(scanSpaceAction(space(focused("BUTTON"))), "press");
  assert.equal(s.press(5100), null);
});
