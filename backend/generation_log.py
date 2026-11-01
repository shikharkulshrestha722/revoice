"""Generation log: one CSV row per /speak call, and summarize() for /stats/summary and report.py.
English sentences are not stored, for privacy. Indian-language rows (IndicF5) DO store the typed
text and its conversion, so a wrong translation or transliteration can be checked later."""

import csv
import os
import statistics
import threading
from collections import defaultdict
from datetime import datetime, timezone

from accent import compare_accents, judge_accent, scores_text

FIELDS = ["timestamp", "utterance_id", "engine", "emotion", "voice_source", "chars", "words",
          "selections", "letter_by_letter", "selections_saved_pct", "seconds", "success", "error",
          # newer columns are appended so older logs stay readable
          "synth_seconds", "similarity", "ref_accent", "out_accent", "accent_match",
          "ref_pitch_hz", "out_pitch_hz", "ref_words_per_sec", "out_words_per_sec",
          "ref_mode", "ref_files", "ref_seconds", "ref_text",
          "ref_accent_scores", "out_accent_scores",
          "input_method",
          "language", "text_mode", "original_text", "converted_text"]

_lock = threading.Lock()   # concurrent requests must not interleave rows


def tidy(text: str) -> str:
    """'  I  NEED   WATER ' -> 'I NEED WATER'."""
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
    """Flattens Colab's metrics (any part may be null) into CSV columns."""
    m = metrics or {}
    ref = m.get("reference") or {}
    out = m.get("output") or {}
    ref_j, out_j = judge_accent(ref), judge_accent(out)
    if ref_j["status"] == "old_notebook" or out_j["status"] == "old_notebook":
        match = m.get("accent_match")                 # before v8 Colab decided it
    else:
        match = compare_accents(ref_j, out_j)["match"] if m.get("reference") else None

    def accent_label(j):
        return j.get("label", "") if j["status"] in ("ok", "old_notebook") else             ("too_short" if j["status"] == "too_short" else "")
    return {
        "similarity": _blank(m.get("similarity")),
        "ref_accent": accent_label(ref_j),
        "out_accent": accent_label(out_j),
        "accent_match": "" if match is None else ("yes" if match else "no"),
        "ref_accent_scores": scores_text(ref_j),
        "out_accent_scores": scores_text(out_j),
        "ref_pitch_hz": _blank(ref.get("pitch_hz")),
        "out_pitch_hz": _blank(out.get("pitch_hz")),
        "ref_words_per_sec": _blank(ref.get("words_per_sec")),
        "out_words_per_sec": _blank(out.get("words_per_sec")),
    }


def reference_columns(voice_reference, ref_text) -> dict:
    """The recordings sent to Colab (app.voice_reference_info) as CSV columns."""
    ref = voice_reference or {}
    return {
        "ref_mode": ref.get("mode", ""),
        "ref_files": " | ".join(f"{c['name']} {_blank(c['duration_s'])}s" for c in ref.get("clips", [])),
        "ref_seconds": _blank(ref.get("total_seconds")),
        "ref_text": " ".join((ref_text or "").split())[:300],
    }


def _upgrade_old_file(path):
    """Rewrites an older log with the current header; new columns stay empty."""
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
                   synth_seconds=None, metrics=None, voice_reference=None, ref_text=None,
                   input_method="", language="en", text_mode="", converted_text=""):
    """Appends one row, creating the file with a header if needed."""
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
        **reference_columns(voice_reference, ref_text),
        "input_method": input_method,
        "language": language,
        # only for Indian languages (see the top of this file); English text is never stored
        "text_mode": text_mode if language != "en" else "",
        "original_text": clean[:300] if language != "en" else "",
        "converted_text": tidy(converted_text)[:300] if language != "en" else "",
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
    """Generation time and voice metrics per engine, and selections saved by prediction."""
    rows = read_rows(path)

    # per engine, successful generations only
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

    # selections saved: each sentence counted once (latest row wins)
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
        # mean of per-sentence savings
        "avg_saved_pct": round(statistics.mean(saved), 1) if saved else None,
        # pooled, so longer sentences weigh more
        "overall_saved_pct": round(100 * (sum(need) - sum(sel)) / sum(need), 1) if sum(need) else None,
    }

    return {
        "total_generations": len(rows),
        "succeeded": sum(r["success"] == "yes" for r in rows),
        "engines": engines,
        "selections": selections,
    }
