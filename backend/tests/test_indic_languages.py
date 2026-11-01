# IndicF5 languages: syllable counts per script, the duration estimate (same code as Colab
# Step 3d), and converting typed text into the right script (Gemini mocked).
import json
import pathlib

import pytest

import indic_languages as il

ROOT = pathlib.Path(__file__).resolve().parents[2]

# "I need water" (or close) in each language, with syllables counted by hand.
SENTENCES = {
    "hi": ("मुझे पानी चाहिए।", 7),             # mu-jhe pa-ni cha-hi-e
    "mr": ("मला पाणी हवे आहे.", 8),            # ma-la pa-ni ha-ve a-he
    "bn": ("আমার জল চাই।", 5),                 # a-mar jol cha-i (final 'a' dropped)
    "as": ("মই ঘৰলৈ যাম।", 6),                 # mo-i gho-ro-loi jam (ৰ is Assamese-only)
    "gu": ("મને પાણી જોઈએ છે.", 8),            # ma-ne pa-ni jo-i-e chhe
    "pa": ("ਮੈਨੂੰ ਪਾਣੀ ਚਾਹੀਦਾ ਹੈ।", 8),           # mai-nu pa-ni cha-hi-da hai (ੰ is a mark)
    "or": ("ମୋତେ ପାଣି ଦରକାର।", 8),              # mo-te pa-ni do-ro-ka-ro (Odia keeps the final 'a')
    "ta": ("எனக்கு தண்ணீர் வேண்டும்.", 7),       # e-na-kku tan-nir ven-dum (் joins)
    "te": ("నాకు నీళ్ళు కావాలి.", 7),            # na-ku ni-llu ka-va-li
    "kn": ("ನನಗೆ ನೀರು ಬೇಕು.", 7),               # na-na-ge ni-ru be-ku
    "ml": ("എനിക്ക് വെള്ളം വേണം.", 6),            # e-ni(kk) vel-lam ve-nam
}


def test_every_indicf5_language_has_a_test_sentence():
    assert set(SENTENCES) == set(il.LANGUAGES)


@pytest.mark.parametrize("language", sorted(SENTENCES))
def test_syllables_are_counted_in_every_script(language):
    text, syllables = SENTENCES[language]
    assert il.count_syllables(text) == syllables


@pytest.mark.parametrize("language", sorted(SENTENCES))
def test_each_sentence_is_recognised_as_its_own_script(language):
    text, _ = SENTENCES[language]
    assert il.in_target_script(text, language)
    others = [code for code, (_, script) in il.LANGUAGES.items() if script != il.LANGUAGES[language][1]]
    assert not any(il.in_target_script(text, other) for other in others)


@pytest.mark.parametrize("language", sorted(SENTENCES))
def test_duration_is_syllables_at_five_per_second(language):
    text, syllables = SENTENCES[language]
    # one short sentence: no pause inside, at least the 1 s minimum
    assert il.estimate_speech_seconds(text) == max(round(syllables / 5.0, 2), il.MIN_SPEECH_S)


def test_t7_sentence_gets_the_duration_that_worked_on_colab():
    # indicf5_test T7, setting 1 (CER 0.00 on both seeds): 23 syllables -> 4.6 s + 0.25 + 0.4 pauses
    text = "नमस्ते, मुझे थोड़ा पानी चाहिए। क्या आप मेरी मदद कर सकते हैं?"
    assert il.count_syllables(text) == 23
    assert il.estimate_speech_seconds(text) == 5.25


@pytest.mark.parametrize("word, syllables", [
    ("आप", 1), ("मदद", 2), ("कर", 1),          # Hindi drops the final 'a'
    ("क्या", 1), ("नमस्ते", 3),                  # virama joins consonants
    ("थोड़ा", 2),                               # nukta (the dot) is not a letter
    ("न", 1),                                   # a one-letter word still has its vowel
    ("അവൻ", 2),                                 # Malayalam chillu ൻ has no vowel
    ("OK", 1), ("water", 2), ("5", 2),          # English words and digits mixed in
])
def test_syllable_rules(word, syllables):
    assert il.count_syllables(word) == syllables


def test_pauses_inside_the_text_but_not_after_the_last_word():
    assert il.estimate_speech_seconds("पानी पानी पानी पानी पानी") == 2.0                 # 10 syllables
    assert il.estimate_speech_seconds("पानी, पानी। पानी पानी पानी?") == 2.0 + 0.25 + 0.4


def test_duration_is_kept_within_sensible_limits():
    assert il.estimate_speech_seconds("ना") == il.MIN_SPEECH_S
    assert il.estimate_speech_seconds("पानी " * 200) == il.MAX_SPEECH_S


def test_fallback_counts_characters_not_bytes():
    # F5's own rule counted UTF-8 bytes: 3 per Devanagari letter vs 1 per English letter,
    # so the Hindi got 3x too long. The fallback must give the same as for English letters.
    hindi = "नमनमनमनमनम"                       # 11 letters = 33 bytes
    assert il.ratio_speech_seconds(10.0, "a" * 110, hindi) == 1.0
    assert il.ratio_speech_seconds(10.0, "a" * 110, "b" * 11) == 1.0


def test_total_duration_uses_syllables_and_falls_back_for_uncountable_text():
    assert il.indicf5_total_seconds(8.0, "Hello there.", "मुझे पानी चाहिए।") == 8.0 + 1.4
    # nothing to count (only punctuation): character ratio (12 reference letters), at least the minimum
    assert il.indicf5_total_seconds(8.0, "Hello there.", "?!?!?!") == 8.0 + 4.0
    assert il.indicf5_total_seconds(8.0, "Hello there.", "?") == 8.0 + il.MIN_SPEECH_S


def test_colab_step_3d_has_exactly_the_same_duration_code():
    source = (ROOT / "backend" / "indic_languages.py").read_text(encoding="utf-8")
    start = source.index("# ==== shared with Colab Step 3d")
    end = source.index("# ==== end of code shared with Colab Step 3d ====")
    shared = source[start:end]
    notebook = json.loads((ROOT / "colab" / "revoice_server_v2.ipynb").read_text(encoding="utf-8"))
    cells = ["".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"]
    step_3d = [c for c in cells if "INDICF5_REPO" in c]
    assert len(step_3d) == 1
    assert shared in step_3d[0], "Copy the shared block of indic_languages.py into Colab Step 3d"


def test_colab_step_3d_comes_after_the_existing_load_steps_and_api_is_v9():
    notebook = json.loads((ROOT / "colab" / "revoice_server_v2.ipynb").read_text(encoding="utf-8"))
    titles = [("".join(c["source"]).splitlines() or [""])[0] for c in notebook["cells"] if c["cell_type"] == "markdown"]
    order = [next(i for i, t in enumerate(titles) if t.startswith(f"## Step {s} ")) for s in ("3c", "3d", "4", "4a", "6")]
    assert order == sorted(order)
    code = "".join("".join(c["source"]) for c in notebook["cells"])
    assert "ReVoice API v9 running" in code
    assert 'req.engine == "indicf5"' in code


# Converting typed text

def test_text_already_in_the_script_is_used_as_typed_without_gemini():
    def gemini(prompt):
        raise AssertionError("Gemini must not be called")
    assert il.convert_text("मुझे  पानी चाहिए", "hi", "translate", call_gemini=gemini) == \
        {"text": "मुझे पानी चाहिए", "mode": "as_typed"}


@pytest.mark.parametrize("mode, words", [("transliterate", "romanised"), ("translate", "Translate")])
def test_gemini_converts_with_a_strict_prompt(mode, words):
    sent = {}

    def gemini(prompt):
        sent["prompt"] = prompt
        return '"मैं पानी चाहता हूँ"\n'
    result = il.convert_text("main paani chahta hoon", "hi", mode, call_gemini=gemini)
    assert result == {"text": "मैं पानी चाहता हूँ", "mode": mode}
    assert words in sent["prompt"] and "Hindi" in sent["prompt"] and "Devanagari" in sent["prompt"]
    assert "ONLY the converted text" in sent["prompt"]
    assert sent["prompt"].endswith("Text: main paani chahta hoon")


def test_without_gemini_english_is_refused_never_spoken():
    with pytest.raises(il.ConversionError, match="Devanagari"):
        il.convert_text("I need water", "hi", "translate", call_gemini=None)


def test_gemini_reply_in_the_wrong_script_is_refused():
    with pytest.raises(il.ConversionError, match="wasn't Tamil"):
        il.convert_text("I need water", "ta", "translate", call_gemini=lambda p: "I need water")


def test_gemini_failure_is_a_clear_error():
    def broken(prompt):
        raise TimeoutError("slow")
    with pytest.raises(il.ConversionError, match="couldn't convert"):
        il.convert_text("I need water", "hi", "translate", call_gemini=broken)


@pytest.mark.parametrize("language, mode", [("xx", "translate"), ("hi", "guess")])
def test_unknown_language_or_mode_is_refused(language, mode):
    with pytest.raises(il.ConversionError, match="Unknown"):
        il.convert_text("hello", language, mode, call_gemini=lambda p: "नमस्ते")


@pytest.mark.parametrize("language, emotion, ending", [
    ("hi", "neutral", "।"), ("ta", "neutral", "."), ("hi", "urgent", "!"), ("bn", "tender", "।")])
def test_sentence_end_matches_language_and_emotion(language, emotion, ending):
    assert il.finish_indic_text(" पानी   दो ", language, emotion) == "पानी दो" + ending
    assert il.finish_indic_text("पानी दो?", language, emotion) == "पानी दो?"


def test_language_list_has_names_for_the_page():
    langs = il.language_list()
    assert len(langs) == 11
    assert {"code": "hi", "name": "Hindi", "script": "Devanagari"} in langs
    assert [lang["name"] for lang in langs] == sorted(lang["name"] for lang in langs)
