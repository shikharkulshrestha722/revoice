// Tests for metrics_format.js (metrics are made up, like a mocked Colab reply).
// Run from the project root:  node --test "frontend/tests/*.test.mjs"
import { test } from "node:test";
import assert from "node:assert/strict";
import { metricsLines, moreSimilarEngine, similarityValue, accentName } from "../metrics_format.js";

const full = {
  similarity: 0.734,
  reference: { pitch_hz: 121.5, pitch_range_hz: 40.2, words_per_sec: 2.6, accent: "england", accent_conf: 0.81 },
  output: { pitch_hz: 118.0, pitch_range_hz: 31.0, words_per_sec: 2.94, accent: "newzealand", accent_conf: 0.552 },
  accent_match: false,
};

test("full metrics give four readable lines", () => {
  assert.deepEqual(metricsLines(full), [
    "Similarity to the real voice: 0.73 (0-1)",
    "Pitch: ref 122 Hz (range 40) → output 118 Hz (range 31)",
    "Rate: ref 2.6 → output 2.9 words/s",
    "Accent (estimated): ref England 81% → output New Zealand 55% (different)",
  ]);
});

test("missing values show n/a instead of crashing", () => {
  const partial = { similarity: null, accent_match: null,
    reference: { pitch_hz: null, pitch_range_hz: null, words_per_sec: null, accent: null, accent_conf: null },
    output: { pitch_hz: 150, pitch_range_hz: null, words_per_sec: null, accent: "us", accent_conf: null } };
  assert.deepEqual(metricsLines(partial), [
    "Similarity to the real voice: n/a (0-1)",
    "Pitch: ref n/a → output 150 Hz",
    "Rate: ref n/a → output n/a words/s",
    "Accent (estimated): ref n/a → output US",
  ]);
});

test("stock voice (no reference) only describes the output", () => {
  const stock = { similarity: null, reference: null, accent_match: null,
    output: { pitch_hz: 200, pitch_range_hz: 50, words_per_sec: 3, accent: "us", accent_conf: 0.9 } };
  assert.deepEqual(metricsLines(stock).slice(1), [
    "Pitch: output 200 Hz (range 50)", "Rate: output 3.0 words/s", "Accent (estimated): output US 90%"]);
});

test("no metrics at all (older Colab, or metrics switched off) shows nothing", () => {
  assert.deepEqual(metricsLines(null), []);
  assert.deepEqual(metricsLines(undefined), []);
});

test("similarity is shown on a 0-1 scale", () => {
  assert.equal(similarityValue({ similarity: -0.2 }), 0);
  assert.equal(similarityValue({ similarity: 0.5 }), 0.5);
  assert.equal(similarityValue({ similarity: null }), null);
});

test("compare mode picks the engine with higher similarity", () => {
  assert.equal(moreSimilarEngine({ xtts: { similarity: 0.61 }, f5: { similarity: 0.74 } }), "f5");
  assert.equal(moreSimilarEngine({ xtts: { similarity: 0.8 }, f5: { similarity: 0.7 } }), "xtts");
});

test("no winner when a score is missing or it's a tie", () => {
  assert.equal(moreSimilarEngine({ xtts: { similarity: 0.6 }, f5: null }), null);
  assert.equal(moreSimilarEngine({ xtts: { similarity: 0.6 }, f5: { similarity: null } }), null);
  assert.equal(moreSimilarEngine({ xtts: { similarity: 0.6 }, f5: { similarity: 0.6 } }), null);
});

test("unknown accent labels are shown as they are", () => {
  assert.equal(accentName("hongkong"), "Hong Kong");
  assert.equal(accentName("martian"), "martian");
  assert.equal(accentName(null), "n/a");
});
