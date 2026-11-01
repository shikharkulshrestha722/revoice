// Tests for indic.js: engine choices for Indian languages, comparisons, and the messages shown.
import { test } from "node:test";
import assert from "node:assert/strict";
import { usesIndianLanguage, compareEngines, previewEngine, indicf5Seconds, generatingMessage,
         convertedLabel, INDICF5_TYPICAL_SECONDS } from "../indic.js";

test("only the IndicF5 choices need a language", () => {
  assert.equal(usesIndianLanguage("indicf5"), true);
  assert.equal(usesIndianLanguage("compare_indic"), true);
  for (const choice of ["f5", "xtts", "both"]) assert.equal(usesIndianLanguage(choice), false);
});

test("comparisons: XTTS vs F5, and IndicF5 first then F5 on the same converted text", () => {
  assert.deepEqual(compareEngines("both"), ["xtts", "f5"]);
  assert.deepEqual(compareEngines("compare_indic"), ["indicf5", "f5"]);
  assert.equal(compareEngines("indicf5"), null);
  assert.equal(compareEngines("f5"), null);
});

test("the Voice Bank previews the clip of the engine that will copy the voice", () => {
  assert.equal(previewEngine("both"), "f5");
  assert.equal(previewEngine("compare_indic"), "indicf5");
  assert.equal(previewEngine("indicf5"), "indicf5");
  assert.equal(previewEngine("xtts"), "xtts");
});

test("a normal sentence takes about 15 s; longer text a little longer, in 5 s steps", () => {
  assert.equal(indicf5Seconds("main paani chahta hoon"), INDICF5_TYPICAL_SECONDS);
  assert.equal(indicf5Seconds("a".repeat(80)), 15);
  assert.equal(indicf5Seconds("a".repeat(81)), 20);
  assert.equal(indicf5Seconds("a".repeat(160)), 25);
  assert.equal(indicf5Seconds(""), 15);
});

test("progress message names the language and the wait, and warns the first time", () => {
  assert.equal(generatingMessage("Hindi", "I need water"), "Generating in Hindi… about 15 s");
  assert.match(generatingMessage("Tamil", "I need water", true), /^Generating in Tamil… about 15 s \(longer the first time/);
});

test("the converted sentence is labelled with how it was made", () => {
  assert.equal(convertedLabel("Hindi", "translate"), "Hindi (translated from English):");
  assert.equal(convertedLabel("Hindi", "transliterate"), "Hindi (converted from English letters):");
  assert.equal(convertedLabel("Tamil", "as_typed"), "Tamil (as typed):");
  assert.equal(convertedLabel("Tamil", undefined), "Tamil:");
});
