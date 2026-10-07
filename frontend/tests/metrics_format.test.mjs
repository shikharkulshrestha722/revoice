// Tests for metrics_format.js, with mocked Colab metrics.
import { test } from "node:test";
import assert from "node:assert/strict";
import { metricsLines, moreSimilarEngine, similarityValue, accentName, judgeAccent, compareAccents,
         accentLines, MIN_ACCENT_SPEECH_SECONDS, LOW_MARGIN, HIGH_MARGIN } from "../metrics_format.js";

// Colab v8 format; accent scores are real ones from the project's recordings.
const UPLOAD = { speech_seconds: 17.7, accent_scores: [["indian", 0.613], ["us", 0.529], ["african", 0.328]] };
const MIC_CLIP = { speech_seconds: 19.1, accent_scores: [["indian", 0.641], ["us", 0.611], ["philippines", 0.331]] };
const CLEAR_CLIP = { speech_seconds: 13.2, accent_scores: [["indian", 0.631], ["us", 0.412], ["wales", 0.272]] };
const SHORT = { speech_seconds: 1.6, accent_scores: [["us", 0.65], ["indian", 0.411], ["african", 0.375]] };

const full = {
  similarity: 0.734,
  reference: { pitch_hz: 121.5, pitch_range_hz: 40.2, words_per_sec: 2.6, ...UPLOAD },
  output: { pitch_hz: 118.0, pitch_range_hz: 31.0, words_per_sec: 2.94, ...SHORT },
  accent_match: null,
};

test("by default (demo) there are no accent lines at all", () => {
  assert.deepEqual(metricsLines(full), [
    "Similarity to the real voice: 0.73 (0-1)",
    "Pitch: ref 122 Hz (range 40) → output 118 Hz (range 31)",
    "Rate: ref 2.6 → output 2.9 words/s",
  ]);
});

test("with accents shown: top 3 raw scores, confidence word, real voice next to output", () => {
  assert.deepEqual(metricsLines(full, { showAccent: true }).slice(3), [
    "Accent (estimate, raw scores -1 to 1, not %):",
    "  real voice: Indian 0.61 · US 0.53 · African 0.33 (medium confidence)",
    "  output: n/a (too short: 1.6 s of speech)",
    "  → change not judged (output too short)",
  ]);
  for (const line of accentLines(full)) assert.ok(!line.includes("%") || line.includes("not %"), line);
});

test("thresholds match backend/accent.py", () => {
  assert.deepEqual([MIN_ACCENT_SPEECH_SECONDS, LOW_MARGIN, HIGH_MARGIN], [3.0, 0.05, 0.15]);
});

test("confidence comes from the lead over second place", () => {
  assert.equal(judgeAccent(MIC_CLIP).confidence, "low");         // 0.030 ahead
  assert.equal(judgeAccent(UPLOAD).confidence, "medium");        // 0.084 ahead
  assert.equal(judgeAccent(CLEAR_CLIP).confidence, "high");      // 0.219 ahead
  assert.equal(judgeAccent({ ...SHORT, speech_seconds: 2.99 }).status, "too_short");
  assert.equal(judgeAccent({ ...SHORT, speech_seconds: 3.0 }).status, "ok");
});

test("an accent change is only claimed when the real voice is classified clearly", () => {
  const longUs = judgeAccent({ speech_seconds: 5, accent_scores: [["us", 0.7], ["indian", 0.5], ["x", 0.1]] });
  assert.deepEqual(compareAccents(judgeAccent(UPLOAD), longUs), { match: false, reason: "" });
  assert.deepEqual(compareAccents(judgeAccent(MIC_CLIP), longUs), { match: null, reason: "reference accent unclear" });
  const m = { reference: UPLOAD, output: { speech_seconds: 5, accent_scores: [["us", 0.7], ["indian", 0.5], ["x", 0.1]] } };
  assert.equal(accentLines(m).at(-1), "  → accent changed: Indian → US");
  assert.equal(accentLines({ reference: UPLOAD, output: UPLOAD }).at(-1), "  → same accent kept");
});

test("missing values show n/a instead of crashing", () => {
  const partial = { similarity: null, accent_match: null,
    reference: { pitch_hz: null, pitch_range_hz: null, words_per_sec: null, accent_scores: null },
    output: { pitch_hz: 150, pitch_range_hz: null, words_per_sec: null, accent_scores: null } };
  assert.deepEqual(metricsLines(partial, { showAccent: true }), [
    "Similarity to the real voice: n/a (0-1)",
    "Pitch: ref n/a → output 150 Hz",
    "Rate: ref n/a → output n/a words/s",
    "Accent (estimate, raw scores -1 to 1, not %):",
    "  real voice: not measured",
    "  output: not measured",
    "  → change not judged (reference not measured)",
  ]);
});

test("older Colab notebook (label + percentage): label only, percentage never shown", () => {
  const old = { reference: { accent: "indian", accent_conf: 0.93 }, output: { accent: "us", accent_conf: 1.0 } };
  assert.deepEqual(accentLines(old), [
    "Accent (estimate, raw scores -1 to 1, not %):",
    "  real voice: Indian (old Colab notebook: re-run Step 4a for scores)",
    "  output: US (old Colab notebook: re-run Step 4a for scores)",
  ]);
});

test("stock voice (no reference) only describes the output", () => {
  const stock = { similarity: null, reference: null, accent_match: null,
    output: { pitch_hz: 200, pitch_range_hz: 50, words_per_sec: 3, ...CLEAR_CLIP } };
  assert.deepEqual(metricsLines(stock, { showAccent: true }).slice(1), [
    "Pitch: output 200 Hz (range 50)", "Rate: output 3.0 words/s",
    "Accent (estimate): output Indian 0.63 · US 0.41 · Wales 0.27 (high confidence)"]);
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

test("reference lines say exactly which clips were sent", async () => {
  const { referenceLines } = await import("../metrics_format.js");
  assert.deepEqual(referenceLines({
    mode: "uploads", mode_label: "Uploaded recordings only", source: "uploaded_recordings", total_seconds: 17.99,
    clips: [{ source: "uploaded_recordings", name: "Recording (2).m4a (#000001)", duration_s: 17.99 }],
  }), ["Voice copied from 1 clip (setting: Uploaded recordings only):",
       "uploaded · Recording (2).m4a (#000001) · 18.0 s"]);

  assert.deepEqual(referenceLines({
    mode: "auto", mode_label: "Auto", total_seconds: 39.8,
    clips: [{ source: "emotion_clips", name: "neutral/000001.webm", duration_s: 19.92 },
            { source: "emotion_clips", name: "neutral/000002.webm", duration_s: null }],
  }), ["Voice copied from 2 clips, 39.8 s in total (setting: Auto):",
       "emotion recording · neutral/000001.webm · 19.9 s",
       "emotion recording · neutral/000002.webm · length unknown"]);

  assert.deepEqual(referenceLines(undefined), []);                  // stock voice / old backend
  assert.deepEqual(referenceLines({ clips: [], error: "No uploads." }), ["No uploads."]);
});
