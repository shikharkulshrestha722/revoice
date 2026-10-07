// Tests for compose.js: one sentence changed by the pointer and by typing.
import { test } from "node:test";
import assert from "node:assert/strict";
import { emptySentence, pointerKey, choosePrediction, typed, textBoxKeyAction, isTypingTarget,
         inputMethod } from "../compose.js";
import { selectionSavings } from "../selection_stats.js";

const spell = (state, letters) => [...letters].reduce((s, c) => pointerKey(s, c === " " ? "SPACE" : c), state);

test("typed text is kept exactly as typed (case, punctuation, spaces) and is not a selection", () => {
  const s = typed(emptySentence(), "Hello, Doctor  Rao! I need water?");
  assert.equal(s.text, "Hello, Doctor  Rao! I need water?");     // this exact string goes to /speak
  assert.equal(s.selections, 0);
  assert.equal(s.typed, true);
  assert.equal(inputMethod(s), "keyboard");
});

test("pointer selections append to typed text, and both edit the same sentence", () => {
  let s = typed(emptySentence(), "I need ");
  s = spell(s, "TEA");
  assert.equal(s.text, "I need TEA");
  assert.equal(s.selections, 3);
  assert.equal(inputMethod(s), "mixed");
  s = pointerKey(s, "DEL");
  assert.equal(s.text, "I need TE");
  s = typed(s, "I need tea please");                           // typing edits the whole text
  assert.equal(s.text, "I need tea please");
  assert.equal(s.selections, 4);                               // pointer selections are kept
});

test("a prediction replaces the text, whether picked by pointer or by click", () => {
  const s = choosePrediction(typed(emptySentence(), "i ne"), "I need water");
  assert.equal(s.text, "I NEED WATER ");
  assert.equal(s.selections, 1);
  assert.equal(choosePrediction(s, undefined), s);              // empty slot: nothing happens
});

test("Clear empties the text, the selection count and the typed mark", () => {
  const s = spell(typed(emptySentence(), "abc"), "D");
  assert.deepEqual(emptySentence(), { text: "", selections: 0, typed: false });
  assert.notDeepEqual(s, emptySentence());
});

test("pointer-only sentences behave exactly as before", () => {
  const s = spell(emptySentence(), "I NEED");
  assert.equal(s.text, "I NEED");
  assert.equal(s.selections, 6);
  assert.equal(s.typed, false);
  assert.equal(inputMethod(s), "pointer");
  assert.equal(pointerKey(s, "_"), s);                          // blank cell
  assert.equal(selectionSavings(s.selections, s.text, s.typed).savedPct, 0);
});

test("typed sentences get no 'saved by prediction' figure (typing is not pointing)", () => {
  assert.equal(selectionSavings(0, "I NEED WATER", true).savedPct, null);
  assert.equal(selectionSavings(1, "I NEED WATER ", false).savedPct, 91.7);   // unchanged for pointer
});

test("typing the same text again changes nothing (no false 'typed' mark)", () => {
  const s = spell(emptySentence(), "HI");
  assert.equal(typed(s, "HI"), s);
});

test("Ctrl+Enter speaks, plain Enter does nothing, other keys type normally", () => {
  assert.equal(textBoxKeyAction({ key: "Enter", ctrlKey: true }), "speak");
  assert.equal(textBoxKeyAction({ key: "Enter", metaKey: true }), "speak");     // Mac
  assert.equal(textBoxKeyAction({ key: "Enter" }), "ignore");
  assert.equal(textBoxKeyAction({ key: "Enter", shiftKey: true }), "ignore");   // still no new line
  assert.equal(textBoxKeyAction({ key: "Enter", ctrlKey: true, isComposing: true }), null);  // IME
  assert.equal(textBoxKeyAction({ key: " " }), null);
  assert.equal(textBoxKeyAction({ key: "a", ctrlKey: true }), null);
});

test("page shortcuts (Space = record in guided banking) don't fire while typing", () => {
  // a tiny stand-in for DOM elements: matches() checks the CSS selector list
  const el = (tag, attrs = {}) => ({
    matches: (selector) => selector.split(",").some((part) => {
      part = part.trim();
      if (part.startsWith("[contenteditable]")) return attrs.contenteditable !== undefined &&
                                                       attrs.contenteditable !== "false";
      return part === tag;
    }),
  });
  assert.equal(isTypingTarget(el("textarea")), true);           // the composed-sentence box
  assert.equal(isTypingTarget(el("input")), true);
  assert.equal(isTypingTarget(el("select")), true);
  assert.equal(isTypingTarget(el("div", { contenteditable: "true" })), true);
  assert.equal(isTypingTarget(el("div", { contenteditable: "false" })), false);
  assert.equal(isTypingTarget(el("button")), false);            // shortcuts still work elsewhere
  assert.equal(isTypingTarget(el("body")), false);
  assert.equal(isTypingTarget(null), false);
  assert.equal(isTypingTarget({}), false);                        // e.g. the document itself
});

