"""Reads Colab's accent estimate: top-3 raw cosine scores (-1..1, not percentages) per clip.
Same rules as frontend/metrics_format.js."""

# Under 3 s of speech the label flipped between Indian and US on our own clips.
MIN_SPEECH_SECONDS = 3.0
LOW_MARGIN = 0.05
HIGH_MARGIN = 0.15          # lead over second place: <0.05 low, <0.15 medium, else high (not calibrated)


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def judge_accent(stats) -> dict:
    """Judges one clip's accent; status is ok, too_short, not_measured or old_notebook."""
    stats = stats or {}
    scores = stats.get("accent_scores")
    speech = stats.get("speech_seconds")
    if not scores:
        if stats.get("accent"):          # Colab before v8: label + unreliable percentage
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
    """Did the output keep the accent? match is True/False, or None with a reason
    unless both sides are long enough and at least medium confidence."""
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
    """'indian 0.638; us 0.511; scotland 0.282', for the CSV log."""
    return "; ".join(f"{label} {score:.3f}" for label, score in judgement.get("top", []))
