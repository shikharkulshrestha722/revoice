// PURE helpers for IndicF5 (Indian languages): which engine choices need a language, which
// engines a comparison runs, and the messages shown while and after speaking. No DOM.

// Engine dropdown values that speak an Indian language.
export const INDIC_CHOICES = ["indicf5", "compare_indic"];

export function usesIndianLanguage(choice) {
  return INDIC_CHOICES.includes(choice);
}

// Comparisons run one engine after the other (they share one GPU on Colab).
const COMPARISONS = { both: ["xtts", "f5"], compare_indic: ["indicf5", "f5"] };

// The engines a comparison runs, in order, or null for a single engine.
export function compareEngines(choice) {
  return COMPARISONS[choice] || null;
}

// Engine whose recordings the Voice Bank previews (all of these use F5's single clip).
export function previewEngine(choice) {
  return compareEngines(choice) ? (choice === "both" ? "f5" : "indicf5") : choice;
}

// IndicF5 took ~17 s for one sentence on Colab's T4 (indicf5_test T7); longer text takes longer.
export const INDICF5_TYPICAL_SECONDS = 15;
const CHARS_IN_TYPICAL = 80;      // a typical sentence
const EXTRA_SECONDS_PER_40_CHARS = 5;

// Rough wait for IndicF5, rounded to 5 s: 15 s for a normal sentence.
export function indicf5Seconds(text) {
  const extraChars = Math.max(0, (text || "").trim().length - CHARS_IN_TYPICAL);
  return INDICF5_TYPICAL_SECONDS + Math.ceil(extraChars / 40) * EXTRA_SECONDS_PER_40_CHARS;
}

// "Generating in Hindi… about 15 s" (+ a note the first time, when Colab also loads the model).
export function generatingMessage(languageName, text, firstTime = false) {
  const base = `Generating in ${languageName || "the chosen language"}… about ${indicf5Seconds(text)} s`;
  return firstTime ? `${base} (longer the first time: the model loads)` : base;
}

const MODE_NOTES = {
  transliterate: "converted from English letters",
  translate: "translated from English",
  as_typed: "as typed",
};

// Label above the converted sentence, e.g. "Hindi (translated from English):".
export function convertedLabel(languageName, mode) {
  const note = MODE_NOTES[mode];
  return note ? `${languageName} (${note}):` : `${languageName}:`;
}
