// ============================================================
// ReVoice — Turning voice metrics into readable lines
// ============================================================
// Pure functions, no DOM (tested with Node).
// Colab measures every generated sentence (see Step 4a of the notebook):
//   similarity : how close the output voice is to the real voice (1 = identical)
//   pitch      : average pitch in Hz, and its range
//   rate       : words per second of actual speech
//   accent     : best-guess English accent + confidence (an ESTIMATE)
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

function accentText(stats) {
  if (!stats || !stats.accent) return "n/a";
  const pct = isNum(stats.accent_conf) ? ` ${Math.round(stats.accent_conf * 100)}%` : "";
  return `${accentName(stats.accent)}${pct}`;
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
export function metricsLines(metrics) {
  if (!metrics) return [];
  const ref = metrics.reference, out = metrics.output;
  const sim = similarityValue(metrics);
  const lines = [`Similarity to the real voice: ${sim === null ? "n/a" : sim.toFixed(2)} (0-1)`];
  lines.push(ref ? `Pitch: ref ${pitchText(ref)} → output ${pitchText(out)}`
                 : `Pitch: output ${pitchText(out)}`);
  lines.push(ref ? `Rate: ref ${rateText(ref)} → output ${rateText(out)} words/s`
                 : `Rate: output ${rateText(out)} words/s`);
  const match = metrics.accent_match === true ? " (same)" : metrics.accent_match === false ? " (different)" : "";
  lines.push(ref ? `Accent (estimated): ref ${accentText(ref)} → output ${accentText(out)}${match}`
                 : `Accent (estimated): output ${accentText(out)}`);
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
