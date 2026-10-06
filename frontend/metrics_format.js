// ============================================================
// ReVoice — Turning voice metrics into readable lines
// ============================================================
// Pure functions, no DOM (tested with Node).
// Colab measures every generated sentence (see Step 4a of the notebook):
//   similarity : how close the output voice is to the real voice (1 = identical)
//   pitch      : average pitch in Hz, and its range
//   rate       : words per second of actual speech
//   accent     : the 3 most likely English accents with RAW scores (-1..1, NOT %)
//                + seconds of speech; read with the rules below (same as backend/accent.py)
// Any of these can be null (not measured); we then show "n/a".
// ============================================================

const ACCENT_NAMES = {
  england: "England", us: "US", canada: "Canada", australia: "Australia", indian: "Indian",
  scotland: "Scotland", ireland: "Ireland", african: "African", malaysia: "Malaysia",
  newzealand: "New Zealand", southatlandtic: "South Atlantic", bermuda: "Bermuda",
  philippines: "Philippines", hongkong: "Hong Kong", wales: "Wales", singapore: "Singapore",
};

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

export function accentName(label) {
  return label ? (ACCENT_NAMES[label] || label) : "n/a";
}

// ---- reading the accent estimate (SAME rules as backend/accent.py) ----
// Under 3 s of speech: too short to say. Confidence = how far the top score is ahead
// of the second (under 0.05 low, under 0.15 medium, else high). These are judgement
// calls from our own clips, not calibrated probabilities, so no percentages are shown.
export const MIN_ACCENT_SPEECH_SECONDS = 3.0;
export const LOW_MARGIN = 0.05;
export const HIGH_MARGIN = 0.15;

export function judgeAccent(stats) {
  const scores = stats?.accent_scores;
  if (!Array.isArray(scores) || scores.length === 0) {
    return stats?.accent ? { status: "old_notebook", label: stats.accent } : { status: "not_measured" };
  }
  const top = scores.slice(0, 3).map(([label, score]) => [String(label), Number(score)]);
  const speech = stats.speech_seconds;
  if (isNum(speech) && speech < MIN_ACCENT_SPEECH_SECONDS) return { status: "too_short", speechSeconds: speech, top };
  const margin = top.length > 1 ? Math.round((top[0][1] - top[1][1]) * 1000) / 1000 : null;
  const confidence = margin === null || margin < LOW_MARGIN ? "low" : margin < HIGH_MARGIN ? "medium" : "high";
  return { status: "ok", label: top[0][0], top, margin, confidence, speechSeconds: speech };
}

// Same accent as the real voice? match: true/false, or null + reason when it can't be judged fairly
export function compareAccents(ref, out) {
  const problem = (j, who) =>
    j.status === "too_short" ? `${who} too short` : j.status !== "ok" ? `${who} not measured`
    : j.confidence === "low" ? `${who} accent unclear` : null;
  const reason = problem(ref, "reference") || problem(out, "output");
  return reason ? { match: null, reason } : { match: ref.label === out.label, reason: "" };
}

function accentText(j) {
  if (j.status === "not_measured") return "not measured";
  if (j.status === "old_notebook") return `${accentName(j.label)} (old Colab notebook: re-run Step 4a for scores)`;
  if (j.status === "too_short") return `n/a (too short: ${j.speechSeconds.toFixed(1)} s of speech)`;
  const scores = j.top.map(([label, score]) => `${accentName(label)} ${score.toFixed(2)}`).join(" · ");
  return `${scores} (${j.confidence} confidence)`;
}

// The accent lines under a player (only when "Show accent estimates" is ticked)
export function accentLines(metrics) {
  const out = judgeAccent(metrics.output);
  if (!metrics.reference) return [`Accent (estimate): output ${accentText(out)}`];   // stock voice
  const ref = judgeAccent(metrics.reference);
  const lines = ["Accent (estimate, raw scores -1 to 1, not %):",
                 `  real voice: ${accentText(ref)}`, `  output: ${accentText(out)}`];
  if (ref.status === "old_notebook" || out.status === "old_notebook") return lines;
  const { match, reason } = compareAccents(ref, out);
  lines.push(match === true ? "  → same accent kept"
    : match === false ? `  → accent changed: ${accentName(ref.label)} → ${accentName(out.label)}`
    : `  → change not judged (${reason})`);
  return lines;
}

function pitchText(stats) {
  if (!stats || !isNum(stats.pitch_hz)) return "n/a";
  const range = isNum(stats.pitch_range_hz) ? ` (range ${Math.round(stats.pitch_range_hz)})` : "";
  return `${Math.round(stats.pitch_hz)} Hz${range}`;
}

function rateText(stats) {
  return stats && isNum(stats.words_per_sec) ? stats.words_per_sec.toFixed(1) : "n/a";
}

// Similarity is a cosine (-1..1); for people it's shown on a 0-1 scale.
export function similarityValue(metrics) {
  const s = metrics?.similarity;
  return isNum(s) ? Math.max(0, Math.min(1, s)) : null;
}

// The lines shown under a player. Returns [] if there are no metrics at all.
// Accent lines only with { showAccent: true } (hidden by default: see the page setting).
export function metricsLines(metrics, { showAccent = false } = {}) {
  if (!metrics) return [];
  const ref = metrics.reference, out = metrics.output;
  const sim = similarityValue(metrics);
  const lines = [`Similarity to the real voice: ${sim === null ? "n/a" : sim.toFixed(2)} (0-1)`];
  lines.push(ref ? `Pitch: ref ${pitchText(ref)} → output ${pitchText(out)}`
                 : `Pitch: output ${pitchText(out)}`);
  lines.push(ref ? `Rate: ref ${rateText(ref)} → output ${rateText(out)} words/s`
                 : `Rate: output ${rateText(out)} words/s`);
  if (showAccent) lines.push(...accentLines(metrics));
  return lines;
}

// Compare mode: which engine sounded more like the person? null if it can't be decided.
export function moreSimilarEngine(metricsByEngine) {
  let best = null, bestValue = -1, tie = false;
  for (const [engine, metrics] of Object.entries(metricsByEngine)) {
    const v = similarityValue(metrics);
    if (v === null) continue;
    if (v > bestValue) { best = engine; bestValue = v; tie = false; }
    else if (v === bestValue) tie = true;
  }
  const measured = Object.values(metricsByEngine).filter((m) => similarityValue(m) !== null).length;
  return measured >= 2 && !tie ? best : null;
}

// ---- which recordings the voice was copied from (sent back by /speak) ----
// e.g. ["Voice copied from 1 clip (setting: Uploaded recordings only):",
//       "uploaded · Recording (2).m4a (#000001) · 18.0 s"]
const SOURCE_NAMES = {
  emotion_clips: "emotion recording", uploaded_recordings: "uploaded", guided_references: "guided take",
};

export function seconds(value) {
  return isNum(value) ? `${value.toFixed(1)} s` : "length unknown";
}

export function referenceLines(ref) {
  if (!ref || !Array.isArray(ref.clips)) return [];
  if (ref.error) return [ref.error];
  const n = ref.clips.length;
  const lines = [`Voice copied from ${n} clip${n === 1 ? "" : "s"}` +
                 (isNum(ref.total_seconds) && n > 1 ? `, ${seconds(ref.total_seconds)} in total` : "") +
                 ` (setting: ${ref.mode_label || ref.mode}):`];
  for (const c of ref.clips) lines.push(`${SOURCE_NAMES[c.source] || c.source} · ${c.name} · ${seconds(c.duration_s)}`);
  return lines;
}
