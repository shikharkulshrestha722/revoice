// Tests for pin.js: PIN rules (same as the server), refusals that open the PIN dialog, indicator text.
import { test } from "node:test";
import assert from "node:assert/strict";
import { isValidPin, newPinProblem, pinPromptFor, formatWait, lockIndicator } from "../pin.js";

test("PIN: 4 to 6 plain digits, like the server", () => {
  for (const pin of ["1234", "000000", "48261"]) assert.equal(isValidPin(pin), true, pin);
  for (const pin of ["123", "1234567", "12a4", " 1234", "", "١٢٣٤", 1234, null]) assert.equal(isValidPin(pin), false, String(pin));
});

test("a new PIN must be valid and typed the same twice", () => {
  assert.equal(newPinProblem("4826", "4826"), "");
  assert.match(newPinProblem("48", "48"), /4 to 6 digits/);
  assert.match(newPinProblem("4826", "4862"), /not the same/);
});

test("only 'locked' and 'pin_not_set' refusals open the PIN dialog", () => {
  assert.equal(pinPromptFor(401, { code: "locked" }), "unlock");
  assert.equal(pinPromptFor(403, { code: "pin_not_set" }), "set");
  assert.equal(pinPromptFor(401, { code: "wrong_pin" }), null);
  assert.equal(pinPromptFor(429, { code: "locked_out" }), null);
  assert.equal(pinPromptFor(400, { error: "No audio file was sent." }), null);
  assert.equal(pinPromptFor(500, null), null);
});

test("waiting times", () => {
  assert.equal(formatWait(272), "4:32");
  assert.equal(formatWait(60), "1:00");
  assert.equal(formatWait(45), "45 s");
  assert.equal(formatWait(0.2), "1 s");
  assert.equal(formatWait(-5), "0 s");
});

test("lock indicator", () => {
  assert.deepEqual(lockIndicator(null), { text: "", state: "unknown" });
  assert.equal(lockIndicator({ pin_set: false }).state, "no_pin");
  assert.deepEqual(lockIndicator({ pin_set: true, seconds_left: 0, locked_out_seconds: 0 }),
                   { text: "🔒 Recordings locked", state: "locked" });
  assert.deepEqual(lockIndicator({ pin_set: true, seconds_left: 801, locked_out_seconds: 0 }),
                   { text: "🔓 Recordings unlocked · 14 min", state: "unlocked" });
  assert.deepEqual(lockIndicator({ pin_set: true, seconds_left: 0, locked_out_seconds: 272 }),
                   { text: "🔒 Locked out · 4:32", state: "locked_out" });
});
