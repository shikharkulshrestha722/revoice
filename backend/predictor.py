"""Phrase prediction: 3 likely sentences from Gemini, or from an offline list when Gemini is unavailable."""

import json
import re

# Common patient phrases, shown before typing starts or when Gemini is unavailable.
STARTER_PHRASES = [
    "I need help", "Yes", "No", "Thank you", "I am in pain",
    "I need water", "Please wait", "I am tired", "Call the nurse",
    "I love you", "I am hungry", "Turn me over",
]

# Offline word completion, most useful first.
COMMON_WORDS = [
    "I", "AM", "NEED", "WANT", "PLEASE", "HELP", "WATER", "FOOD", "PAIN",
    "YES", "NO", "THANK", "YOU", "NURSE", "DOCTOR", "BED", "COLD", "HOT",
    "TIRED", "HUNGRY", "THIRSTY", "TOILET", "MEDICINE", "CALL", "FAMILY",
    "LOVE", "SORRY", "WAIT", "STOP", "MORE", "LESS", "GOOD", "BAD", "OKAY",
    "TURN", "SIT", "LIE", "DOWN", "UP", "LIGHT", "DARK", "TV", "MUSIC",
    "THERE", "THIS", "THAT", "THE", "AND", "MY", "ME", "IS", "IT", "NOT",
    "CAN", "WILL", "WHEN", "WHERE", "WHAT", "WHY", "HOW", "HOME", "NOW",
]


# Offline next-word guesses.
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
    """Builds the Gemini prompt."""
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
    """Extracts the suggestions from Gemini's reply."""
    if not raw:
        return []

    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()

    # preferred: a JSON array anywhere in the reply
    match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
    if match:
        try:
            items = json.loads(match.group(0))
            out = [str(i).strip() for i in items if str(i).strip()]
            if out:
                return out[:limit]
        except (ValueError, TypeError):
            pass

    # otherwise: one suggestion per line
    lines = []
    for line in cleaned.splitlines():
        line = line.strip().strip('",')
        line = re.sub(r"^[-*\d.)\s]+", "", line)  # strip bullets/numbering
        if line:
            lines.append(line)
    return lines[:limit]


def fallback_suggestions(text: str, limit: int = 3) -> list:
    """Offline suggestions: work with no internet and no API key."""
    text = text.lstrip()          # keep a trailing space: it means a new word starts

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

    # no word matched: starter phrases beginning with the typed letters
    if not out:
        out = [p for p in STARTER_PHRASES if p.upper().startswith(partial)][:limit]
    # pad to `limit` so the suggestion bar is never half empty
    for p in STARTER_PHRASES:
        if len(out) >= limit:
            break
        if p not in out:
            out.append(p)
    return out[:limit]


# Starts of refusals and chatty replies. Kept specific: a plain "i can't" would also block "I can't breathe".
REFUSAL_PATTERNS = [
    r"^(i'm|i am) sorry,? but\b",
    r"^i (cannot|can't|can not) (help|assist|provide|fulfil|fulfill|comply|do that)\b",
    r"^as an ai\b",
    r"^sure,",
    r"^here (are|is)\b",
    r"^here's\b",
    r"^okay,",
    r"^note:",
]


def is_sensible(s: str) -> bool:
    """Rejects refusals, explanations and anything too long to be a spoken phrase."""
    if not s or len(s) > 60 or len(s.split()) > 10:
        return False
    lowered = s.lower().strip()
    return not any(re.search(p, lowered) for p in REFUSAL_PATTERNS)


def get_suggestions(text: str, call_gemini=None, limit: int = 3) -> dict:
    """Returns {"suggestions": [...], "source": "gemini" | "offline"}.
    Uses call_gemini(prompt) -> str when given; falls back to the offline list if it's None or fails."""
    if call_gemini is not None:
        try:
            raw = call_gemini(build_prompt(text))
            parsed = [s for s in parse_suggestions(raw, limit=limit) if is_sensible(s)]
            if parsed:
                return {"suggestions": parsed, "source": "gemini"}
        except Exception as e:
            print(f"[predictor] Gemini failed, using offline list: {e}")

    return {"suggestions": fallback_suggestions(text, limit=limit), "source": "offline"}