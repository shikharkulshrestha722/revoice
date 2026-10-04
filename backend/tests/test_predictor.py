# Tests for predictor.py: reading Gemini's reply, the offline fallback,
# and the junk filter. No internet is used.
from predictor import (
    STARTER_PHRASES,
    build_prompt,
    fallback_suggestions,
    get_suggestions,
    is_sensible,
    parse_suggestions,
)


# ---------- parse_suggestions: reading Gemini's reply ----------

def test_parse_plain_json_array():
    assert parse_suggestions('["I need water", "I need help", "I need rest"]') == [
        "I need water", "I need help", "I need rest"]


def test_parse_keeps_only_the_limit():
    assert len(parse_suggestions('["a", "b", "c", "d", "e"]')) == 3


def test_parse_json_inside_code_fence():
    raw = '```json\n["I am cold", "I am tired"]\n```'
    assert parse_suggestions(raw) == ["I am cold", "I am tired"]


def test_parse_json_with_text_around_it():
    raw = 'Suggestions: ["Call the nurse", "Call my family"] hope this helps'
    assert parse_suggestions(raw) == ["Call the nurse", "Call my family"]


def test_parse_numbered_or_bulleted_lines():
    raw = "1. I need water\n2) I am cold\n- Call the nurse"
    assert parse_suggestions(raw) == ["I need water", "I am cold", "Call the nurse"]


def test_parse_skips_blank_items():
    assert parse_suggestions('["I need water", "", "  "]') == ["I need water"]


def test_parse_empty_reply():
    assert parse_suggestions("") == []
    assert parse_suggestions(None) == []


# ---------- fallback_suggestions: offline list ----------

def test_fallback_empty_text_gives_starter_phrases():
    assert fallback_suggestions("") == STARTER_PHRASES[:3]
    assert fallback_suggestions("   ") == STARTER_PHRASES[:3]


def test_fallback_trailing_space_suggests_next_word():
    # a trailing space means "this word is finished, what comes next?"
    assert fallback_suggestions("I ") == ["I need", "I am", "I want"]


def test_fallback_unknown_word_then_space_uses_generic_next_words():
    assert fallback_suggestions("HELLO ") == ["Hello please", "Hello now", "Hello help"]


def test_fallback_completes_partial_word():
    # words come out in COMMON_WORDS order (most useful first)
    assert fallback_suggestions("TH") == ["Thank", "Thirsty", "There"]


def test_fallback_keeps_earlier_words():
    out = fallback_suggestions("I NE")
    assert out[0] == "I need"
    assert len(out) == 3            # padded so the bar is never half empty


def test_fallback_no_match_still_returns_three():
    out = fallback_suggestions("ZZQ")
    assert len(out) == 3
    assert all(isinstance(s, str) and s for s in out)


# ---------- is_sensible: junk filter ----------

def test_sensible_normal_phrases_pass():
    for s in ["I need water", "Call the nurse", "Yes", "I'm sorry",
              "I am sorry", "I can't breathe", "I cannot move my arm"]:
        assert is_sensible(s), s


def test_sensible_rejects_refusals_and_chatter():
    for s in ["I'm sorry, but I can't help with that",
              "I cannot help with medical advice",
              "I can't assist with that",
              "As an AI, I cannot",
              "Sure, here you go",
              "Here are three suggestions",
              "Here's what I suggest",
              "Note: these are guesses"]:
        assert not is_sensible(s), s


def test_sensible_rejects_empty_and_too_long():
    assert not is_sensible("")
    assert not is_sensible("x" * 61)
    assert not is_sensible("one two three four five six seven eight nine ten eleven")


# ---------- get_suggestions: Gemini first, offline if it fails ----------

def test_uses_gemini_when_it_works():
    result = get_suggestions("I NEED ", call_gemini=lambda p: '["I need water", "I need help"]')
    assert result == {"suggestions": ["I need water", "I need help"], "source": "gemini"}


def test_no_gemini_function_means_offline():
    assert get_suggestions("", call_gemini=None)["source"] == "offline"


def test_gemini_error_falls_back_to_offline():
    def broken(prompt):
        raise TimeoutError("no internet")
    result = get_suggestions("", call_gemini=broken)
    assert result == {"suggestions": STARTER_PHRASES[:3], "source": "offline"}


def test_gemini_junk_only_falls_back_to_offline():
    result = get_suggestions("", call_gemini=lambda p: '["Here are some ideas"]')
    assert result["source"] == "offline"


def test_gemini_junk_items_are_filtered_out():
    result = get_suggestions("", call_gemini=lambda p: '["As an AI, I think", "I need water"]')
    assert result == {"suggestions": ["I need water"], "source": "gemini"}


# ---------- build_prompt ----------

def test_prompt_mentions_typed_text_and_partial_word():
    prompt = build_prompt("I NE")
    assert '"I NE"' in prompt
    assert '"NE" may be incomplete' in prompt


def test_prompt_for_empty_text():
    assert "not typed anything yet" in build_prompt("")
