"""IndicF5's 11 Indian languages: names, scripts, how long a sentence takes to say, and
turning typed text into the right script (via Gemini) before it is spoken.

The part between the "shared with Colab" lines is copied word for word into Step 3d of
colab/revoice_server_v2.ipynb (tests/test_indic_languages.py checks they stay identical):
Colab uses it to tell IndicF5 how long to make the speech."""

# ==== shared with Colab Step 3d (keep identical; tests/test_indic_languages.py checks) ====
import re, unicodedata

SYLLABLES_PER_SECOND = 5.0                # normal speaking rate (indicf5_test T7: Hindi, CER 0.00)
COMMA_PAUSE_S = 0.25                      # pause after , ; :
SENTENCE_PAUSE_S = 0.4                    # pause after । ॥ . ? ! inside the text (not after the last word)
MIN_SPEECH_S, MAX_SPEECH_S = 1.0, 20.0    # sensible limits for one sentence

# First code point of each script's Unicode block. The 9 Indian scripts share one layout
# (from the old ISCII standard), so the same offsets mean the same thing in every block.
INDIC_BLOCKS = {"Devanagari": 0x0900, "Bengali": 0x0980, "Gurmukhi": 0x0A00, "Gujarati": 0x0A80,
                "Odia": 0x0B00, "Tamil": 0x0B80, "Telugu": 0x0C00, "Kannada": 0x0C80, "Malayalam": 0x0D00}
# Scripts whose languages drop the 'a' after a bare last consonant (Hindi आप = "aap", not "aapa").
DROPS_FINAL_A = {"Devanagari", "Bengali", "Gurmukhi", "Gujarati"}
# Letters outside the common layout: Assamese ৰ ৱ, Odia ୱ (consonants); Gurmukhi ੰ ੱ (marks);
# Gurmukhi ੲ ੳ (vowel carriers); Malayalam chillus ൺ ൻ ർ ൽ ൾ ൿ (consonants with no vowel).
EXTRA_KINDS = {("Bengali", 0x70): "consonant", ("Bengali", 0x71): "consonant",
               ("Odia", 0x71): "consonant", ("Gurmukhi", 0x70): "sign", ("Gurmukhi", 0x71): "sign",
               ("Gurmukhi", 0x72): "vowel", ("Gurmukhi", 0x73): "vowel",
               **{("Malayalam", o): "dead" for o in range(0x7A, 0x80)}}


def script_of(ch):
    """Name of the Indian script a character belongs to, or None."""
    for name, start in INDIC_BLOCKS.items():
        if start <= ord(ch) < start + 0x80:
            return name
    return None


def letter_kind(ch):
    """What a character does: 'vowel' (a vowel letter, e.g. आ), 'consonant' (क), 'sign' (a vowel
    sign or nasal mark, e.g. ा ं), 'virama' (् joins two consonants), 'nukta' (the dot in ड़),
    'dead' (consonant with no vowel), 'digit', or None (anything else)."""
    if ch.isdigit():
        return "digit"
    script = script_of(ch)
    if script is None:
        return None
    o = ord(ch) - INDIC_BLOCKS[script]
    if (script, o) in EXTRA_KINDS:
        return EXTRA_KINDS[(script, o)]
    if 0x04 <= o <= 0x14 or o in (0x60, 0x61):
        return "vowel"
    if 0x15 <= o <= 0x39 or 0x58 <= o <= 0x5F:
        return "consonant"
    if o == 0x3C:
        return "nukta"
    if o == 0x4D:
        return "virama"
    if 0x01 <= o <= 0x03 or 0x3E <= o <= 0x4C or 0x55 <= o <= 0x57 or o in (0x62, 0x63):
        return "sign"
    return None


def words_of(text):
    """Words = runs of letters, marks and digits (vowel signs are marks, so they stay inside the word)."""
    return "".join(c if unicodedata.category(c)[0] in "LMN" else " " for c in text).split()


def count_syllables(text):
    """Syllables in text written in any of the 9 Indian scripts (plus any English words or digits
    mixed in). Each vowel letter is one syllable, and each consonant that carries a vowel: one
    written as a vowel sign, or the built-in 'a'. A consonant with a virama (्) joins the next
    one, so it adds nothing; in scripts whose languages drop a final 'a', a bare last consonant
    adds nothing either."""
    count = 0
    for word in words_of(text):
        chars = [c for c in word if letter_kind(c) != "nukta"]
        for i, ch in enumerate(chars):
            kind = letter_kind(ch)
            after = letter_kind(chars[i + 1]) if i + 1 < len(chars) else "end"
            if kind == "vowel":
                count += 1
            elif kind == "digit":
                count += 2                  # a digit is read as a number word, about 2 syllables
            elif kind == "consonant":
                if after == "virama":
                    continue                # part of a cluster, e.g. स्त in नमस्ते
                if after == "end" and i > 0 and script_of(ch) in DROPS_FINAL_A:
                    continue                # final 'a' dropped: मदद = "madad"
                count += 1
        latin = "".join(c for c in word if c.isascii() and c.isalpha())
        if latin:                           # an English word: one syllable per vowel group
            count += max(1, len(re.findall("[aeiouy]+", latin.lower())))
    return count


def clamp_speech(seconds):
    return round(min(max(seconds, MIN_SPEECH_S), MAX_SPEECH_S), 2)


def estimate_speech_seconds(text):
    """How long the sentence should take: syllables at a normal rate, plus short pauses at
    punctuation (none after the last word), kept between MIN_SPEECH_S and MAX_SPEECH_S."""
    body = text.strip().rstrip("।॥.?!,;: ")
    pauses = (sum(COMMA_PAUSE_S for c in body if c in ",;:")
              + sum(SENTENCE_PAUSE_S for c in body if c in "।॥.?!"))
    return clamp_speech(count_syllables(text) / SYLLABLES_PER_SECOND + pauses)


def ratio_speech_seconds(ref_seconds, ref_text, gen_text):
    """F5's own guess (reference length x text-length ratio), but counting CHARACTERS, not
    UTF-8 bytes: an Indian letter is 3 bytes and an English one 1, so F5's byte count made the
    Hindi part ~3x too long (the garbled start in indicf5_test T7). Fallback only."""
    return clamp_speech(ref_seconds * len(gen_text.strip()) / max(len(ref_text.strip()), 1))


def indicf5_total_seconds(ref_seconds, ref_text, gen_text):
    """fix_duration for IndicF5: the reference recording + the new speech. Uses the syllable
    estimate; only text with nothing countable falls back to the character ratio."""
    speech = (estimate_speech_seconds(gen_text) if count_syllables(gen_text)
              else ratio_speech_seconds(ref_seconds, ref_text, gen_text))
    return round(ref_seconds + speech, 2)
# ==== end of code shared with Colab Step 3d ====


# code: (name shown on the page, script). Codes are the ones IndicF5 uses.
LANGUAGES = {
    "as": ("Assamese", "Bengali"),
    "bn": ("Bengali", "Bengali"),
    "gu": ("Gujarati", "Gujarati"),
    "hi": ("Hindi", "Devanagari"),
    "kn": ("Kannada", "Kannada"),
    "ml": ("Malayalam", "Malayalam"),
    "mr": ("Marathi", "Devanagari"),
    "or": ("Odia", "Odia"),
    "pa": ("Punjabi", "Gurmukhi"),
    "ta": ("Tamil", "Tamil"),
    "te": ("Telugu", "Telugu"),
}
SCRIPT_NAMES = {"Bengali": "Bengali-Assamese", "Gurmukhi": "Gurmukhi (Punjabi)"}   # for messages
TEXT_MODES = ("transliterate", "translate")
DANDA_LANGUAGES = {"as", "bn", "hi", "or", "pa"}     # end a sentence with । instead of .
IN_SCRIPT_SHARE = 0.8                                # this share of letters in the script = already converted


class ConversionError(Exception):
    """The text couldn't be turned into the chosen language; nothing should be spoken."""


def language_list():
    """For the page: [{code, name, script}], sorted by name."""
    return sorted(({"code": code, "name": name, "script": SCRIPT_NAMES.get(script, script)}
                   for code, (name, script) in LANGUAGES.items()), key=lambda lang: lang["name"])


def script_share(text, language):
    """Share of the text's letters (vowel signs included) written in the language's script."""
    letters = [c for c in text if unicodedata.category(c)[0] in "LM"]
    if not letters:
        return 0.0
    script = LANGUAGES[language][1]
    return sum(script_of(c) == script for c in letters) / len(letters)


def in_target_script(text, language):
    return script_share(text, language) >= IN_SCRIPT_SHARE


def conversion_prompt(text, language, mode):
    """Strict prompt: Gemini must reply with the converted text only."""
    name, script = LANGUAGES[language]
    script = SCRIPT_NAMES.get(script, script)
    if mode == "transliterate":
        task = (f"The text below is {name} typed with English letters (romanised). Rewrite it in "
                f"{name} using the {script} script. Do NOT translate and do NOT add, remove or "
                f"change words; only change the letters.")
    else:
        task = (f"Translate the English text below into simple, natural, everyday {name}, written "
                f"in the {script} script. Keep the meaning exactly; add nothing.")
    return (f"{task} Keep the punctuation. Reply with ONLY the converted text: no quotes, no "
            f"explanation, no English, no romanisation.\n\nText: {text}")


def clean_reply(reply):
    """First non-empty line of Gemini's reply, without quotes or code marks around it."""
    lines = [line.strip() for line in (reply or "").replace("```", "\n").splitlines() if line.strip()]
    return lines[0].strip(" \"'“”«»`") if lines else ""


def finish_indic_text(text, language, emotion="neutral"):
    """Tidies spaces and ends the sentence with a stop (। or .), or ! for urgent/happy like English."""
    text = " ".join(text.split())
    if text and text[-1] not in "।॥.?!":
        text += "!" if emotion in ("urgent", "happy") else ("।" if language in DANDA_LANGUAGES else ".")
    return text


def convert_text(text, language, mode, call_gemini=None):
    """Returns {"text": text in the language's script, "mode": "as_typed" | mode}.
    Text already in the right script is used as typed (no Gemini). Otherwise Gemini converts it;
    without Gemini, or if its reply isn't in the right script, raises ConversionError, so
    English is never sent to IndicF5 as if it were Hindi."""
    if language not in LANGUAGES:
        raise ConversionError(f"Unknown language '{language}'. Use one of: {', '.join(sorted(LANGUAGES))}.")
    if mode not in TEXT_MODES:
        raise ConversionError(f"Unknown text mode '{mode}'. Use transliterate or translate.")
    name, script = LANGUAGES[language]
    script = SCRIPT_NAMES.get(script, script)
    text = " ".join(text.split())
    if in_target_script(text, language):
        return {"text": text, "mode": "as_typed"}
    if call_gemini is None:
        raise ConversionError(f"Can't convert to {name} without Gemini (no GEMINI_API_KEY). "
                              f"Type the sentence in {script} script instead.")
    try:
        converted = clean_reply(call_gemini(conversion_prompt(text, language, mode)))
    except Exception as e:
        raise ConversionError(f"Gemini couldn't convert the text to {name} ({type(e).__name__}). "
                              f"Try again, or type it in {script} script.") from e
    if not in_target_script(converted, language):
        raise ConversionError(f"Gemini's reply wasn't {name} in {script} script, so nothing was spoken.")
    return {"text": converted, "mode": mode}
