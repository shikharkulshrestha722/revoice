// Upload conversion after the browser decodes a file: mono, trim, resample to 24 kHz, 16-bit WAV.

// averages channels to mono (same as ffmpeg -ac 1)
export function mixToMono(channels) {
  if (channels.length === 1) return Float32Array.from(channels[0]);
  const out = new Float32Array(channels[0].length);
  for (const ch of channels) for (let i = 0; i < out.length; i++) out[i] += ch[i] / channels.length;
  return out;
}

// samples between start and end seconds, clamped to the recording
export function trimSamples(samples, sampleRate, startSec, endSec) {
  const from = Math.max(0, Math.min(samples.length, Math.round(startSec * sampleRate)));
  const to = Math.max(from, Math.min(samples.length, Math.round(endSec * sampleRate)));
  return samples.slice(from, to);
}

// Windowed-sinc resampler. The low-pass removes frequencies the new rate can't hold,
// which would otherwise alias (e.g. 15 kHz -> 9 kHz at 24 kHz).
const SINC_ZEROS = 16;          // filter length: 16 sinc lobes each side (sharper = longer)
const TABLE_STEPS = 512;        // filter precomputed at 512 points per lobe (fast lookups)
const PASS_FRACTION = 0.92;     // keep everything below 92% of the new top frequency

function besselI0(x) {          // for the Kaiser window
  let sum = 1, term = 1;
  for (let k = 1; k < 30; k++) { term *= (x / (2 * k)) ** 2; sum += term; }
  return sum;
}

const SINC_TABLE = (() => {
  const beta = 8.6;             // Kaiser window shape: about 90 dB of stop-band rejection
  const n = SINC_ZEROS * TABLE_STEPS;
  const table = new Float64Array(n + 2);
  for (let i = 0; i <= n; i++) {
    const x = i / TABLE_STEPS;                     // distance in sinc lobes
    const sinc = x === 0 ? 1 : Math.sin(Math.PI * x) / (Math.PI * x);
    const w = besselI0(beta * Math.sqrt(Math.max(0, 1 - (x / SINC_ZEROS) ** 2))) / besselI0(beta);
    table[i] = sinc * w;
  }
  return table;
})();

export function resample(samples, fromRate, toRate) {
  if (fromRate === toRate) return Float32Array.from(samples);
  const outLength = Math.round((samples.length * toRate) / fromRate);
  const out = new Float32Array(outLength);
  // filter cutoff, as a fraction of half the INPUT rate
  const cutoff = (Math.min(fromRate, toRate) / fromRate) * PASS_FRACTION;
  const reach = SINC_ZEROS / cutoff;               // input samples the filter spans, each side
  const step = fromRate / toRate;
  const end = SINC_ZEROS * TABLE_STEPS;
  for (let j = 0; j < outLength; j++) {
    const centre = j * step;                       // output sample's position in the input
    const first = Math.max(0, Math.ceil(centre - reach));
    const last = Math.min(samples.length - 1, Math.floor(centre + reach));
    let sum = 0;
    for (let i = first; i <= last; i++) {
      const pos = Math.abs(i - centre) * cutoff * TABLE_STEPS;    // position in the table
      const k = Math.floor(pos);
      if (k >= end) continue;
      sum += samples[i] * (SINC_TABLE[k] + (SINC_TABLE[k + 1] - SINC_TABLE[k]) * (pos - k));
    }
    out[j] = sum * cutoff;                         // keeps the level unchanged
  }
  return out;
}

// channels -> mono -> trim -> resample -> 16-bit WAV; also returns the length and clipped-sample count
export function convertForUpload(channels, sampleRate, startSec, endSec, toRate) {
  const part = trimSamples(mixToMono(channels), sampleRate, startSec, endSec);
  const samples = resample(part, sampleRate, toRate);
  let clipped = 0;
  for (const v of samples) if (v > 1 || v < -1) clipped++;
  return { wav: encodeWav16(samples, toRate), clipped, seconds: samples.length / toRate };
}

// parses a 16-bit WAV into { sampleRate, channels, bitsPerSample, samples } (used by the tests)
export function readWav16(buffer) {
  const view = new DataView(buffer);
  const text = (o) => String.fromCharCode(...new Uint8Array(buffer, o, 4));
  if (text(0) !== "RIFF" || text(8) !== "WAVE") throw new Error("not a WAV file");
  let offset = 12, fmt = null;
  while (offset + 8 <= buffer.byteLength) {
    const id = text(offset), size = view.getUint32(offset + 4, true);
    if (id === "fmt ") {
      fmt = { channels: view.getUint16(offset + 10, true), sampleRate: view.getUint32(offset + 12, true),
              bitsPerSample: view.getUint16(offset + 22, true) };
    } else if (id === "data" && fmt) {
      const n = Math.floor(size / 2);
      const samples = new Float32Array(n);
      for (let i = 0; i < n; i++) samples[i] = view.getInt16(offset + 8 + i * 2, true) / 0x8000;
      return { ...fmt, samples };
    }
    offset += 8 + size + (size % 2);
  }
  throw new Error("WAV file has no audio data");
}

// mono 16-bit PCM WAV
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
    // round: setInt16 alone would truncate
    view.setInt16(44 + i * 2, Math.round(v < 0 ? v * 0x8000 : v * 0x7fff), true);
  }
  return buffer;
}

// min/max sample per bucket, for drawing the waveform
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

// Speech estimate: a chunk is speech when clearly louder than this recording's own
// quiet parts, so quiet phone recordings work too. Computed once per file.
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

// accepted file types (the server checks the same list)
export const UPLOAD_EXTENSIONS = [".mp3", ".m4a", ".wav", ".ogg", ".opus", ".webm", ".mp4"];

export function hasAllowedExtension(fileName) {
  const dot = (fileName || "").lastIndexOf(".");
  return dot >= 0 && UPLOAD_EXTENSIONS.includes(fileName.slice(dot).toLowerCase());
}
