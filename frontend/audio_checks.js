// Flags recordings that are too quiet or clipping. Loudness uses the 90th-percentile
// 10 ms chunk level, so a single click at the start can't hide a quiet take.

export function analyzeLevels(samples, {
  chunkSize = 480,        // samples per chunk (10 ms at 48 kHz)
  quietLevel = 0.012,     // speech quieter than this (about -38 dB) = too quiet
  clipLevel = 0.99,       // a sample this close to the max counts as "clipped"
  maxClippedShare = 0.001 // more than 0.1% clipped samples = distorted
} = {}) {
  // RMS per chunk, and clipped samples
  const chunkLevels = [];
  let clipped = 0, peak = 0;
  for (let start = 0; start < samples.length; start += chunkSize) {
    const end = Math.min(start + chunkSize, samples.length);
    let sumSquares = 0;
    for (let i = start; i < end; i++) {
      const v = Math.abs(samples[i]);
      if (v > peak) peak = v;
      if (v >= clipLevel) clipped++;
      sumSquares += v * v;
    }
    chunkLevels.push(Math.sqrt(sumSquares / (end - start)));
  }

  // 90th-percentile chunk level = speech level
  chunkLevels.sort((a, b) => a - b);
  const speechLevel = chunkLevels.length
    ? chunkLevels[Math.min(chunkLevels.length - 1, Math.floor(chunkLevels.length * 0.9))]
    : 0;
  const clippedShare = clipped / (samples.length || 1);

  const problems = [];
  if (speechLevel < quietLevel) problems.push("too_quiet");
  if (clippedShare > maxClippedShare) problems.push("clipping");

  return { peak, speechLevel, clippedShare, problems };
}

export const PROBLEM_MESSAGES = {
  too_quiet: "Very quiet. Move closer to the microphone and redo.",
  clipping: "Too loud (distorted). Move back a little and redo.",
};
