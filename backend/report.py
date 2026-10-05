# ============================================================
# ReVoice — Print a summary of the generation log (for the report)
# ============================================================
# Usage (from the backend folder):
#     py -3.10 report.py                     reads data/generation_log.csv
#     py -3.10 report.py path\to\other.csv   reads another log
#
# The same numbers are available while the server runs at
#     http://localhost:8000/stats/summary
# ============================================================

import os
import sys

from generation_log import summarize

ENGINE_NAMES = {"xtts": "XTTS-v2", "f5": "F5-TTS"}


def show(value, suffix=""):
    return "-" if value is None else f"{value}{suffix}"


def main():
    default = os.path.join(os.environ.get("REVOICE_DATA_DIR", os.path.join(os.path.dirname(__file__), "data")),
                           "generation_log.csv")
    path = sys.argv[1] if len(sys.argv) > 1 else default
    if not os.path.exists(path):
        print(f"No log yet at {path}. Speak a few sentences in the app first.")
        return

    s = summarize(path)
    print(f"ReVoice generation summary  ({path})")
    print(f"{s['total_generations']} speech requests, {s['succeeded']} succeeded\n")

    print("Generation time per engine (successful requests, seconds, includes network)")
    header = f"{'Engine':<10}{'Requests':>9}{'OK':>5}{'Failed':>8}{'Success':>9}{'Average':>9}{'Median':>8}{'Min':>7}{'Max':>7}"
    print(header)
    print("-" * len(header))
    for engine, e in s["engines"].items():
        print(f"{ENGINE_NAMES.get(engine, engine):<10}{e['requests']:>9}{e['succeeded']:>5}{e['failed']:>8}"
              f"{show(e['success_rate_pct'], '%'):>9}{show(e['avg_seconds']):>9}{show(e['median_seconds']):>8}"
              f"{show(e['min_seconds']):>7}{show(e['max_seconds']):>7}")

    sel = s["selections"]
    print("\nSelections saved by prediction (each sentence counted once)")
    print(f"  Sentences measured:                  {sel['sentences']}")
    print(f"  Average selections used:             {show(sel['avg_selections'])}")
    print(f"  Average letter-by-letter selections: {show(sel['avg_letter_by_letter'])}")
    print(f"  Average saving per sentence:         {show(sel['avg_saved_pct'], '%')}")
    print(f"  Overall saving (all pooled):         {show(sel['overall_saved_pct'], '%')}")


if __name__ == "__main__":
    main()
