// Selection counter: pointer selections used vs. spelling letter by letter (one per letter and space).
// Must match backend/generation_log.py so the page and the CSV agree.

// "  I  NEED   WATER " -> "I NEED WATER"
export function tidyText(text) {
  return (text || "").split(/\s+/).filter(Boolean).join(" ");
}

// typed sentences get no savedPct: typed characters aren't pointer selections
export function selectionSavings(selections, text, typed = false) {
  const sentence = tidyText(text);
  const letterByLetter = sentence.length;                 // letters + single spaces
  const spaces = sentence ? sentence.split(" ").length - 1 : 0;
  const savedPct = letterByLetter && !typed
    ? Math.round((1000 * (letterByLetter - selections)) / letterByLetter) / 10
    : null;
  return { selections, letterByLetter, letters: letterByLetter - spaces, spaces, savedPct, typed };
}
