# ============================================================
# ReVoice — Phrase Prediction
# ============================================================
# Given what the patient has typed so far, suggest 3 likely
# completions so they don't have to spell every letter.
#
# Two modes:
#   1. Gemini (smart, context-aware)  -> used when an API key is set
#   2. Offline word list (always works) -> used if Gemini fails
# ============================================================

import json
import re

# Short, high-frequency phrases a patient is likely to need.
# Used when there's no text yet, or when Gemini is unavailable.
STARTER_PHRASES = [
    "I need help", "Yes", "No", "Thank you", "I am in pain",
    "I need water", "Please wait", "I am tired", "Call the nurse",
    "I love you", "I am hungry", "Turn me over",
]

# Common words for offline completion, ordered by usefulness
COMMON_WORDS = [
    "I", "AM", "NEED", "WANT", "PLEASE", "HELP", "WATER", "FOOD", "PAIN",
    "YES", "NO", "THANK", "YOU", "NURSE", "DOCTOR", "BED", "COLD", "HOT",
    "TIRED", "HUNGRY", "THIRSTY", "TOILET", "MEDICINE", "CALL", "FAMILY",
    "LOVE", "SORRY", "WAIT", "STOP", "MORE", "LESS", "GOOD", "BAD", "OKAY",
    "TURN", "SIT", "LIE", "DOWN", "UP", "LIGHT", "DARK", "TV", "MUSIC",
    "THERE", "THIS", "THAT", "THE", "AND", "MY", "ME", "IS", "IT", "NOT",
    "CAN", "WILL", "WHEN", "WHERE", "WHAT", "WHY", "HOW", "HOME", "NOW",
]


# Likely next words, used only by the offline fallback
CONTINUATIONS = {
    "I":      ["NEED", "AM", "WANT"],
    "AM":     ["TIRED", "IN PAIN", "COLD"],
    "NEED":   ["WATER", "HELP", "THE NURSE"],
    "WANT":   ["TO SIT UP", "WATER", "TO REST"],
    "CALL":   ["THE NURSE", "THE DOCTOR", "MY FAMILY"],
    "PLEASE": ["HELP ME", "WAIT", "COME HERE"],
    "THANK":  ["YOU"],
    "TURN":   ["ME OVER", "ON THE LIGHT", "OFF THE TV"],
}


def build_prompt(text: str) -> str:
    """Creates the instruction we send to Gemini."""
    text = text.strip()
    if not text:
        situation = "The patient has not typed anything yet. Suggest 3 common things they might want to say."
    elif text.endswith(" "):
        situation = f'The patient has typed: "{text.strip()}". Suggest 3 likely ways to continue.'
    else:
        words = text.split()
        situation = (
            f'The patient has typed: "{text}". '
            f'The last word "{words[-1]}" may be incomplete. '
            f"Suggest 3 likely completions of the full sentence."
        )

    return (
        "You help a patient with paralysis communicate. They select letters very slowly, "
        "so your job is to guess the whole sentence early.\n\n"
        f"{situation}\n\n"
        "Rules:\n"
        "- Give exactly 3 suggestions.\n"
        "- Each must be a complete, natural, SHORT sentence (max 6 words).\n"
        "- Everyday needs: comfort, pain, food, family, medical help.\n"
        "- Each suggestion must continue what they typed, keeping those letters.\n"
        '- Reply with ONLY a JSON array of strings, e.g. ["I need water", "I need help", "I need rest"]\n'
        "- No explanation, no markdown, no code fences."
    )


def parse_suggestions(raw: str, limit: int = 3) -> list:
    """Pulls the list of suggestions out of Gemini's reply."""
    if not raw:
        return []

    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()

    # Preferred: a JSON array somewhere in the reply
    match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
    if match:
        try:
            items = json.loads(match.group(0))
            out = [str(i).strip() for i in items if str(i).strip()]
            if out:
                return out[:limit]
        except (ValueError, TypeError):
            pass

    # Fallback: treat each line as a suggestion
    lines = []
    for line in cleaned.splitlines():
        line = line.strip().strip('",')
        line = re.sub(r"^[-*\d.)\s]+", "", line)  # strip bullets/numbering
        if line:
            lines.append(line)
    return lines[:limit]


def fallback_suggestions(text: str, limit: int = 3) -> list:
    """Works with no internet and no API key, so a demo never dies."""
    text = text.lstrip()          # keep a trailing space: it means "start a new word"

    if not text.strip():
        return STARTER_PHRASES[:limit]

    if text.endswith(" "):
        base = text.strip()
        last = base.split()[-1].upper() if base.split() else ""
        nexts = CONTINUATIONS.get(last, ["PLEASE", "NOW", "HELP"])
        out = [f"{base} {w}".strip().capitalize() for w in nexts]
        for p in STARTER_PHRASES:
            if len(out) >= limit:
                break
            if p not in out:
                out.append(p)
        return out[:limit]

    words = text.split()
    partial = words[-1].upper()
    prefix = " ".join(words[:-1])

    matches = [w for w in COMMON_WORDS if w.startswith(partial) and w != partial]
    if not matches:
        matches = [w for w in COMMON_WORDS if partial in w][:limit]

    out = []
    for w in matches[:limit]:
        phrase = f"{prefix} {w}".strip()
        out.append(phrase.capitalize())

    # if nothing matched, fall back to starter phrases that begin with the typed letters
    if not out:
        out = [p for p in STARTER_PHRASES if p.upper().startswith(partial)][:limit]
    # pad out to `limit` so the suggestion bar is never half empty
    for p in STARTER_PHRASES:
        if len(out) >= limit:
            break
        if p not in out:
            out.append(p)
    return out[:limit]


def is_sensible(s: str) -> bool:
    """Rejects refusals, explanations, and anything too long to be a spoken phrase."""
    if not s or len(s) > 60 or len(s.split()) > 10:
        return False
    bad_starts = ("i'm sorry", "i am sorry", "i cannot", "i can't", "as an ai",
                  "sure,", "here are", "here's", "okay,", "note:")
    return not s.lower().startswith(bad_starts)


def get_suggestions(text: str, call_gemini=None, limit: int = 3) -> dict:
    """
    Main entry point.
    call_gemini: a function(prompt) -> str. If it's None or fails,
    we silently use the offline list instead.
    Returns {"suggestions": [...], "source": "gemini"|"offline"}
    """
    if call_gemini is not None:
        try:
            raw = call_gemini(build_prompt(text))
            parsed = [s for s in parse_suggestions(raw, limit=limit) if is_sensible(s)]
            if parsed:
                return {"suggestions": parsed, "source": "gemini"}
        except Exception as e:
            print(f"[predictor] Gemini failed, using offline list: {e}")

    return {"suggestions": fallback_suggestions(text, limit=limit), "source": "offline"}