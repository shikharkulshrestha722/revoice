// Tests for selection_stats.js.  Run from the project root:  node --test "frontend/tests/*.test.mjs"
import { test } from "node:test";
import assert from "node:assert/strict";
import { selectionSavings, tidyText } from "../selection_stats.js";

test("tidyText removes extra and trailing spaces", () => {
  assert.equal(tidyText("  I  NEED   WATER "), "I NEED WATER");
  assert.equal(tidyText(""), "");
});

test("picking a prediction after a few letters saves most selections", () => {
  // typed "I", SPACE, "N" (3 selections) then picked "I NEED WATER" (1 selection)
  const s = selectionSavings(4, "I NEED WATER ");
  assert.equal(s.letterByLetter, 12);     // 10 letters + 2 spaces
  assert.equal(s.letters, 10);
  assert.equal(s.spaces, 2);
  assert.equal(s.savedPct, 66.7);
});

test("spelling everything by hand saves nothing", () => {
  assert.equal(selectionSavings(5, "HELLO").savedPct, 0);
});

test("lots of corrections can make it worse than spelling", () => {
  assert.equal(selectionSavings(8, "YES").savedPct, -166.7);
});

test("empty text has no percentage", () => {
  const s = selectionSavings(0, "   ");
  assert.equal(s.letterByLetter, 0);
  assert.equal(s.savedPct, null);
});

test("same counting as the backend log (letters + single spaces)", () => {
  // backend: letter_by_letter_selections("  I  NEED   WATER  ") == 12
  assert.equal(selectionSavings(0, "  I  NEED   WATER  ").letterByLetter, 12);
});
