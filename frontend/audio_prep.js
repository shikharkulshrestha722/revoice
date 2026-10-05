// ============================================================
// ReVoice — Preparing uploaded recordings in the browser
// ============================================================
// Pure maths, no browser/DOM code (tested with Node).
// The browser decodes an old recording (mp3, WhatsApp voice note, video...),
// the caregiver trims it to just the person's voice, and these functions
// turn that part into a clean mono WAV file to upload.
// ============================================================

// Stereo (or more) -> mono, by averaging the channels
export function mixToMono(channels) {
  if (channels.length === 1) return Float32Array.from(channels[0]);
  const out = new Float32Array(channels[0].length);
  for (const ch of channels) for (let i = 0; i < out.length; i++) out[i] += ch[i] / channels.length;
  return out;
}

// The samples between start and end (seconds), kept inside the recording
export function trimSamples(samples, sampleRate, startSec, endSec) {
  const from = Math.max(0, Math.min(samples.length, Math.round(startSec * sampleRate)));
  const to = Math.max(from, Math.min(samples.length, Math.round(endSec * sampleRate)));
  return samples.slice(from, to);
}

// Mono 16-bit PCM WAV file (the simplest, most widely readable audio format)
export function encodeWav16(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset, s) => { for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i)); };
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);              // size of the format block
  view.setUint16(20, 1, true);               // 1 = plain PCM
  view.setUint16(22, 1, true);               // 1 channel (mono)
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);  // bytes per second
  view.setUint16(32, 2, true);               // bytes per sample
  view.setUint16(34, 16, true);              // bits per sample
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const v = Math.max(-1, Math.min(1, samples[i]));      // avoid wrap-around distortion
    view.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
  }
  return buffer;
}

// For drawing: the lowest and highest sample in each of `buckets` slices
export function waveformPeaks(samples, buckets) {
  const peaks = [];
  const size = samples.length / buckets;
  for (let b = 0; b < buckets; b++) {
    let min = 0, max = 0;
    const end = Math.min(samples.length, Math.floor((b + 1) * size));
    for (let i = Math.floor(b * size); i < end; i++) {
      if (samples[i] < min) min = samples[i];
      if (samples[i] > max) max = samples[i];
    }
    peaks.push([min, max]);
  }
  return peaks;
}

// ---- how much of a recording is actually speech ----
// The audio is cut into short chunks; a chunk counts as speech when it is clearly
// louder than the quiet parts of THIS recording (so it works for quiet phone
// recordings too). Done once per file, then any trim range can be measured instantly.
export function speechChunks(samples, sampleRate, chunkSec = 0.02) {
  const size = Math.max(1, Math.round(sampleRate * chunkSec));
  const levels = [];
  for (let start = 0; start < samples.length; start += size) {
    const end = Math.min(start + size, samples.length);
    let sum = 0;
    for (let i = start; i < end; i++) sum += samples[i] * samples[i];
    levels.push(Math.sqrt(sum / (end - start)));
  }
  const sorted = [...levels].sort((a, b) => a - b);
  const loud = sorted.length ? sorted[Math.floor(sorted.length * 0.95)] : 0;
  const threshold = Math.max(0.008, loud * 0.15);   // 0.008 = about -42 dB: below that is silence
  return { chunkSec: size / sampleRate, isSpeech: levels.map((l) => l >= threshold) };
}

export function speechSecondsInRange(chunks, startSec, endSec) {
  const from = Math.max(0, Math.floor(startSec / chunks.chunkSec));
  const to = Math.min(chunks.isSpeech.length, Math.ceil(endSec / chunks.chunkSec));
  let n = 0;
  for (let i = from; i < to; i++) if (chunks.isSpeech[i]) n++;
  return Math.round(n * chunks.chunkSec * 10) / 10;
}

// File types we accept (the server checks the same list)
export const UPLOAD_EXTENSIONS = [".mp3", ".m4a", ".wav", ".ogg", ".opus", ".webm", ".mp4"];

export function hasAllowedExtension(fileName) {
  const dot = (fileName || "").lastIndexOf(".");
  return dot >= 0 && UPLOAD_EXTENSIONS.includes(fileName.slice(dot).toLowerCase());
}
