// Checks that the upload conversion does NOT change the voice:
// (decoded audio) -> mono -> trim -> 24 kHz -> 16-bit WAV.
// The test signals are maths formulas, so we can also work out the PERFECT 24 kHz
// version and compare the converted WAV with it sample by sample.
// Run from the project root:  node --test "frontend/tests/*.test.mjs"
import { test } from "node:test";
import assert from "node:assert/strict";
import { convertForUpload, encodeWav16, readWav16, resample } from "../audio_prep.js";

const OUT_RATE = 24000;      // what app.js uploads (UPLOAD_RATE)

// a signal given as a formula of time (seconds), sampled at any rate
const sampled = (fn, rate, seconds) => Float32Array.from({ length: Math.round(rate * seconds) }, (_, i) => fn(i / rate));

const tone440 = (t) => 0.5 * Math.sin(2 * Math.PI * 440 * t);

// "speech-like": a 120 Hz voice with harmonics up to 5 kHz (louder around 500 Hz and
// 1.5 kHz, like vowel formants), a slow pitch wobble, and 4 "syllables" per second
// with short gaps between them
const F0 = 120;
function speechLike(t) {
  // phase of a pitch that wobbles +-3 Hz around 120 Hz, 0.7 times a second
  const phase = 2 * Math.PI * (F0 * t - (3 / (2 * Math.PI * 0.7)) * Math.cos(2 * Math.PI * 0.7 * t));
  let v = 0;
  for (let h = 1; h * F0 <= 5000; h++) {
    const f = h * F0;
    const formants = Math.exp(-(((f - 500) / 300) ** 2)) + 0.6 * Math.exp(-(((f - 1500) / 400) ** 2)) + 0.05;
    v += (formants / h) * Math.sin(h * phase);
  }
  const syllable = Math.max(0, Math.sin(2 * Math.PI * 4 * t)) ** 2;           // 4 bursts per second
  return 0.35 * v * syllable;
}

// frequency of a tone from its zero crossings (accurate to well under 0.1 Hz)
function measureFrequency(samples, rate) {
  const crossings = [];
  for (let i = 1; i < samples.length; i++) {
    if (samples[i - 1] < 0 && samples[i] >= 0) {
      crossings.push(i - 1 + samples[i - 1] / (samples[i - 1] - samples[i]));   // in between the two
    }
  }
  return (crossings.length - 1) / ((crossings.at(-1) - crossings[0]) / rate);
}

// strength of one frequency (Goertzel / single DFT bin), as an amplitude
function amplitudeAt(samples, rate, freq) {
  let re = 0, im = 0;
  for (let i = 0; i < samples.length; i++) {
    re += samples[i] * Math.cos(2 * Math.PI * freq * i / rate);
    im -= samples[i] * Math.sin(2 * Math.PI * freq * i / rate);
  }
  return (2 * Math.hypot(re, im)) / samples.length;
}

// how far `got` is from the perfect version, in dB (higher = closer; 60 dB = 0.1% error)
function signalToErrorDb(got, ideal, skip) {
  let sig = 0, err = 0;
  for (let i = skip; i < Math.min(got.length, ideal.length) - skip; i++) {
    sig += ideal[i] ** 2;
    err += (got[i] - ideal[i]) ** 2;
  }
  return 10 * Math.log10(sig / err);
}

// the full path, exactly as app.js does it after decodeAudioData()
function convert(channels, rate, start, end) {
  const result = convertForUpload(channels, rate, start, end, OUT_RATE);
  return { ...result, read: readWav16(result.wav) };
}

for (const rate of [48000, 44100, 16000]) {
  test(`440 Hz tone at ${rate} Hz keeps its pitch, length, loudness and WAV format`, () => {
    const decoded = sampled(tone440, rate, 4);
    const { read, clipped } = convert([decoded, decoded], rate, 1.0, 3.5);  // stereo, trimmed to 2.5 s

    assert.equal(read.sampleRate, OUT_RATE);           // WAV header
    assert.equal(read.channels, 1);
    assert.equal(read.bitsPerSample, 16);
    assert.equal(read.samples.length, 2.5 * OUT_RATE); // duration: exactly 2.5 s
    assert.equal(clipped, 0);

    const middle = read.samples.slice(2400, -2400);    // ignore 0.1 s at each cut edge
    assert.ok(Math.abs(measureFrequency(middle, OUT_RATE) - 440) < 0.05,
              `frequency ${measureFrequency(middle, OUT_RATE)}`);
    const amp = amplitudeAt(middle, OUT_RATE, 440);
    assert.ok(Math.abs(amp - 0.5) < 0.005, `amplitude ${amp}`);      // within 0.1 dB

    // compared with the perfect 24 kHz tone starting at 1.0 s
    const ideal = sampled((t) => tone440(t + 1.0), OUT_RATE, 2.5);
    const db = signalToErrorDb(read.samples, ideal, 240);
    assert.ok(db > 60, `only ${db.toFixed(1)} dB from the perfect tone`);
  });
}

test("speech-like signal at 48 kHz stereo comes through almost unchanged", () => {
  const decoded = sampled(speechLike, 48000, 6);
  const { read, clipped, seconds } = convert([decoded, decoded], 48000, 0.5, 5.5);
  assert.equal(read.sampleRate, OUT_RATE);
  assert.equal(seconds, 5);
  assert.equal(read.samples.length, 5 * OUT_RATE);
  assert.equal(clipped, 0);

  const ideal = sampled((t) => speechLike(t + 0.5), OUT_RATE, 5);
  const db = signalToErrorDb(read.samples, ideal, 240);
  assert.ok(db > 55, `only ${db.toFixed(1)} dB from the perfect signal`);

  // the voice's harmonics (pitch and "formant" shape) keep their strength
  for (const f of [F0, 4 * F0, 12 * F0, 40 * F0]) {
    const a = amplitudeAt(read.samples, OUT_RATE, f), b = amplitudeAt(ideal, OUT_RATE, f);
    assert.ok(Math.abs(20 * Math.log10(a / b)) < 0.1, `${f} Hz changed by ${(20 * Math.log10(a / b)).toFixed(2)} dB`);
  }
});

test("speech-like signal from a 44.1 kHz phone recording (uneven rate ratio)", () => {
  const decoded = sampled(speechLike, 44100, 4);
  const { read } = convert([decoded], 44100, 0, 4);
  assert.equal(read.samples.length, 4 * OUT_RATE);
  const db = signalToErrorDb(read.samples, sampled(speechLike, OUT_RATE, 4), 240);
  assert.ok(db > 55, `only ${db.toFixed(1)} dB`);
});

test("sounds too high for 24 kHz are removed, not folded back as false tones", () => {
  // 15 kHz can't exist at 24 kHz (max 12 kHz). A bad resampler turns it into 9 kHz.
  const out = resample(sampled((t) => 0.5 * Math.sin(2 * Math.PI * 15000 * t), 48000, 1), 48000, OUT_RATE);
  const middle = out.slice(1000, -1000);
  const alias = amplitudeAt(middle, OUT_RATE, 9000);
  assert.ok(20 * Math.log10(alias / 0.5) < -60, `9 kHz alias at ${(20 * Math.log10(alias / 0.5)).toFixed(1)} dB`);
});

test("channel mixing: identical channels unchanged, one silent channel halves the level", () => {
  const left = sampled(tone440, 24000, 1);
  const same = convert([left, left], 24000, 0, 1).read.samples;
  const solo = convert([left, new Float32Array(left.length)], 24000, 0, 1).read.samples;
  assert.ok(Math.abs(amplitudeAt(same, OUT_RATE, 440) - 0.5) < 0.001);
  assert.ok(Math.abs(amplitudeAt(solo, OUT_RATE, 440) - 0.25) < 0.001);   // same as ffmpeg -ac 1
});

test("Int16 scaling: full scale, half scale and rounding are exact", () => {
  const { samples } = readWav16(encodeWav16(Float32Array.of(1, -1, 0.5, -0.5, 0, 0.00002, 1.7, -3), 24000));
  const ints = [...samples].map((v) => Math.round(v * 0x8000));
  assert.deepEqual(ints, [32767, -32768, 16384, -16384, 0, 1, 32767, -32768]);   // 1.7 and -3 are clamped
});

test("a loud but legal recording isn't clipped; an overloaded one is reported", () => {
  const loud = sampled((t) => 0.98 * Math.sin(2 * Math.PI * 440 * t), 48000, 1);
  assert.equal(convert([loud], 48000, 0, 1).clipped, 0);
  const tooLoud = sampled((t) => 1.3 * Math.sin(2 * Math.PI * 440 * t), 48000, 1);
  assert.ok(convert([tooLoud], 48000, 0, 1).clipped > 0);
});
