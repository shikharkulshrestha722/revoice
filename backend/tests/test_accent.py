# accent.py rules, using real scores the model gave on the project's recordings.
import csv

import pytest

from accent import HIGH_MARGIN, LOW_MARGIN, MIN_SPEECH_SECONDS, compare_accents, judge_accent, scores_text
from generation_log import log_generation


def stats(scores, speech=10.0):
    return {"speech_seconds": speech, "accent_scores": scores}


# real results (see the commit message): long clips -> Indian, short clips flip to US
NEUTRAL_CLIP_1 = stats([["indian", 0.641], ["us", 0.611], ["philippines", 0.331]], 19.1)  # margin 0.030
UPLOAD = stats([["indian", 0.613], ["us", 0.529], ["african", 0.328]], 17.7)               # margin 0.084
NEUTRAL_CLIP_2 = stats([["indian", 0.631], ["us", 0.412], ["wales", 0.272]], 13.2)        # margin 0.219
SHORT_GUIDED = stats([["us", 0.650], ["indian", 0.411], ["african", 0.375]], 1.6)         # 1.6 s of speech


def test_thresholds_are_the_documented_ones():
    assert (MIN_SPEECH_SECONDS, LOW_MARGIN, HIGH_MARGIN) == (3.0, 0.05, 0.15)


@pytest.mark.parametrize("clip, confidence", [(NEUTRAL_CLIP_1, "low"), (UPLOAD, "medium"),
                                              (NEUTRAL_CLIP_2, "high")])
def test_confidence_comes_from_the_lead_over_second_place(clip, confidence):
    j = judge_accent(clip)
    assert j["status"] == "ok" and j["label"] == "indian"
    assert j["confidence"] == confidence


def test_raw_scores_are_kept_top_three_only():
    j = judge_accent(stats([["a", 0.9], ["b", 0.5], ["c", 0.4], ["d", 0.1]]))
    assert j["top"] == [["a", 0.9], ["b", 0.5], ["c", 0.4]]
    assert j["margin"] == 0.4
    assert scores_text(j) == "a 0.900; b 0.500; c 0.400"


def test_under_three_seconds_of_speech_is_too_short_even_with_a_big_lead():
    j = judge_accent(SHORT_GUIDED)
    assert j["status"] == "too_short" and "label" not in j
    assert judge_accent(stats(SHORT_GUIDED["accent_scores"], 2.99))["status"] == "too_short"
    assert judge_accent(stats(SHORT_GUIDED["accent_scores"], 3.0))["status"] == "ok"


def test_missing_and_old_notebook_results():
    assert judge_accent(None)["status"] == "not_measured"
    assert judge_accent({"accent_scores": None})["status"] == "not_measured"
    assert judge_accent({"accent": "us", "accent_conf": 1.0}) == {"status": "old_notebook", "label": "us"}


def test_drift_only_judged_when_both_sides_are_clear():
    upload = judge_accent(UPLOAD)
    long_us = judge_accent(stats([["us", 0.7], ["indian", 0.5], ["x", 0.1]], 5.0))
    assert compare_accents(upload, long_us) == {"match": False, "reason": ""}         # real drift
    assert compare_accents(upload, judge_accent(UPLOAD))["match"] is True
    # unclear reference (lead 0.03): a different output label is NOT called drift
    assert compare_accents(judge_accent(NEUTRAL_CLIP_1), long_us) == {"match": None,
                                                                      "reason": "reference accent unclear"}
    assert compare_accents(upload, judge_accent(SHORT_GUIDED)) == {"match": None, "reason": "output too short"}
    assert compare_accents(judge_accent(None), long_us)["reason"] == "reference not measured"


def test_log_columns(tmp_path):
    path = tmp_path / "log.csv"
    metrics = {"similarity": 0.6, "reference": UPLOAD, "output": SHORT_GUIDED, "accent_match": None}
    log_generation(str(path), text="HI", engine="f5", emotion="neutral", success=True, metrics=metrics)
    row = list(csv.DictReader(open(path, newline="", encoding="utf-8")))[-1]
    assert row["ref_accent"] == "indian" and row["out_accent"] == "too_short"
    assert row["accent_match"] == ""                       # not judged: output too short
    assert row["ref_accent_scores"] == "indian 0.613; us 0.529; african 0.328"
    assert row["out_accent_scores"] == "us 0.650; indian 0.411; african 0.375"
