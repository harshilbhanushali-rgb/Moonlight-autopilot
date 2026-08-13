"""Scores prompt versions against the hand labels in `call_score_labels`.

This is the only measurement in the call-score work that says anything about
*correctness*. Everything else — reproducibility, tier reachability, the band
sweep — measures self-consistency, which a confidently wrong prompt can ace.

Read `docs/eval/2026-08-14-call-score-grading-standard.md` before quoting a
number. The labels are one non-auditor reviewer's judgements, so this measures
agreement with that reviewer, not with Moonlight's standard.

    uv run python -m app.services.eval.call_score_accuracy \\
        docs/eval/2026-08-13-call-score-phaseA.json

Reports, per version:
  * **agreement** on the first repeat — the like-for-like accuracy figure.
  * **agreement on either repeat** — the ceiling a perfectly stable version of
    the same prompt could reach. The gap between the two is churn, not skill.
  * **bias** — signed tier distance, so "wrong" is separated into too harsh and
    too generous. A prompt that is 40% accurate because it calls everything
    Medium needs a different fix from one that is 40% accurate at random.
  * **adjacent vs opposite** errors. High->Medium is a disagreement;
    High->Low is a contradiction, and only the second would mislead a moderator
    badly.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from app.services.eval.call_score_labels import UNGRADABLE, LABELS, gradable

ORDER = {"Low": 0, "Medium": 1, "High": 2}


def _tiers(row, version):
    return [run.get("tier") for run in row["arms"].get(version) or []]


def report(results, versions):
    by_id = {row["id"]: row for row in results}
    labels = gradable()
    covered = [label for label in labels if label.analysis_id in by_id]

    print(f"hand-labelled calls          : {len(LABELS)}")
    print(f"  gradable (tier, uncontaminated): {len(labels)}")
    print(f"  also present in this run       : {len(covered)}")
    ungradable = [label for label in LABELS if label.tier == UNGRADABLE]
    if ungradable:
        print(f"  UNGRADABLE (not a Joveo sell)  : {len(ungradable)} "
              f"-> ids {[label.analysis_id for label in ungradable]}")
    borderline = [label for label in covered if label.borderline]
    print(f"  of the covered, borderline     : {len(borderline)}")
    if not covered:
        print("\nno overlap between labels and this run")
        return

    strict = [label for label in covered if not label.borderline]
    print(f"\n{'':22}{'agree (1st run)':>18}{'agree (either run)':>21}{'excl. borderline':>19}")
    for version in versions:
        first = [
            (label, _tiers(by_id[label.analysis_id], version)) for label in covered
        ]
        hit = sum(1 for label, tiers in first if tiers and tiers[0] == label.tier)
        either = sum(1 for label, tiers in first if label.tier in tiers)
        strict_hit = sum(
            1
            for label in strict
            if (_tiers(by_id[label.analysis_id], version) or [None])[0] == label.tier
        )
        print(
            f"  {version:<20}{hit}/{len(covered)} ({hit / len(covered):>3.0%})".ljust(42)
            + f"{either}/{len(covered)} ({either / len(covered):>3.0%})".rjust(14)
            + f"{strict_hit}/{len(strict)} ({strict_hit / max(len(strict), 1):>3.0%})".rjust(19)
        )

    print("\n--- BIAS: signed distance from the label (first run) ---")
    for version in versions:
        deltas = []
        for label in covered:
            tiers = _tiers(by_id[label.analysis_id], version)
            if tiers and tiers[0]:
                deltas.append(ORDER[tiers[0]] - ORDER[label.tier])
        if not deltas:
            continue
        harsher = sum(1 for d in deltas if d < 0)
        kinder = sum(1 for d in deltas if d > 0)
        opposite = sum(1 for d in deltas if abs(d) == 2)
        print(f"  {version}: {sum(1 for d in deltas if d == 0)} exact, "
              f"{kinder} too generous, {harsher} too harsh, "
              f"{opposite} opposite-tier (High<->Low)")

    print("\n--- CONFUSION (label -> first run) ---")
    for version in versions:
        matrix = Counter()
        for label in covered:
            tiers = _tiers(by_id[label.analysis_id], version)
            if tiers and tiers[0]:
                matrix[(label.tier, tiers[0])] += 1
        print(f"  {version}:")
        print("    " + "label/pred".ljust(12) + "".join(f"{t:>9}" for t in ORDER))
        for actual in ORDER:
            row = "".join(f"{matrix.get((actual, pred), 0):>9}" for pred in ORDER)
            print(f"    {actual:<12}{row}")

    print("\n--- BY CALL TYPE (first run; n>=3 only) ---")
    by_type = defaultdict(list)
    for label in covered:
        by_type[by_id[label.analysis_id]["call_type"]].append(label)
    for call_type in sorted(by_type):
        group = by_type[call_type]
        if len(group) < 3:
            continue
        cells = []
        for version in versions:
            hit = sum(
                1
                for label in group
                if (_tiers(by_id[label.analysis_id], version) or [None])[0] == label.tier
            )
            cells.append(f"{version} {hit}/{len(group)}")
        print(f"  {call_type:<24}" + "   ".join(cells))

    print("\n--- PER CALL ---")
    print(f"  {'id':<6}{'call type':<22}{'label':<9}" + "".join(f"{v:>10}" for v in versions))
    for label in sorted(covered, key=lambda x: x.analysis_id):
        row = by_id[label.analysis_id]
        cells = "".join(
            f"{(_tiers(row, v) or ['-'])[0] or '-':>10}" for v in versions
        )
        flag = " (borderline)" if label.borderline else ""
        print(f"  {label.analysis_id:<6}{row['call_type'][:20]:<22}{label.tier:<9}{cells}{flag}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_json", help="output of call_score_ab.py")
    parser.add_argument("--versions", default="v1,v2")
    args = parser.parse_args()
    report(
        json.loads(Path(args.run_json).read_text(encoding="utf-8")),
        [v.strip() for v in args.versions.split(",")],
    )


if __name__ == "__main__":
    main()
