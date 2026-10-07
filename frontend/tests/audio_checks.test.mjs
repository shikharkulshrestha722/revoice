// Tests for audio_checks.js.
import { test } from "node:test";
import assert from "node:assert/strict";
import { analyzeLevels } from "../audio_checks.js";

// a sine wave at the given loudness (0..1), like a steady hum
function tone(amplitude, length = 24000) {
  return Float32Array.from({ length }, (_, i) => amplitude * Math.sin(i / 10));
}

test("normal speech level has no problems", () => {
  const r = analyzeLevels(tone(0.5));
  assert.deepEqual(r.problems, []);
  assert.ok(Math.abs(r.peak - 0.5) < 0.01);
  assert.ok(Math.abs(r.speechLevel - 0.5 / Math.SQRT2) < 0.02);   // RMS of a sine
});

test("a very quiet recording is flagged", () => {
  assert.deepEqual(analyzeLevels(tone(0.01)).problems, ["too_quiet"]);
});

test("a quiet recording with a loud click at the start is still flagged", () => {
  // real microphones often "pop" when recording starts
  const s = tone(0.005);
  for (let i = 0; i < 400; i++) s[i] = 0.5 * Math.sin(i);
  assert.deepEqual(analyzeLevels(s).problems, ["too_quiet"]);
});

test("speech with pauses (mostly silence) is not flagged as quiet", () => {
  // 30% talking, 70% silence: the louder parts are what count
  const s = new Float32Array(24000);
  for (let i = 0; i < 7200; i++) s[i] = 0.3 * Math.sin(i / 10);
  assert.deepEqual(analyzeLevels(s).problems, []);
});

test("silence is flagged as too quiet", () => {
  assert.deepEqual(analyzeLevels(new Float32Array(1000)).problems, ["too_quiet"]);
});

test("a clipped (too loud) recording is flagged", () => {
  // amplitude 3 squashed to the -1..1 range = lots of flat-topped samples
  const clipped = tone(3).map((v) => Math.max(-1, Math.min(1, v)));
  assert.deepEqual(analyzeLevels(clipped).problems, ["clipping"]);
});

test("one stray loud click is not counted as clipping", () => {
  const s = tone(0.4);
  s[100] = 1.0;
  assert.deepEqual(analyzeLevels(s).problems, []);
});

test("empty input does not crash", () => {
  const r = analyzeLevels(new Float32Array(0));
  assert.equal(r.peak, 0);
  assert.deepEqual(r.problems, ["too_quiet"]);
});
