// Tests for audio_prep.js.  Run from the project root:  node --test "frontend/tests/*.test.mjs"
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  mixToMono, trimSamples, encodeWav16, waveformPeaks, speechChunks, speechSecondsInRange, hasAllowedExtension,
} from "../audio_prep.js";

const SR = 8000;
const tone = (seconds, amp = 0.5) =>
  Float32Array.from({ length: Math.round(seconds * SR) }, (_, i) => amp * Math.sin(i / 3));
const join = (...parts) => {
  const out = new Float32Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
};

test("stereo is averaged to mono", () => {
  assert.deepEqual([...mixToMono([Float32Array.of(1, 0.5), Float32Array.of(0, -0.5)])], [0.5, 0]);
  assert.deepEqual([...mixToMono([Float32Array.of(0.25)])], [0.25]);
});

test("trim keeps exactly the chosen seconds, clamped to the recording", () => {
  const s = tone(10);
  assert.equal(trimSamples(s, SR, 2, 5).length, 3 * SR);
  assert.equal(trimSamples(s, SR, -3, 1).length, 1 * SR);       // start clamped to 0
  assert.equal(trimSamples(s, SR, 8, 99).length, 2 * SR);       // end clamped to the length
  assert.equal(trimSamples(s, SR, 5, 2).length, 0);             // backwards = nothing
});

test("WAV file has a correct mono 16-bit header and samples", () => {
  const wav = new DataView(encodeWav16(Float32Array.of(0, 1, -1, 2), 24000));
  const text = (o) => String.fromCharCode(...[0, 1, 2, 3].map((i) => wav.getUint8(o + i)));
  assert.equal(text(0), "RIFF");
  assert.equal(text(8), "WAVE");
  assert.equal(wav.getUint16(22, true), 1);          // mono
  assert.equal(wav.getUint32(24, true), 24000);      // sample rate
  assert.equal(wav.getUint16(34, true), 16);         // 16-bit
  assert.equal(wav.getUint32(40, true), 8);          // 4 samples x 2 bytes
  assert.equal(wav.byteLength, 44 + 8);
  assert.deepEqual([0, 1, 2, 3].map((i) => wav.getInt16(44 + i * 2, true)), [0, 32767, -32768, 32767]);  // 2 is clipped
});

test("waveform peaks give one min/max pair per bucket", () => {
  const peaks = waveformPeaks(Float32Array.of(0.1, -0.2, 0.5, -0.9), 2);
  assert.equal(peaks.length, 2);
  assert.deepEqual(peaks[1].map((v) => Math.round(v * 10) / 10), [-0.9, 0.5]);
});

test("speech time ignores silence", () => {
  // 3 s speech, 2 s silence, 1 s speech
  const s = join(tone(3), new Float32Array(2 * SR), tone(1));
  const chunks = speechChunks(s, SR);
  assert.equal(speechSecondsInRange(chunks, 0, 6), 4);
  assert.equal(speechSecondsInRange(chunks, 3, 5), 0);      // just the silent part
  assert.equal(speechSecondsInRange(chunks, 2, 6), 2);
});

test("a quiet (but clear) phone recording still counts as speech", () => {
  const s = join(tone(2, 0.03), new Float32Array(SR));      // quiet voice, then silence
  assert.equal(speechSecondsInRange(speechChunks(s, SR), 0, 3), 2);
});

test("background hiss is not speech", () => {
  const hiss = Float32Array.from({ length: 2 * SR }, () => (Math.random() - 0.5) * 0.004);
  assert.equal(speechSecondsInRange(speechChunks(hiss, SR), 0, 2), 0);
});

test("only the supported file types are accepted", () => {
  for (const ok of ["a.mp3", "b.M4A", "PTT-20240101-WA0001.opus", "c.ogg", "d.webm", "e.mp4", "f.wav"]) {
    assert.ok(hasAllowedExtension(ok), ok);
  }
  for (const bad of ["a.pdf", "b.flac", "noext", "", "mp3"]) assert.ok(!hasAllowedExtension(bad), bad);
});
