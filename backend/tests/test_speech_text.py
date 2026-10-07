# prepare_text_for_speech: sentence case and punctuation per emotion.
import pytest

from app import prepare_text_for_speech


def test_all_caps_becomes_sentence_case():
    assert prepare_text_for_speech("I NEED WATER") == "I need water."


@pytest.mark.parametrize("emotion, ending", [
    ("neutral", "."),
    ("tender", "."),
    ("happy", "!"),
    ("urgent", "!"),
])
def test_ending_punctuation_matches_emotion(emotion, ending):
    assert prepare_text_for_speech("I NEED HELP", emotion) == "I need help" + ending


def test_existing_punctuation_is_kept():
    assert prepare_text_for_speech("IS IT READY?") == "Is it ready?"
    assert prepare_text_for_speech("HELLO!", "neutral") == "Hello!"


def test_standalone_i_is_capitalised():
    assert prepare_text_for_speech("CAN I HAVE WATER") == "Can I have water."
    assert prepare_text_for_speech("can i have water") == "Can I have water."


def test_i_inside_words_is_not_touched():
    assert prepare_text_for_speech("I LIKE ICE") == "I like ice."


def test_extra_spaces_are_tidied():
    assert prepare_text_for_speech("  I   AM   COLD  ") == "I am cold."


def test_mixed_case_text_is_not_lowercased():
    # only text that's MOSTLY capitals gets lowercased
    assert prepare_text_for_speech("Call Dr Smith") == "Call Dr Smith."


def test_empty_text_stays_empty():
    assert prepare_text_for_speech("") == ""
    assert prepare_text_for_speech("    ") == ""
