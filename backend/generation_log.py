# ============================================================
# ReVoice — Generation log + summary (evidence for the report)
# ============================================================
# Every /speak call adds one row to data/generation_log.csv:
#
#   timestamp             when it happened (UTC)
#   utterance_id          random id for one composed sentence. "Compare both"
#                         or speaking the same sentence twice gives several rows
#                         with the same id; the summary counts the sentence once.
#   engine, emotion       what was asked for
#   voice_source          stock / emotion_clips / guided_references
#   chars, words          length of the composed text (the text itself is NOT
#                         saved, for privacy)
#   selections            pointer selections the patient used for this sentence
#   letter_by_letter      selections it would take to spell it letter by letter
#                         (one per letter and one per space)
#   selections_saved_pct  how much prediction saved: (letter_by_letter - selections)
#                         / letter_by_letter x 100
#   seconds               time for Colab to generate the audio (includes network)
#   success, error        did it work, and if not, why
#   synth_seconds         speech generation alone, measured on Colab (no network, no metrics)
#   similarity            how similar the output voice is to the person's real voice
#                         (speaker-embedding cosine similarity, 1 = identical)
#   ref_/out_accent       estimated English accent of the real voice / of the output
#   accent_match          yes if both got the same accent label
#   ref_/out_pitch_hz     average pitch of the real voice / of the output
#   ref_/out_words_per_sec  speaking rate of the real voice / of the output
# ============================================================

import csv
import os
import statistics
import threading
from collections import defaultdict
from datetime import datetime, timezone

FIELDS = ["timestamp", "utterance_id", "engine", "emotion", "voice_source", "chars", "words",
          "selections", "letter_by_letter", "selections_saved_pct", "seconds", "success", "error",
          # voice evaluation (added later, so they come last)
          "synth_seconds", "similarity", "ref_accent", "out_accent", "accent_match",
          "ref_pitch_hz", "out_pitch_hz", "ref_words_per_sec", "out_words_per_sec"]

_lock = threading.Lock()   # two requests at once must not mix up their lines


def tidy(text: str) -> str:
    """'  I  NEED   WATER ' -> 'I NEED WATER' (the sentence as it would be spelled)."""
    return " ".join((text or "").split())


def letter_by_letter_selections(text: str) -> int:
    """Selections needed with no predictions: one per letter, one per space."""
    return len(tidy(text))


def saved_percent(selections: int, letter_by_letter: int):
    if not letter_by_letter or selections is None:
        return None
    return round(100 * (letter_by_letter - selections) / letter_by_letter, 1)


def _blank(value):
    return "" if value is None else value


def metric_columns(metrics) -> dict:
    """Turns the metrics from Colab (nested, any part may be null) into flat CSV columns."""
    m = metrics or {}
    ref = m.get("reference") or {}
    out = m.get("output") or {}
    match = m.get("accent_match")
    return {
        "similarity": _blank(m.get("similarity")),
        "ref_accent": ref.get("accent") or "",
        "out_accent": out.get("accent") or "",
        "accent_match": "" if match is None else ("yes" if match else "no"),
        "ref_pitch_hz": _blank(ref.get("pitch_hz")),
        "out_pitch_hz": _blank(out.get("pitch_hz")),
        "ref_words_per_sec": _blank(ref.get("words_per_sec")),
        "out_words_per_sec": _blank(out.get("words_per_sec")),
    }


def _upgrade_old_file(path):
    """An older log may have fewer columns. Rewrite it with the current header
    (old rows keep their values, new columns stay empty) so the CSV stays valid."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames == FIELDS:
            return
        rows = list(reader)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def log_generation(path, *, text, engine, emotion, voice_source="", seconds=None,
                   success=False, error="", selections=None, utterance_id="",
                   synth_seconds=None, metrics=None):
    """Appends one row to the CSV (creating it, with a header line, if needed)."""
    clean = tidy(text)
    needed = letter_by_letter_selections(clean)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "utterance_id": utterance_id,
        "engine": engine,
        "emotion": emotion,
        "voice_source": voice_source,
        "chars": len(clean),
        "words": len(clean.split()),
        "selections": "" if selections is None else selections,
        "letter_by_letter": needed,
        "selections_saved_pct": "" if selections is None else saved_percent(selections, needed),
        "seconds": "" if seconds is None else round(seconds, 2),
        "success": "yes" if success else "no",
        "error": (error or "").replace("\n", " ")[:300],
        "synth_seconds": _blank(synth_seconds),
        **metric_columns(metrics),
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with _lock:
        new_file = not os.path.exists(path) or os.path.getsize(path) == 0
        if not new_file:
            _upgrade_old_file(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow(row)
    return row


def read_rows(path) -> list:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize(path) -> dict:
    """Average generation time per engine, and how many selections prediction saved."""
    rows = read_rows(path)

    # ---- generation time per engine (successful generations only) ----
    by_engine = defaultdict(list)
    for r in rows:
        by_engine[r["engine"]].append(r)
    engines = {}
    for engine, items in sorted(by_engine.items()):
        ok = [r for r in items if r["success"] == "yes"]
        times = [t for t in (_number(r["seconds"]) for r in ok) if t is not None]
        synth = [t for t in (_number(r.get("synth_seconds")) for r in ok) if t is not None]
        sims = [s for s in (_number(r.get("similarity")) for r in ok) if s is not None]
        matches = [r.get("accent_match") for r in ok if r.get("accent_match") in ("yes", "no")]
        engines[engine] = {
            "requests": len(items),
            "succeeded": len(ok),
            "failed": len(items) - len(ok),
            "success_rate_pct": round(100 * len(ok) / len(items), 1),
            "avg_seconds": round(statistics.mean(times), 2) if times else None,
            "median_seconds": round(statistics.median(times), 2) if times else None,
            "min_seconds": round(min(times), 2) if times else None,
            "max_seconds": round(max(times), 2) if times else None,
            "avg_synth_seconds": round(statistics.mean(synth), 2) if synth else None,
            "avg_similarity": round(statistics.mean(sims), 3) if sims else None,
            "similarity_measured": len(sims),
            "accent_match_rate_pct": round(100 * matches.count("yes") / len(matches), 1) if matches else None,
            "accent_measured": len(matches),
        }

    # ---- selections saved, counted once per sentence (the latest row wins) ----
    latest = {}
    for r in rows:
        if r.get("utterance_id") and _number(r.get("selections")) is not None:
            latest[r["utterance_id"]] = r
    sentences = list(latest.values())
    sel = [_number(r["selections"]) for r in sentences]
    need = [_number(r["letter_by_letter"]) for r in sentences]
    saved = [s for s in (_number(r["selections_saved_pct"]) for r in sentences) if s is not None]
    selections = {
        "sentences": len(sentences),
        "avg_selections": round(statistics.mean(sel), 1) if sel else None,
        "avg_letter_by_letter": round(statistics.mean(need), 1) if need else None,
        # average of each sentence's own saving
        "avg_saved_pct": round(statistics.mean(saved), 1) if saved else None,
        # all sentences pooled together (long sentences count for more)
        "overall_saved_pct": round(100 * (sum(need) - sum(sel)) / sum(need), 1) if sum(need) else None,
    }

    return {
        "total_generations": len(rows),
        "succeeded": sum(r["success"] == "yes" for r in rows),
        "engines": engines,
        "selections": selections,
    }
