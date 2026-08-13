"""Re-derives call-score tiers from a saved call_score_ab run under different
band thresholds, without calling the gateway again.

This is only possible because Phase A stores the subscores: the mean per run is
already recorded, so "what would the tiers have been at 3.0 / 4.0?" is
arithmetic on a file rather than another 188 requests. Before Phase A the
question could not be asked at all.

Two things it answers, and one it cannot:

  * **Would different thresholds be more reproducible?** Agreement between the
    two repeats is a pure function of where the edges sit relative to each
    call's two means, so this sweep measures it exactly.
  * **Would different thresholds make every tier reachable on every call type?**
    Same reasoning.
  * It says **nothing about whether the resulting tiers are right.** Moving a
    threshold until the numbers look good is overfitting to 47 calls, which is
    why app/domain/scoring.py keeps the business team's 4.2 / 2.8 and this is a
    reporting tool rather than a tuner. Any change it motivates needs its own
    justification, not just a better score here.

    uv run python -m app.services.eval.call_score_bands docs/eval/....json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from app.domain.scoring import HIGH_THRESHOLD, MEDIUM_THRESHOLD

TIERS = ("High", "Medium", "Low")

# Candidate edges to sweep. The pairs are (medium_edge, high_edge); the first is
# the shipped setting so it appears in the table as the comparison point.
CANDIDATES = [
    (MEDIUM_THRESHOLD, HIGH_THRESHOLD),
    (2.8, 4.0),
    (3.0, 4.0),
    (3.0, 4.2),
    (3.2, 4.0),
    (3.25, 3.9),
    (3.3, 4.1),
    (3.5, 4.2),
]


def _tier(mean: float, medium_edge: float, high_edge: float) -> str:
    if mean >= high_edge:
        return "High"
    if mean >= medium_edge:
        return "Medium"
    return "Low"


def _means(results, version):
    """(call, [mean per repeat]) for calls where every repeat produced a mean."""
    out = []
    for row in results:
        runs = row["arms"].get(version) or []
        means = [run.get("mean") for run in runs]
        if means and all(m is not None for m in means):
            out.append((row, means))
    return out


def sweep(results, version):
    pairs = _means(results, version)
    if not pairs:
        print(f"no stored means for {version} — was it a tier-only arm?")
        return

    by_type = defaultdict(list)
    for row, means in pairs:
        by_type[row["call_type"]].append(means)

    print(f"\n{'medium/high edge':<20}{'agreement':<13}{'unreachable tiers':<20}{'H/M/L (first repeat)'}")
    for medium_edge, high_edge in CANDIDATES:
        agree = sum(
            1
            for _, means in pairs
            if len({_tier(m, medium_edge, high_edge) for m in means}) == 1
        )
        blocked = 0
        for call_type, runs in by_type.items():
            if len(runs) < 4:
                continue
            seen = {_tier(m, medium_edge, high_edge) for means in runs for m in means}
            if len(seen) < 3:
                blocked += 1
        counts = Counter(_tier(means[0], medium_edge, high_edge) for _, means in pairs)
        mix = "/".join(str(counts.get(t, 0)) for t in TIERS)
        shipped = "  <- shipped" if (medium_edge, high_edge) == (MEDIUM_THRESHOLD, HIGH_THRESHOLD) else ""
        print(
            f"  {medium_edge:.2f} / {high_edge:.2f}      "
            f"{agree}/{len(pairs)} ({agree / len(pairs):>3.0%})  "
            f"{blocked} of {len(by_type):<15}{mix}{shipped}"
        )

    print("\n  'unreachable tiers' counts call types (n>=4) that never produce all three.")
    print("  A threshold that maximises agreement by collapsing everything into one")
    print("  tier is worse, not better — read the two columns together.")

    print("\nmean by call type (all repeats):")
    for call_type in sorted(by_type):
        flat = sorted(m for means in by_type[call_type] for m in means)
        print(f"  {call_type:<24}n={len(flat):<4}"
              f"min {flat[0]:.2f}  median {flat[len(flat) // 2]:.2f}  max {flat[-1]:.2f}")


def sweep_against_labels(results, version):
    """Which thresholds best reproduce the hand labels.

    This is the sweep the module header says is NOT safe to do on
    self-consistency alone — and it becomes safe here, because agreement with an
    independent human grade cannot be gamed by collapsing everything into one
    tier the way re-run agreement can. A threshold that calls every call Medium
    scores at most the Medium base rate against labels, and badly.

    Still a fit to 47 calls at most, so treat the winner as a hypothesis to
    confirm on the next batch of labelled calls, not as a setting to ship blind.
    """
    from app.services.eval.call_score_labels import gradable

    by_id = {row["id"]: row for row in results}
    pairs = []
    for label in gradable():
        row = by_id.get(label.analysis_id)
        if row is None:
            continue
        runs = row["arms"].get(version) or []
        means = [run.get("mean") for run in runs if run.get("mean") is not None]
        if means:
            pairs.append((label, means))

    if not pairs:
        print(f"\nno labelled calls with stored means for {version}")
        return

    print(f"\n=== THRESHOLDS vs HAND LABELS ({version}, n={len(pairs)}) ===")
    print(f"{'medium/high edge':<22}{'agrees (1st run)':>18}{'too harsh':>11}{'too kind':>10}")

    # A wider grid than CANDIDATES: with labels there is a real objective to
    # optimise, so it is worth seeing the shape rather than a few guesses.
    grid = [(m / 100, h / 100) for m in range(250, 400, 10) for h in range(380, 490, 10) if h > m]
    scored = []
    for medium_edge, high_edge in grid:
        hits = harsh = kind = 0
        for label, means in pairs:
            predicted = _tier(means[0], medium_edge, high_edge)
            if predicted == label.tier:
                hits += 1
            elif TIERS.index(predicted) > TIERS.index(label.tier):
                harsh += 1  # TIERS is High,Medium,Low so a higher index is lower
            else:
                kind += 1
        scored.append((hits, medium_edge, high_edge, harsh, kind))

    scored.sort(reverse=True)
    for hits, medium_edge, high_edge, harsh, kind in scored[:8]:
        shipped = "  <- shipped" if (medium_edge, high_edge) == (MEDIUM_THRESHOLD, HIGH_THRESHOLD) else ""
        print(f"  {medium_edge:.2f} / {high_edge:.2f}        "
              f"{hits}/{len(pairs)} ({hits / len(pairs):>3.0%})".rjust(16)
              + f"{harsh:>11}{kind:>10}{shipped}")

    current = sum(
        1 for label, means in pairs
        if _tier(means[0], MEDIUM_THRESHOLD, HIGH_THRESHOLD) == label.tier
    )
    print(f"\n  shipped {MEDIUM_THRESHOLD} / {HIGH_THRESHOLD}: "
          f"{current}/{len(pairs)} ({current / len(pairs):.0%})")
    print("  Best-on-this-sample is an upper bound - it is fitted to these calls.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_json", help="output of call_score_ab.py")
    parser.add_argument("--version", default="v2")
    parser.add_argument(
        "--labels",
        action="store_true",
        help="also sweep thresholds against the hand labels in call_score_labels",
    )
    args = parser.parse_args()
    results = json.loads(Path(args.run_json).read_text(encoding="utf-8"))
    sweep(results, args.version)
    if args.labels:
        sweep_against_labels(results, args.version)


if __name__ == "__main__":
    main()
