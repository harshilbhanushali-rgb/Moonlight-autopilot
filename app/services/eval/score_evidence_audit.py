"""Checks whether the scoring step's per-category evidence is real speech.

Phase A made every subscore cite the transcript words that decided it. Nothing
verified those words existed. If they are invented, the subscores are decoration
and the whole "the tier is now auditable" claim is false — so this runs before
any accuracy work, because a failure here makes the rest moot.

Reuses `app/domain/citation.py::quote_coverage`, the same word-run matcher that
gap citations are held to. That matter's already been measured once for gaps: 12
of 58 citations failed a naive verbatim match and **not one was fabricated** —
every case was real speech carrying invented `Speaker:` labels, stitched across
turns, or elided with "...". So a coverage figure below 1.0 is expected and is
not evidence of invention; a figure near 0 is.

Reads a call_score_ab run and the transcripts. Never writes to `analysis`.

    uv run python -m app.services.eval.score_evidence_audit \\
        docs/eval/2026-08-13-call-score-phaseA.json --version v2
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from app.db.models import Analysis, CallStorage
from app.db.session import SessionLocal
from app.domain.citation import quote_coverage
from app.domain.transcript import Transcript

# The threshold verify_citations enforces on gap quotes. Used here only to
# label rows, never to reject them.
ACCEPT = 0.6
# Below this, a "quote" shares almost nothing with the transcript and is the
# shape a genuine fabrication would take.
FABRICATION = 0.2

# N/A categories are told to emit this, so they carry no claim to check.
_NO_EVIDENCE = {"none", "n/a", "", "-"}


def _transcripts(analysis_ids):
    with SessionLocal() as session:
        rows = session.execute(
            select(Analysis.id, CallStorage.transcript)
            .join(CallStorage, CallStorage.avoma_recording_id == Analysis.avoma_recording_id)
            .where(Analysis.id.in_(list(analysis_ids)))
        ).all()
    out = {}
    for analysis_id, raw in rows:
        try:
            out[analysis_id] = Transcript.model_validate(raw)
        except Exception:
            continue
    return out


def audit(results, version):
    wanted = {r["id"] for r in results}
    transcripts = _transcripts(wanted)

    coverages, per_category, offenders = [], defaultdict(list), []
    skipped_na = 0

    for row in results:
        transcript = transcripts.get(row["id"])
        if transcript is None:
            continue
        for run in row["arms"].get(version) or []:
            for category in run.get("categories") or []:
                text = (category.get("evidence") or "").strip()
                if text.lower() in _NO_EVIDENCE:
                    skipped_na += 1
                    continue
                coverage = quote_coverage(text, transcript)
                coverages.append(coverage)
                per_category[category["name"]].append(coverage)
                if coverage < ACCEPT:
                    offenders.append((coverage, row["id"], category["name"], text))

    if not coverages:
        print(f"no evidence-bearing categories found for {version}")
        return

    accepted = sum(1 for c in coverages if c >= ACCEPT)
    perfect = sum(1 for c in coverages if c >= 0.999)
    suspect = [o for o in offenders if o[0] < FABRICATION]

    print(f"=== EVIDENCE AUDIT ({version}) ===")
    print(f"  quotes checked            : {len(coverages)}   ({skipped_na} N/A rows skipped)")
    print(f"  fully in the transcript   : {perfect}  ({perfect / len(coverages):.0%})")
    print(f"  >= {ACCEPT:.0%} coverage (gap bar) : {accepted}  ({accepted / len(coverages):.0%})")
    print(f"  median coverage           : {statistics.median(coverages):.2f}")
    print(f"  < {FABRICATION:.0%} — fabrication shape : {len(suspect)}  "
          f"({len(suspect) / len(coverages):.1%})")

    print(f"\n--- weakest 15 quotes ---")
    for coverage, analysis_id, name, text in sorted(offenders)[:15]:
        print(f"  {coverage:.2f}  id={analysis_id:<5}{name[:34]:<36}{text[:60]!r}")
    if not offenders:
        print("  none below the gap bar")

    print(f"\n--- categories whose evidence matches worst (mean coverage) ---")
    ranked = sorted(
        ((statistics.mean(v), k, len(v)) for k, v in per_category.items() if len(v) >= 4)
    )
    for mean_coverage, name, n in ranked[:8]:
        print(f"  {mean_coverage:.2f}  {name[:52]:<54}n={n}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_json", help="output of call_score_ab.py")
    parser.add_argument("--version", default="v2")
    args = parser.parse_args()
    audit(json.loads(Path(args.run_json).read_text(encoding="utf-8")), args.version)


if __name__ == "__main__":
    main()
