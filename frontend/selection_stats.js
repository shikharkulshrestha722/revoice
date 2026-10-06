// ============================================================
// ReVoice — Selection counter maths
// ============================================================
// Pure functions, no DOM (tested with Node).
// Shows how much effort the AI predictions save the patient:
//   selections used   = every pointer selection made for this sentence
//                       (letters, SPACE, DEL, and picking a prediction)
//   letter-by-letter  = selections needed to spell the final sentence with
//                       no predictions: one per letter + one per space
//   saved             = (letter-by-letter - used) / letter-by-letter
// Must match backend/generation_log.py so the page and the CSV agree.
// ============================================================

// "  I  NEED   WATER " -> "I NEED WATER"
export function tidyText(text) {
  return (text || "").split(/\s+/).filter(Boolean).join(" ");
}

// typed = (part of) the sentence was typed on a keyboard: typed characters aren't pointer
// selections, so no "saved" percentage (it would look like a huge saving that isn't real)
export function selectionSavings(selections, text, typed = false) {
  const sentence = tidyText(text);
  const letterByLetter = sentence.length;                 // letters + single spaces
  const spaces = sentence ? sentence.split(" ").length - 1 : 0;
  const savedPct = letterByLetter && !typed
    ? Math.round((1000 * (letterByLetter - selections)) / letterByLetter) / 10
    : null;
  return { selections, letterByLetter, letters: letterByLetter - spaces, spaces, savedPct, typed };
}
