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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_json", help="output of call_score_ab.py")
    parser.add_argument("--version", default="v2")
    args = parser.parse_args()
    sweep(json.loads(Path(args.run_json).read_text(encoding="utf-8")), args.version)


if __name__ == "__main__":
    main()
