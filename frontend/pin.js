// PURE helpers for the caregiver PIN (backend/caregiver_pin.py): PIN rules, what to do when the
// server refuses, and the text of the lock indicator. No DOM.

export const PIN_PATTERN = /^[0-9]{4,6}$/;          // same rule as the server: 4-6 plain digits

export function isValidPin(pin) {
  return typeof pin === "string" && PIN_PATTERN.test(pin);
}

// A problem with a new PIN and its repeat, as a message ("" = fine).
export function newPinProblem(pin, repeat) {
  if (!isValidPin(pin)) return "The PIN must be 4 to 6 digits (0-9).";
  if (pin !== repeat) return "The two PINs are not the same.";
  return "";
}

// A refused request: "unlock" = ask for the PIN, "set" = this user has no PIN yet, null = not a PIN problem.
export function pinPromptFor(status, body) {
  if (status === 401 && body?.code === "locked") return "unlock";
  if (status === 403 && body?.code === "pin_not_set") return "set";
  return null;
}

// 272 -> "4:32", 45 -> "45 s"
export function formatWait(seconds) {
  const s = Math.max(0, Math.ceil(Number(seconds) || 0));
  return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : `${s} s`;
}

// The lock indicator from /profiles/{id}/lock_status: { text, state }.
export function lockIndicator(status) {
  if (!status) return { text: "", state: "unknown" };
  if (!status.pin_set) return { text: "No caregiver PIN yet", state: "no_pin" };
  if (status.locked_out_seconds > 0) {
    return { text: `🔒 Locked out · ${formatWait(status.locked_out_seconds)}`, state: "locked_out" };
  }
  if (status.seconds_left > 0) {
    return { text: `🔓 Recordings unlocked · ${Math.ceil(status.seconds_left / 60)} min`, state: "unlocked" };
  }
  return { text: "🔒 Recordings locked", state: "locked" };
}
