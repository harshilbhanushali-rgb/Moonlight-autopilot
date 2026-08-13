"""Replays the gap entailment verifier over gaps already stored in `analysis`.

Why replay rather than re-run the batch: at the measured 43% reproduction rate,
re-running gap analysis changes the gap set for reasons that have nothing to do
with the verifier, so no before/after comparison is attributable. Here the gap
step does not run at all — the stored gaps are the fixed input, and the only
thing exercised is verification. Same gaps in, verdicts out.

Like `reasoning_effort_ab.py`, this **never writes to `analysis`**. Those rows
are the baseline for prompt comparisons and must survive; results go to a JSON
file for review.

    uv run python -m app.services.eval.verification_replay --nonce run1
    uv run python -m app.services.eval.verification_replay --limit 5 --out x.json
    uv run python -m app.services.eval.verification_replay --framing neutral --nonce n1

`--nonce` matters on any repeat run: the gateway serves a response cache keyed
on the request messages, so an unvaried replay silently scores cached answers
from the previous run instead of fresh ones.

`--framing neutral` swaps the shipped "does this quote support this claim?"
question for "does this call exhibit this theme?", holding the context each gap
kind receives constant so only the framing differs. Score either arm the same
way, with `score_verification.py`, and read the wrong-drop count first.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.models import Analysis, CallStorage
from app.db.session import SessionLocal
from app.domain.gap_verification import DEFAULT_WINDOW_TURNS, verify_gap_claims
from app.domain.transcript import Transcript, TranscriptSpeaker, TranscriptTurn
from app.domain.types import Gap, Verdict
from app.llm.factory import build_llm_client
from app.llm.gateway_config import load_llm_gateway_config
from app.prompts.registry import PromptRegistry
from app.services.batch.run import _PROMPTS_ROOT
from app.services.eval.neutral_framing import (
    NEUTRAL_DIALOGUE_PROMPT,
    NEUTRAL_EXPLANATION_PROMPT,
    dialogue_body,
    explanation_body,
    is_kept,
    judge_presence,
)

logger = logging.getLogger(__name__)


def _load_rows(session, limit: int | None):
    stmt = (
        select(Analysis, CallStorage)
        .join(CallStorage, CallStorage.avoma_recording_id == Analysis.avoma_recording_id)
        .where(Analysis.status == "processed")
        .order_by(Analysis.id)
    )
    if limit:
        stmt = stmt.limit(limit)
    return [
        {
            "id": analysis.id,
            "recording_id": analysis.avoma_recording_id,
            "call_type": analysis.call_type,
            "title": (storage.call_metadata or {}).get("title"),
            "gaps": [Gap(**g) for g in (analysis.risk_gap_analysis or [])],
            "transcript": Transcript.model_validate(storage.transcript),
        }
        for analysis, storage in session.execute(stmt).all()
    ]


def _with_nonce(transcript: Transcript, nonce: str | None) -> Transcript:
    if not nonce:
        return transcript
    last = transcript.turns[-1].start_s if transcript.turns else 0.0
    # Marked is_rep so the nonce can never read as client speech if this
    # transcript is passed through the input gate.
    eval_id = max((s.id for s in transcript.speakers), default=-1) + 1
    return Transcript(
        speakers=[
            *transcript.speakers,
            TranscriptSpeaker(id=eval_id, name="eval", email=None, is_rep=True),
        ],
        turns=[
            *transcript.turns,
            TranscriptTurn(
                speaker="eval", speaker_id=eval_id, text=f"[replay {nonce}]", start_s=last
            ),
        ],
    )


async def _judge_production(llm_client, gap, transcript, prompts, batch_size):
    dialogue_prompt, explanation_prompt = prompts
    outcome = await verify_gap_claims(
        llm_client=llm_client,
        gaps=[gap],
        transcript=transcript,
        dialogue_prompt=dialogue_prompt,
        explanation_prompt=explanation_prompt,
        batch_size=batch_size,
    )
    (judgement,) = outcome.judgements
    return (
        judgement.verdict is Verdict.SUPPORTED,
        judgement.verdict.value,
        judgement.reason,
        judgement.evidence_quote,
    )


async def _judge_neutral(llm_client, gap, transcript):
    """The reframed arm: asks whether the call exhibits the theme, without
    showing the claim or the gap's own quote. Same context per gap kind as
    production, so framing is the only thing that differs."""
    if gap.evidence_type == "dialogue":
        prompt_text, key = NEUTRAL_DIALOGUE_PROMPT, "neutral_dialogue"
        body = dialogue_body(transcript, [(0, gap)], DEFAULT_WINDOW_TURNS)
    else:
        prompt_text, key = NEUTRAL_EXPLANATION_PROMPT, "neutral_explanation"
        body = explanation_body(transcript, [(0, gap)])

    judged = await judge_presence(
        llm_client, prompt_text=prompt_text, body=body, response_key=key, batch=[(0, gap)]
    )
    answer = judged[0]
    return is_kept(answer.presence), answer.presence.value, answer.reason, answer.evidence_quote


async def _replay_one(llm_client, row, prompts, batch_size, nonce, framing):
    """Verifies one call's gaps. Runs each gap alone so a single verdict can be
    attributed to a single gap — batching is what production does, but it would
    make a per-gap scorecard ambiguous if the model's verdicts interacted."""
    transcript = _with_nonce(row["transcript"], nonce)
    out = []
    for gap in row["gaps"]:
        try:
            if framing == "neutral":
                kept, verdict, reason, quote = await _judge_neutral(llm_client, gap, transcript)
            else:
                kept, verdict, reason, quote = await _judge_production(
                    llm_client, gap, transcript, prompts, batch_size
                )
            error = None
        except Exception as exc:  # one bad gap must not abandon the sweep
            kept, verdict, reason, quote = None, None, None, None
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("verification failed for id=%s %r: %s", row["id"], gap.theme, error)
        out.append(
            {
                "analysis_id": row["id"],
                "call_type": row["call_type"],
                "title": row["title"],
                "theme": gap.theme,
                "evidence_type": gap.evidence_type,
                "timestamp": gap.timestamp,
                "evidence": gap.evidence,
                "kept": kept,
                "verdict": verdict,
                # Kept so a wrong verdict can be diagnosed without re-running:
                # the first pass recorded only the label, which made the seven
                # misses far harder to explain than they needed to be.
                "reason": reason,
                "evidence_quote": quote,
                "error": error,
            }
        )
    return out


async def _run(limit, out_path, batch_size, nonce, concurrency, framing):
    registry = PromptRegistry(root=_PROMPTS_ROOT)
    prompts = (
        registry.latest(kind="gap_verification", mode="dialogue"),
        registry.latest(kind="gap_verification", mode="explanation"),
    )
    gateway_config = load_llm_gateway_config()
    llm_client = build_llm_client(settings=settings, gateway_config=gateway_config)

    session = SessionLocal()
    try:
        rows = _load_rows(session, limit)
    finally:
        session.close()

    total_gaps = sum(len(r["gaps"]) for r in rows)
    print(
        f"replaying {framing} framing over {len(rows)} calls / {total_gaps} gaps",
        file=sys.stderr,
    )

    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(row):
        async with semaphore:
            return await _replay_one(llm_client, row, prompts, batch_size, nonce, framing)

    try:
        results = [r for batch in await asyncio.gather(*(guarded(r) for r in rows)) for r in batch]
    finally:
        await llm_client.aclose()

    Path(out_path).write_text(json.dumps(results, indent=1), encoding="utf-8")
    _report(results, out_path)


def _report(results, out_path):
    judged = [r for r in results if r["error"] is None]
    kept = [r for r in judged if r["kept"]]
    print(f"\ngaps judged: {len(judged)}/{len(results)}   errors: {len(results) - len(judged)}")
    if not judged:
        return
    print(f"kept: {len(kept)} ({len(kept)/len(judged):.0%})   "
          f"dropped: {len(judged)-len(kept)} ({1-len(kept)/len(judged):.0%})")
    print("\nverdicts:", dict(Counter(r["verdict"] for r in judged)))

    print("\nby evidence type:")
    for et in ("dialogue", "explanation"):
        rows = [r for r in judged if r["evidence_type"] == et]
        if rows:
            k = sum(1 for r in rows if r["kept"])
            print(f"  {et:<12} {k}/{len(rows)} kept ({k/len(rows):.0%})")

    print("\nby theme (drop rate — a saturated theme dropping hard is the signal):")
    by_theme = defaultdict(list)
    for r in judged:
        by_theme[r["theme"]].append(r)
    for theme, rows in sorted(by_theme.items(), key=lambda kv: -len(kv[1])):
        k = sum(1 for r in rows if r["kept"])
        print(f"  {str(theme)[:46]:<48} kept {k}/{len(rows)}")
    print(f"\nwrote {out_path}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="only the first N calls")
    parser.add_argument("--out", default="verification_replay.json")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument(
        "--nonce",
        default=None,
        help="busts the gateway response cache; REQUIRED for a repeat run to "
             "measure anything (see module docstring)",
    )
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument(
        "--framing",
        choices=("production", "neutral"),
        default="production",
        help="'production' asks whether the quote supports the claim (the "
             "shipped verifier). 'neutral' asks whether the call exhibits the "
             "theme at all, withholding the claim and the pre-picked quote — "
             "see app/services/eval/neutral_framing.py",
    )
    args = parser.parse_args()
    asyncio.run(
        _run(args.limit, args.out, args.batch_size, args.nonce, args.concurrency, args.framing)
    )


if __name__ == "__main__":
    main()
