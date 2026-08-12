"""Scores a verification replay against the hand labels from the gap audit.

Closes the loop that `verification_replay.py` opens: the replay says which gaps
the verifier kept, this says whether keeping them was right.

    uv run python -m app.services.eval.verification_replay --nonce run3 --out run3.json
    uv run python -m app.services.eval.score_verification run3.json

**The number that matters is wrong drops, not bad gaps removed.** A verifier
that rejects everything scores perfectly on the bad gaps and destroys the
product. Retention of gaps a human judged good is the cost side of the trade,
and it is the figure to watch when tuning the verification prompts.

Read `gap_audit_labels` before quoting any percentage from this — the labels are
one reviewer's, and the sample is not random.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from app.services.eval.gap_audit_labels import lookup

# Themes that fired on 75-100% of their call type in the 2026-08-12 audit. A
# saturated theme dropping hard here is the signal that its definition, not the
# verifier, is the problem — see docs/gap-rubric-review-2026-08-12.md.
SATURATED_THEMES = (
    "IC-Level Only at Pricing Stage",
    "No Committed Timeline",
    "Escalation Path Not Established",
    "Scope Not Locked Before Kick-off",
    "Success Metrics Not Agreed at Start",
    "Kick-off Agenda Not Proactively Set",
)


def score(results: list[dict]) -> None:
    judged = [r for r in results if r.get("error") is None]
    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    wrong_drops, wrong_keeps, borderline = [], [], []

    for row in judged:
        label = lookup(row["analysis_id"], row["theme"])
        if label is None:
            continue
        should_keep, is_borderline, note = label
        kept = bool(row["kept"])
        entry = (row["analysis_id"], row["theme"], row.get("verdict"), note)
        if is_borderline:
            borderline.append((entry, should_keep, kept))
        elif should_keep and kept:
            counts["tp"] += 1
        elif should_keep:
            counts["fn"] += 1
            wrong_drops.append(entry)
        elif not kept:
            counts["tn"] += 1
        else:
            counts["fp"] += 1
            wrong_keeps.append(entry)

    labelled = sum(counts.values())
    print(f"gaps judged by verifier : {len(judged)}/{len(results)}")
    print(f"of those, hand-labelled : {labelled} (excluding {len(borderline)} borderline)\n")
    if not labelled:
        print("no labelled gaps in this file — is it a replay of the audited corpus?")
        return

    print("                     verifier KEPT   verifier DROPPED")
    print(f"  label = good gap        {counts['tp']:>4}             {counts['fn']:>4}   <- wrong drops")
    print(f"  label = bad gap         {counts['fp']:>4}             {counts['tn']:>4}")
    print(f"\nagreement with manual review : "
          f"{(counts['tp'] + counts['tn']) / labelled:.0%}")
    if counts["tp"] + counts["fn"]:
        print(f"good gaps retained           : {counts['tp']}/{counts['tp'] + counts['fn']} "
              f"({counts['tp'] / (counts['tp'] + counts['fn']):.0%})  <- the cost side")
    if counts["tn"] + counts["fp"]:
        print(f"bad gaps removed             : {counts['tn']}/{counts['tn'] + counts['fp']} "
              f"({counts['tn'] / (counts['tn'] + counts['fp']):.0%})")

    for title, rows in (("WRONG DROPS (real gaps killed)", wrong_drops),
                        ("WRONG KEEPS (bad gaps that survived)", wrong_keeps)):
        print(f"\n--- {title} ---")
        for analysis_id, theme, verdict, note in rows:
            print(f"  id={analysis_id:<5}{str(verdict):<14}{str(theme)[:40]:<42}{note}")
        if not rows:
            print("  none")

    print("\n--- BORDERLINE (excluded from the score) ---")
    for (analysis_id, theme, verdict, note), should_keep, kept in borderline:
        flag = "agree" if should_keep == kept else "differ"
        print(f"  {flag:<7}id={analysis_id:<5}{str(verdict):<14}{str(theme)[:38]:<40}{note}")

    print("\n--- SATURATED THEMES (fired on 75-100% of their call type) ---")
    by_theme = defaultdict(list)
    for row in judged:
        by_theme[row["theme"]].append(row)
    for theme in SATURATED_THEMES:
        rows = by_theme.get(theme, [])
        if rows:
            kept = sum(1 for r in rows if r["kept"])
            verdicts: dict[str, int] = {}
            for r in rows:
                verdicts[str(r.get("verdict"))] = verdicts.get(str(r.get("verdict")), 0) + 1
            print(f"  {theme:<40} kept {kept}/{len(rows)}  {verdicts}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay_json", help="output of verification_replay.py")
    args = parser.parse_args()
    score(json.loads(Path(args.replay_json).read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
