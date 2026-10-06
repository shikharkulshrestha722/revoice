# ============================================================
# ReVoice — How to read the accent estimate
# ============================================================
# Colab (API v8) sends, for the real voice AND the output:
#   accent_scores   the 3 most likely English accents with their RAW scores,
#                   e.g. [["indian", 0.638], ["us", 0.511], ["scotland", 0.282]]
#                   (cosine scores from -1 to 1, NOT percentages)
#   speech_seconds  how much actual speech they were measured on
#
# Rules (the web page uses the SAME rules, see frontend/metrics_format.js):
#   - under 3 s of speech: "too short", no accent given. On our own clips the
#     answer flipped between Indian and US from one 3 s window to the next.
#   - confidence = how far the top score is ahead of the second:
#       under 0.05 = low, 0.05 to 0.15 = medium, 0.15 or more = high
#     (judgement calls from our clips, where the winner was 0.03-0.22 ahead;
#     they are NOT calibrated probabilities)
#   - "same / different accent" is only decided when BOTH the real voice and
#     the output are long enough and at least medium confidence. Otherwise a
#     "different" result could just be the classifier being unsure.
# ============================================================

MIN_SPEECH_SECONDS = 3.0
LOW_MARGIN = 0.05
HIGH_MARGIN = 0.15


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def judge_accent(stats) -> dict:
    """What can be said about one piece of audio's accent.
    status: ok / too_short / not_measured / old_notebook"""
    stats = stats or {}
    scores = stats.get("accent_scores")
    speech = stats.get("speech_seconds")
    if not scores:
        if stats.get("accent"):          # Colab before v8: a label + an unreliable percentage
            return {"status": "old_notebook", "label": stats["accent"]}
        return {"status": "not_measured"}
    top = [[str(label), float(score)] for label, score in scores[:3]]
    if _number(speech) and speech < MIN_SPEECH_SECONDS:
        return {"status": "too_short", "speech_seconds": speech, "top": top}
    margin = round(top[0][1] - top[1][1], 3) if len(top) > 1 else None
    if margin is None or margin < LOW_MARGIN:
        confidence = "low"
    elif margin < HIGH_MARGIN:
        confidence = "medium"
    else:
        confidence = "high"
    return {"status": "ok", "label": top[0][0], "top": top, "margin": margin,
            "confidence": confidence, "speech_seconds": speech}


def compare_accents(ref: dict, out: dict) -> dict:
    """Did the output keep the person's accent? match is True/False, or None
    (with a reason) when it can't fairly be judged."""
    def usable(j, who):
        if j["status"] == "too_short":
            return f"{who} too short"
        if j["status"] != "ok":
            return f"{who} not measured"
        if j["confidence"] == "low":
            return f"{who} accent unclear"
        return None
    reason = usable(ref, "reference") or usable(out, "output")
    if reason:
        return {"match": None, "reason": reason}
    return {"match": ref["label"] == out["label"], "reason": ""}


def scores_text(judgement: dict) -> str:
    """'indian 0.638; us 0.511; scotland 0.282' for the CSV log."""
    return "; ".join(f"{label} {score:.3f}" for label, score in judgement.get("top", []))
