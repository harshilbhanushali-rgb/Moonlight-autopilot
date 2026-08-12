"""Compares two versions of the same gap rubric on the same real calls.

Built for the v1 -> v2 rewrite of the two themes that fired on evidence
disproving them (`docs/gap-rubric-review-2026-08-12.md` R1), and kept because
rubric edits are the remaining lever on gap quality, so this comparison will
recur.

**Why a same-session control and not the stored numbers.** Gap generation
reproduces only 43% of the time, and this session already produced two prompt
rewrites that looked plausible and moved nothing (`problems-and-fixes.md` 8.7,
8.13). Comparing a fresh v2 run against last week's stored v1 output would
attribute run-to-run churn to the rewrite. So both arms run here, now, over the
same transcripts, and `--nonce` is mandatory on any repeat because the gateway
serves a response cache keyed on the request messages.

Gaps are generated **without** verification, deliberately: the rewrite's job is
to stop the theme firing in the first place. A theme the verifier has to clean
up is still a theme producing noise, and measuring post-verification output
would hide exactly the effect we want to see. The rewritten theme's firings are
then verified separately, to answer the second question — when it does still
fire, does the evidence hold up?

Never writes to `analysis`.

    uv run python -m app.services.eval.rubric_version_ab \\
        --call-type discovery --theme "Incumbent Vendor Not Probed" \\
        --old-theme "No Pre-Call Research" --nonce r1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.models import Analysis, CallStorage
from app.db.session import SessionLocal
from app.domain.gap_analysis import analyse_gaps
from app.domain.gap_verification import verify_gap_claims
from app.domain.transcript import Transcript, TranscriptTurn
from app.domain.types import Verdict
from app.llm.factory import build_llm_client
from app.llm.gateway_config import load_llm_gateway_config
from app.prompts.registry import PromptRegistry
from app.services.batch.run import _PROMPTS_ROOT

logger = logging.getLogger(__name__)

# call_type as stored in `analysis` -> rubric directory name
CALL_TYPE_DIRS = {
    "Discovery": "discovery",
    "Demo": "demo",
    "Follow-up Demo": "follow_up_demo",
    "Kick-off": "kickoff",
    "Pricing/Negotiation": "pricing_negotiation",
    "Technical Integration": "technical_integration",
}


def _load_calls(session, call_type: str):
    stmt = (
        select(Analysis.id, CallStorage.transcript, CallStorage.call_metadata)
        .join(CallStorage, CallStorage.avoma_recording_id == Analysis.avoma_recording_id)
        .where(Analysis.call_type == call_type)
        .order_by(Analysis.id)
    )
    return [
        {
            "id": row[0],
            "title": (row[2] or {}).get("title"),
            "transcript": Transcript.model_validate(row[1]),
        }
        for row in session.execute(stmt).all()
    ]


def _with_nonce(transcript: Transcript, nonce: str | None) -> Transcript:
    if not nonce:
        return transcript
    last = transcript.turns[-1].start_s if transcript.turns else 0.0
    return Transcript(
        turns=[*transcript.turns, TranscriptTurn(speaker="eval", text=f"[ab {nonce}]", start_s=last)]
    )


async def _one_arm(llm_client, transcript, rubric):
    """Raw rubric output — verification deliberately not applied here."""
    result = await analyse_gaps(
        llm_client=llm_client, transcript=transcript, prompt=rubric, verification_prompts=None
    )
    return result.value


async def _verify(llm_client, gaps, transcript, verification_prompts):
    if not gaps:
        return []
    dialogue_prompt, explanation_prompt = verification_prompts
    outcome = await verify_gap_claims(
        llm_client=llm_client,
        gaps=gaps,
        transcript=transcript,
        dialogue_prompt=dialogue_prompt,
        explanation_prompt=explanation_prompt,
    )
    return [
        {
            "theme": j.gap.theme,
            "kept": j.verdict is Verdict.SUPPORTED,
            "verdict": j.verdict.value,
            "reason": j.reason,
        }
        for j in outcome.judgements
    ]


async def _run_call(
    llm_client, call, rubrics, verification_prompts, nonce, target_themes, verify_all
):
    transcript = _with_nonce(call["transcript"], nonce)
    out = {"id": call["id"], "title": call["title"], "arms": {}}
    for label, rubric in rubrics.items():
        try:
            gaps = await _one_arm(llm_client, transcript, rubric)
            target = [g for g in gaps if any(t in (g.theme or "") for t in target_themes)]
            # By default only the rewritten theme is verified, since the others
            # are unchanged between arms. `verify_all` is for the case where a
            # change is expected to alter OTHER themes' output — then "more
            # gaps" is not a result on its own, and only survival tells you
            # whether the extra ones are worth a moderator's time.
            verified = gaps if verify_all else target
            verdicts = await _verify(llm_client, verified, transcript, verification_prompts)
            out["arms"][label] = {
                "themes": [g.theme for g in gaps],
                "target_fired": len(target),
                "target_verdicts": [
                    v for v in verdicts
                    if any(t in (v["theme"] or "") for t in target_themes)
                ],
                "all_verdicts": verdicts if verify_all else None,
                "error": None,
            }
        except Exception as exc:
            logger.warning("call %s arm %s failed: %s", call["id"], label, exc)
            out["arms"][label] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


async def _run(call_type, labels, target_themes, nonce, out_path, concurrency, verify_all):
    registry = PromptRegistry(root=_PROMPTS_ROOT)
    rubric_dir = CALL_TYPE_DIRS[call_type]
    rubrics = {
        label: registry.get(
            kind="gap_rubric", call_type=rubric_dir, label=label, mode="descriptiononly"
        )
        for label in labels
    }
    verification_prompts = (
        registry.latest(kind="gap_verification", mode="dialogue"),
        registry.latest(kind="gap_verification", mode="explanation"),
    )
    gateway_config = load_llm_gateway_config()
    llm_client = build_llm_client(settings=settings, gateway_config=gateway_config)

    session = SessionLocal()
    try:
        calls = _load_calls(session, call_type)
    finally:
        session.close()

    print(
        f"{call_type}: {len(calls)} calls x {len(labels)} rubric versions "
        f"({', '.join(f'{k}={v.content_hash[:8]}' for k, v in rubrics.items())})",
        file=sys.stderr,
    )

    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(call):
        async with semaphore:
            return await _run_call(
                llm_client, call, rubrics, verification_prompts, nonce, target_themes, verify_all
            )

    try:
        results = await asyncio.gather(*(guarded(c) for c in calls))
    finally:
        await llm_client.aclose()

    Path(out_path).write_text(json.dumps(results, indent=1), encoding="utf-8")
    _report(results, labels, out_path, verify_all)


def _report(results, labels, out_path, verify_all=False):
    print(f"\n{'':<12}{'target fired':<14}{'target survived':<18}{'raw gaps':<11}{'gaps surviving'}")
    for label in labels:
        arms = [r["arms"][label] for r in results if not r["arms"][label].get("error")]
        fired = [a for a in arms if a["target_fired"]]
        verdicts = [v for a in arms for v in a["target_verdicts"]]
        kept = sum(1 for v in verdicts if v["kept"])
        total = sum(len(a["themes"]) for a in arms)
        # The figure that actually matters when other themes are expected to
        # move: raw gap count rewards noise, surviving count does not.
        if verify_all:
            allv = [v for a in arms for v in (a["all_verdicts"] or [])]
            survived = f"{sum(1 for v in allv if v['kept'])}/{len(allv)}"
        else:
            survived = "n/a"
        print(
            f"  {label:<10}{len(fired)}/{len(arms):<12}"
            f"{kept}/{len(verdicts):<16}{total:<11}{survived}"
        )
        errs = sum(1 for r in results if r["arms"][label].get("error"))
        if errs:
            print(f"             ({errs} call(s) errored)")

    if verify_all:
        print("\nper-theme: raw firings -> surviving verification")
        themes = sorted({t for r in results for label in labels
                         for t in (r["arms"][label].get("themes") or [])})
        width = max((len(t) for t in themes), default=10)
        print(f"  {'theme':<{width}}  " + "  ".join(f"{l:>9}" for l in labels))
        for theme in themes:
            cells = []
            for label in labels:
                arms = [r["arms"][label] for r in results if not r["arms"][label].get("error")]
                raw = sum(1 for a in arms for t in a["themes"] if t == theme)
                kept = sum(1 for a in arms for v in (a["all_verdicts"] or [])
                           if v["theme"] == theme and v["kept"])
                cells.append(f"{raw:>4} ->{kept:>3}")
            print(f"  {theme:<{width}}  " + "  ".join(f"{c:>9}" for c in cells))

    print("\nper-call detail for the rewritten theme:")
    for r in results:
        cells = []
        for label in labels:
            a = r["arms"][label]
            if a.get("error"):
                cells.append(f"{label}=ERR")
            else:
                vs = "".join("K" if v["kept"] else "d" for v in a["target_verdicts"])
                cells.append(f"{label}={a['target_fired']}{'(' + vs + ')' if vs else ''}")
        print(f"  id={r['id']:<5}{str(r['title'])[:44]:<46}{'  '.join(cells)}")
    print("\n  K = fired and survived verification, d = fired and was dropped")

    print("\nfull theme mix per arm:")
    for label in labels:
        counts = Counter(
            t for r in results if not r["arms"][label].get("error") for t in r["arms"][label]["themes"]
        )
        print(f"  {label}:")
        for theme, n in counts.most_common():
            print(f"     {n:>2}  {theme[:60]}")
    print(f"\nwrote {out_path}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--call-type", required=True, choices=sorted(CALL_TYPE_DIRS))
    parser.add_argument(
        "--labels", default="v1,v2", help="rubric version labels to compare, in order"
    )
    parser.add_argument(
        "--theme",
        action="append",
        required=True,
        help="substring identifying the rewritten theme; repeat to match both "
             "the old and the new title, since a rewrite usually renames it",
    )
    parser.add_argument("--nonce", default=None, help="busts the gateway response cache")
    parser.add_argument("--out", default="rubric_version_ab.json")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument(
        "--verify-all",
        action="store_true",
        help="verify every gap, not just the rewritten theme's. Needed when a "
             "change is expected to move OTHER themes too, because then raw gap "
             "counts reward noise and only survival is a result.",
    )
    args = parser.parse_args()
    asyncio.run(
        _run(
            args.call_type,
            [s.strip() for s in args.labels.split(",")],
            args.theme,
            args.nonce,
            args.out,
            args.concurrency,
            args.verify_all,
        )
    )


if __name__ == "__main__":
    main()
