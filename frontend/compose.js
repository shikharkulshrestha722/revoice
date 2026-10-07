// The one composed sentence {text, selections, typed}, changed by pointer, predictions and typing.
// Typed characters aren't pointer selections, so typed sentences get no "saved by prediction" figure.

export function emptySentence() {
  return { text: "", selections: 0, typed: false };
}

// one pointer selection: letters are appended, counts 1 selection
export function pointerKey(state, label) {
  if (!label || label === "_") return state;                 // empty cell
  const text = label === "SPACE" ? state.text + " "
    : label === "DEL" ? state.text.slice(0, -1)
    : state.text + label;
  return { ...state, text, selections: state.selections + 1 };
}

// a prediction (dwell or click) replaces the text, counts 1 selection
export function choosePrediction(state, phrase) {
  if (!phrase) return state;
  return { ...state, text: phrase.toUpperCase() + " ", selections: state.selections + 1 };
}

// the text box content is the sentence, exactly as typed
export function typed(state, value) {
  if (value === state.text) return state;
  return { ...state, text: value, typed: true };
}

// Ctrl/Cmd+Enter -> "speak"; other Enter -> "ignore" (the sentence stays one line); else null
export function textBoxKeyAction(event) {
  if (event.key !== "Enter" || event.isComposing) return null;
  return event.ctrlKey || event.metaKey ? "speak" : "ignore";
}

// Page shortcuts (e.g. Space to record in guided banking) must not fire while typing.
export function isTypingTarget(target) {
  return Boolean(target && typeof target.matches === "function" &&
                 target.matches("input, select, textarea, [contenteditable]:not([contenteditable='false'])"));
}

// pointer, keyboard or mixed, for the generation log
export function inputMethod(state) {
  if (!state.typed) return "pointer";
  return state.selections > 0 ? "mixed" : "keyboard";
}
