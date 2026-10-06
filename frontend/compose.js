// ============================================================
// ReVoice — The composed sentence (pointer + keyboard)
// ============================================================
// Pure functions, no DOM (tested with Node).
// There is ONE sentence. The patient builds it with pointer selections (letters,
// SPACE, DEL, predictions); a caregiver or a user who can still type can also type
// it in the text box. Both change the same text:
//   pointer letter      -> added at the end, counts 1 selection
//   prediction          -> REPLACES the text, counts 1 selection
//   typing              -> the text is exactly what is in the box; NOT a selection,
//                          and marks the sentence as typed (see below)
//   Clear               -> empty text, 0 selections, not typed
// Why "typed" matters: the selection counter shows how much the predictions save the
// PATIENT's pointer effort. Typed characters cost no pointer selections, so a typed
// sentence would look like a huge saving. For typed sentences we still count the
// pointer selections, but give no "saved" percentage and don't log it as savings.
// ============================================================

export function emptySentence() {
  return { text: "", selections: 0, typed: false };
}

// one pointer selection on the letter grid
export function pointerKey(state, label) {
  if (!label || label === "_") return state;                 // empty cell: nothing happens
  const text = label === "SPACE" ? state.text + " "
    : label === "DEL" ? state.text.slice(0, -1)
    : state.text + label;
  return { ...state, text, selections: state.selections + 1 };
}

// choosing a prediction (by pointer dwell or by clicking it): replaces the text
export function choosePrediction(state, phrase) {
  if (!phrase) return state;
  return { ...state, text: phrase.toUpperCase() + " ", selections: state.selections + 1 };
}

// the text box changed: its content IS the sentence, kept exactly as typed
export function typed(state, value) {
  if (value === state.text) return state;
  return { ...state, text: value, typed: true };
}

// What a key press in the text box does:
//   "speak"  Ctrl+Enter (or Cmd+Enter on a Mac)
//   "ignore" Enter without Ctrl (no new line: the sentence is one line, and nothing is sent)
//   null     anything else: normal typing
export function textBoxKeyAction(event) {
  if (event.key !== "Enter" || event.isComposing) return null;
  return event.ctrlKey || event.metaKey ? "speak" : "ignore";
}

// Page-wide shortcuts (e.g. Space = record in guided voice banking) must NOT fire
// while someone is typing in a text box, a field or a list.
export function isTypingTarget(target) {
  return Boolean(target && typeof target.matches === "function" &&
                 target.matches("input, select, textarea, [contenteditable]:not([contenteditable='false'])"));
}

// how the sentence was made, for the generation log
export function inputMethod(state) {
  if (!state.typed) return "pointer";
  return state.selections > 0 ? "mixed" : "keyboard";
}
