// ============================================================
// ReVoice — Recording quality checks
// ============================================================
// Pure maths, no browser/DOM code (so it can be tested with Node).
// Given the audio samples of a recording (numbers between -1 and 1),
// spot the two problems that ruin voice-cloning training data:
//   - TOO QUIET: the voice barely reached the microphone
//   - CLIPPING:  too loud, the waveform got cut off (sounds distorted)
//
// Why not just look at the loudest sample? Because one click when the
// microphone starts would make a whisper-quiet take look fine. Instead we
// cut the audio into short chunks, measure each chunk's loudness, and use
// the loudness of the louder speech parts (90th percentile), which a single
// click can't change.
// ============================================================

export function analyzeLevels(samples, {
  chunkSize = 480,        // samples per chunk (10 ms at 48 kHz)
  quietLevel = 0.012,     // speech quieter than this (about -38 dB) = too quiet
  clipLevel = 0.99,       // a sample this close to the max counts as "clipped"
  maxClippedShare = 0.001 // more than 0.1% clipped samples = distorted
} = {}) {
  // loudness (RMS) of each chunk, and count clipped samples
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

  // the level that 90% of chunks are quieter than = "how loud the speech is"
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

// Friendly messages for each problem
export const PROBLEM_MESSAGES = {
  too_quiet: "Very quiet. Move closer to the microphone and redo.",
  clipping: "Too loud (distorted). Move back a little and redo.",
};
