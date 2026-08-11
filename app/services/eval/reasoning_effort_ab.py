"""A/B harness: `reasoning_effort: high` vs `medium`, same calls, one variable.

Answers the open decision in CLAUDE.md — `high` was chosen because it was asked
for, never because it measured better. It costs ~82s/call, and if `medium`
produces materially the same output the whole backfill and every future nightly
run gets cheaper.

**This never writes to `analysis`.** The 46 rows already there ARE the "high"
side of the experiment and must survive it, so the "medium" side is run
out-of-band and written to a JSON file. Re-running the batch pipeline instead
would have been wrong twice over: `persist_analysis_result` upserts in place, so
it would overwrite the baseline, and the rows are `processed`, so they would
first have to be forced back to `pending` — mutating production state to run an
experiment.

Both sides use the identical prompts, the identical transcripts, and the
identical four steps in the identical order. The only difference is
`reasoning_effort`.

Usage:
    uv run python -m app.services.eval.reasoning_effort_ab --effort medium
    uv run python -m app.services.eval.reasoning_effort_ab --effort medium --limit 5
    uv run python -m app.services.eval.reasoning_effort_ab --compare <results.json>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.analyser_config import load_analyser_config
from app.core.config import settings
from app.db.models import Analysis, CallStorage
from app.db.session import SessionLocal
from app.domain.call_type import classify_call_type
from app.domain.card_type import classify_card_type
from app.domain.gap_analysis import analyse_gaps
from app.domain.scoring import score_call
from app.domain.types import CallType, CardTypeContext
from app.llm.client import OpenAICompatibleLLMClient
from app.llm.gateway_config import load_llm_gateway_config
from app.prompts.registry import PromptRegistry
from app.services.batch.repository import render_transcript_text
from app.services.batch.run import _PROMPTS_ROOT, build_step_prompts

logger = logging.getLogger("reasoning_effort_ab")

STEPS = ("call_type", "scoring", "gap_analysis", "card_type")


@dataclass
class Baseline:
    """One call's `high` result, read from the `analysis` row it already has."""

    recording_id: str
    call_type: str | None
    call_score: str | None
    card_type: str | None
    gaps: list[dict] = field(default_factory=list)

    @property
    def gap_count(self) -> int:
        return len(self.gaps)

    @property
    def anchored_count(self) -> int:
        return sum(1 for g in self.gaps if g.get("evidence_type") == "dialogue")


def load_baseline(limit: int | None = None) -> tuple[list[Baseline], dict[str, dict]]:
    """The `high` side plus the transcripts to re-run, in one read.

    Ordered by recording id so a `--limit` run is a stable subset rather than
    whatever the planner returns that day.
    """
    with SessionLocal() as session:
        rows = session.execute(
            select(Analysis, CallStorage)
            .join(CallStorage, Analysis.avoma_recording_id == CallStorage.avoma_recording_id)
            .where(Analysis.status == "processed")
            .order_by(Analysis.avoma_recording_id)
        ).all()

    if limit is not None:
        rows = rows[:limit]

    baselines = [
        Baseline(
            recording_id=analysis.avoma_recording_id,
            call_type=analysis.call_type,
            call_score=analysis.call_score,
            card_type=analysis.card_type,
            gaps=list(analysis.risk_gap_analysis or []),
        )
        for analysis, _ in rows
    ]
    # call_metadata travels with the transcript because the card_type step's
    # prompt input includes it in production (orchestrator.py builds
    # CardTypeContext with `call_metadata or None`). Omitting it here made the
    # card_type comparison meaningless — a different prompt was being sent than
    # the one that produced the baseline, which showed up as card_type being the
    # only field to "disagree" in a high-vs-high control.
    inputs = {
        analysis.avoma_recording_id: {
            "transcript": storage.transcript,
            "call_metadata": storage.call_metadata or {},
        }
        for analysis, storage in rows
    }
    return baselines, inputs


def _reasoning_tokens(usage) -> int:
    if usage is None:
        return 0
    details = getattr(usage, "completion_tokens_details", None)
    return getattr(details, "reasoning_tokens", 0) or 0


class UsageRecordingClient:
    """Delegating wrapper that records each step's token usage.

    The domain's `ClassificationResult` carries value/prompt hash/raw response
    and deliberately not usage — token accounting is not something the analyser
    steps have any business knowing about. Rather than widen that contract for an
    experiment, the instrumentation lives here: the eval wraps the client, reads
    `StructuredLLMResponse.usage` on the way past, and the domain is untouched.

    One instance per call under analysis, so `by_step` is that call's own tally.
    """

    def __init__(self, inner):
        self._inner = inner
        self.by_step: dict[str, int] = {}

    async def complete_structured(self, **kwargs):
        response = await self._inner.complete_structured(**kwargs)
        step = kwargs.get("response_key") or "unknown"
        self.by_step[step] = _reasoning_tokens(response.usage)
        return response


async def analyse_one(
    *,
    llm_client,
    prompts,
    recording_id: str,
    transcript: dict,
    call_metadata: dict,
    effort: str,
    nonce: str | None = None,
) -> dict:
    """The same four steps in the same order, on the same inputs, as
    `advance_analysis`.

    Deliberately not reusing `advance_analysis` itself: that function's job is to
    produce a persistable record with per-step status/retry bookkeeping, and here
    a failure should just be reported, not retried or dead-lettered. What has to
    match is the *sequence* and the *prompt inputs* — and every divergence in the
    latter silently invalidates the comparison, which is why `call_metadata` is
    threaded through to the card_type step exactly as production does it.

    `nonce`, when given, is appended to the transcript text to defeat the
    gateway's response cache. Measured: re-running an identical request returns
    in ~3s instead of ~55s, replaying the stored answer *and* its token counts.
    That makes a same-effort control look perfectly deterministic when it has
    actually computed nothing. Both sides of a comparison must use the same nonce
    policy or the comparison is between a fresh run and a replay.
    """
    transcript_text = render_transcript_text(transcript)
    if nonce:
        transcript_text = f"{transcript_text}\n\n[eval run {nonce}]"
    recording_client = UsageRecordingClient(llm_client)
    started = time.monotonic()
    result: dict = {
        "recording_id": recording_id,
        "effort": effort,
        "call_type": None,
        "call_score": None,
        "card_type": None,
        "gaps": [],
        "errors": {},
        "reasoning_tokens": {},
    }

    try:
        call_type_result = await classify_call_type(
            llm_client=recording_client, transcript=transcript_text, prompt=prompts.call_type
        )
        call_type_value = call_type_result.value
        result["call_type"] = call_type_value.value
    except Exception as exc:
        result["errors"]["call_type"] = f"{type(exc).__name__}: {exc}"
        call_type_value = None

    if call_type_value is not None:
        try:
            scoring_result = await score_call(
                llm_client=recording_client,
                transcript=transcript_text,
                prompt=prompts.scoring_for(call_type_value),
            )
            result["call_score"] = scoring_result.value.value
        except Exception as exc:
            result["errors"]["scoring"] = f"{type(exc).__name__}: {exc}"

        try:
            gap_result = await analyse_gaps(
                llm_client=recording_client,
                transcript=transcript_text,
                prompt=prompts.gap_rubric_for(call_type_value),
            )
            result["gaps"] = [
                {
                    "theme": g.theme,
                    "evidence_type": g.evidence_type,
                    "evidence": g.evidence,
                    "timestamp": g.timestamp,
                    "confidence": g.confidence,
                }
                for g in gap_result.value
            ]
        except Exception as exc:
            result["errors"]["gap_analysis"] = f"{type(exc).__name__}: {exc}"

    try:
        card_type_result = await classify_card_type(
            llm_client=recording_client,
            context=CardTypeContext(
                transcript=transcript_text,
                # `or None` matches orchestrator.py exactly: an empty dict must
                # not render an empty "Call metadata:" section the baseline
                # never had.
                call_metadata=call_metadata or None,
                existing_score=result["call_score"],
            ),
            prompt=prompts.card_type,
        )
        result["card_type"] = card_type_result.value.value
    except Exception as exc:
        result["errors"]["card_type"] = f"{type(exc).__name__}: {exc}"

    result["reasoning_tokens"] = dict(recording_client.by_step)
    result["seconds"] = round(time.monotonic() - started, 1)
    return result


async def run_side(
    *,
    effort: str,
    limit: int | None,
    out_path: Path,
    concurrency: int | None = None,
    nonce: str | None = None,
) -> list[dict]:
    analyser_config = load_analyser_config()
    gateway_config = load_llm_gateway_config()
    registry = PromptRegistry(root=_PROMPTS_ROOT)
    prompts = build_step_prompts(registry, analyser_config.gap_rubric_mode)

    baselines, inputs = load_baseline(limit)
    concurrency = concurrency or analyser_config.max_concurrent_calls

    logger.info(
        "A/B side '%s': %d call(s), model=%s, concurrency=%d, gap_rubric_mode=%s",
        effort,
        len(baselines),
        gateway_config.default_model,
        concurrency,
        analyser_config.gap_rubric_mode,
    )
    logger.info("baseline 'high' side stays in `analysis` untouched; this side -> %s", out_path)
    logger.info(
        "cache-busting nonce: %s",
        nonce or "NONE - identical repeat requests will be served from the gateway cache",
    )

    llm_client = OpenAICompatibleLLMClient(
        base_url=settings.llm_gateway_base_url,
        api_key=settings.llm_gateway_api_key,
        model=gateway_config.default_model,
        timeout_seconds=gateway_config.timeout_seconds,
        max_retries=gateway_config.max_retries,
        environment=gateway_config.environment,
        trace_name=gateway_config.trace_name,
        temperature=gateway_config.temperature,
        # The one variable under test.
        reasoning_effort=effort,
    )

    semaphore = asyncio.Semaphore(concurrency)
    done = 0
    total = len(baselines)
    started = time.monotonic()

    async def worker(baseline: Baseline) -> dict:
        nonlocal done
        async with semaphore:
            row_input = inputs[baseline.recording_id]
            result = await analyse_one(
                llm_client=llm_client,
                prompts=prompts,
                recording_id=baseline.recording_id,
                transcript=row_input["transcript"],
                call_metadata=row_input["call_metadata"],
                effort=effort,
                nonce=nonce,
            )
        done += 1
        logger.info(
            "[%d/%d] %s  %ss  call_type=%s score=%s card=%s gaps=%d%s",
            done,
            total,
            baseline.recording_id[:20],
            result["seconds"],
            result["call_type"],
            result["call_score"],
            result["card_type"],
            len(result["gaps"]),
            f"  ERRORS={list(result['errors'])}" if result["errors"] else "",
        )
        return result

    try:
        results = await asyncio.gather(*(worker(b) for b in baselines))
    finally:
        await llm_client.aclose()

    elapsed = time.monotonic() - started
    payload = {
        "effort": effort,
        "model": gateway_config.default_model,
        "gap_rubric_mode": analyser_config.gap_rubric_mode,
        "concurrency": concurrency,
        "nonce": nonce,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "wall_clock_seconds": round(elapsed, 1),
        "results": list(results),
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(
        "side '%s' done: %d call(s) in %.1fs wall-clock (%.1fs/call serial-equivalent) -> %s",
        effort,
        total,
        elapsed,
        sum(r["seconds"] for r in results) / max(total, 1),
        out_path,
    )
    return list(results)


def _pct(numerator: int, denominator: int) -> str:
    return f"{(100 * numerator / denominator):.0f}%" if denominator else "n/a"


def compare(results: list[dict], baselines: list[Baseline], effort: str) -> None:
    """Per-call diff, then the same diff stratified by call type.

    Stratification is not optional decoration: aggregate anchoring rate is
    dominated by which call types are in the sample, because absence-shaped
    rubric themes cannot be quoted. An aggregate number would hide the variable
    under test behind rubric mix — see CLAUDE.md.
    """
    by_id = {b.recording_id: b for b in baselines}
    rows = [(by_id[r["recording_id"]], r) for r in results if r["recording_id"] in by_id]

    logger.info("")
    logger.info("=" * 78)
    logger.info("high (in `analysis`)  vs  %s (this run)   --  %d call(s)", effort, len(rows))
    logger.info("=" * 78)

    agree = {"call_type": 0, "call_score": 0, "card_type": 0}
    gap_same = 0
    high_gaps = high_anchored = new_gaps = new_anchored = 0
    disagreements = []

    per_type_agree: dict = defaultdict(lambda: defaultdict(int))
    per_type_total: dict = defaultdict(int)
    per_type_anchor: dict = defaultdict(lambda: {"high": [0, 0], effort: [0, 0]})

    for base, res in rows:
        bucket = base.call_type or "unknown"
        per_type_total[bucket] += 1

        for field_name, base_value in (
            ("call_type", base.call_type),
            ("call_score", base.call_score),
            ("card_type", base.card_type),
        ):
            if base_value == res[field_name]:
                agree[field_name] += 1
                per_type_agree[bucket][field_name] += 1
            else:
                disagreements.append(
                    (base.recording_id, bucket, field_name, base_value, res[field_name])
                )

        res_anchored = sum(1 for g in res["gaps"] if g["evidence_type"] == "dialogue")
        high_gaps += base.gap_count
        high_anchored += base.anchored_count
        new_gaps += len(res["gaps"])
        new_anchored += res_anchored
        if base.gap_count == len(res["gaps"]):
            gap_same += 1

        per_type_anchor[bucket]["high"][0] += base.anchored_count
        per_type_anchor[bucket]["high"][1] += base.gap_count
        per_type_anchor[bucket][effort][0] += res_anchored
        per_type_anchor[bucket][effort][1] += len(res["gaps"])

    total = len(rows)
    logger.info("")
    logger.info("AGREEMENT WITH THE high BASELINE")
    for field_name in ("call_type", "call_score", "card_type"):
        logger.info(
            "  %-11s %3d/%-3d  %s", field_name, agree[field_name], total, _pct(agree[field_name], total)
        )
    logger.info("  gap count   %3d/%-3d  %s (identical number of gaps)", gap_same, total, _pct(gap_same, total))

    logger.info("")
    logger.info("GAP VOLUME AND ANCHORING")
    logger.info("  high    %3d gaps, %3d anchored (%s)", high_gaps, high_anchored, _pct(high_anchored, high_gaps))
    logger.info("  %-7s %3d gaps, %3d anchored (%s)", effort, new_gaps, new_anchored, _pct(new_anchored, new_gaps))

    logger.info("")
    logger.info("STRATIFIED BY CALL TYPE (baseline's call_type)")
    logger.info(
        "  %-24s %5s  %8s %8s %8s   %-13s %-13s",
        "call_type", "n", "type", "score", "card", "anchor high", f"anchor {effort}",
    )
    for bucket in sorted(per_type_total):
        n = per_type_total[bucket]
        hi_a, hi_g = per_type_anchor[bucket]["high"]
        nw_a, nw_g = per_type_anchor[bucket][effort]
        logger.info(
            "  %-24s %5d  %8s %8s %8s   %-13s %-13s",
            bucket,
            n,
            _pct(per_type_agree[bucket]["call_type"], n),
            _pct(per_type_agree[bucket]["call_score"], n),
            _pct(per_type_agree[bucket]["card_type"], n),
            f"{hi_a}/{hi_g} ({_pct(hi_a, hi_g)})",
            f"{nw_a}/{nw_g} ({_pct(nw_a, nw_g)})",
        )

    if disagreements:
        logger.info("")
        logger.info("EVERY DISAGREEMENT (%d)", len(disagreements))
        for recording_id, bucket, field_name, was, now in disagreements:
            logger.info("  %s  [%s]  %s: high=%r -> %s=%r", recording_id[:20], bucket, field_name, was, effort, now)

    tokens = [sum(r["reasoning_tokens"].values()) for r in results if r["reasoning_tokens"]]
    seconds = [r["seconds"] for r in results]
    if tokens:
        logger.info("")
        logger.info("COST / LATENCY FOR THIS SIDE (no baseline equivalent -- never recorded for the high run)")
        logger.info("  reasoning tokens per call: mean %.0f, min %d, max %d", sum(tokens) / len(tokens), min(tokens), max(tokens))
    if seconds:
        logger.info("  seconds per call:          mean %.1f, min %.1f, max %.1f", sum(seconds) / len(seconds), min(seconds), max(seconds))

    errored = [r for r in results if r["errors"]]
    logger.info("")
    if errored:
        logger.info("!! %d call(s) had step errors -- treat their rows above as incomplete:", len(errored))
        for r in errored:
            logger.info("   %s  %s", r["recording_id"][:20], r["errors"])
    else:
        logger.info("no step errors on this side.")
    logger.info("=" * 78)


def compare_runs(left: dict, right: dict) -> None:
    """Diff two *run files* against each other rather than against `analysis`.

    This is the comparison that actually answers the open decision. Diffing a
    run against the stored baseline conflates two things: the effort change, and
    whatever the model does differently on a fresh pass. Running both efforts
    with the same cache-busting nonce and diffing them directly isolates the
    effort — and diffing a same-effort fresh run against the baseline measures
    the non-determinism floor that any effort comparison has to clear.
    """
    l_label = f"{left['effort']}"
    r_label = f"{right['effort']}"
    l_by_id = {r["recording_id"]: r for r in left["results"]}
    r_by_id = {r["recording_id"]: r for r in right["results"]}
    shared = sorted(set(l_by_id) & set(r_by_id))

    logger.info("")
    logger.info("=" * 78)
    logger.info("RUN-TO-RUN: %s vs %s  --  %d shared call(s)", l_label, r_label, len(shared))
    logger.info("  nonce: left=%r right=%r", left.get("nonce"), right.get("nonce"))
    if left.get("nonce") != right.get("nonce"):
        logger.info("  !! DIFFERENT NONCES: one side may be a cache replay. Not comparable.")
    logger.info("=" * 78)

    agree = defaultdict(int)
    gap_same = 0
    disagreements = []
    per_type_agree: dict = defaultdict(lambda: defaultdict(int))
    per_type_total: dict = defaultdict(int)

    for recording_id in shared:
        a, b = l_by_id[recording_id], r_by_id[recording_id]
        bucket = a["call_type"] or "unknown"
        per_type_total[bucket] += 1
        for field_name in ("call_type", "call_score", "card_type"):
            if a[field_name] == b[field_name]:
                agree[field_name] += 1
                per_type_agree[bucket][field_name] += 1
            else:
                disagreements.append(
                    (recording_id, bucket, field_name, a[field_name], b[field_name])
                )
        if len(a["gaps"]) == len(b["gaps"]):
            gap_same += 1

    total = len(shared)
    logger.info("")
    logger.info("AGREEMENT BETWEEN THE TWO RUNS")
    for field_name in ("call_type", "call_score", "card_type"):
        logger.info("  %-11s %3d/%-3d  %s", field_name, agree[field_name], total, _pct(agree[field_name], total))
    logger.info("  gap count   %3d/%-3d  %s", gap_same, total, _pct(gap_same, total))

    for label, payload in ((l_label, left), (r_label, right)):
        results = payload["results"]
        gaps = sum(len(r["gaps"]) for r in results)
        anchored = sum(1 for r in results for g in r["gaps"] if g["evidence_type"] == "dialogue")
        tokens = [sum(r["reasoning_tokens"].values()) for r in results if r["reasoning_tokens"]]
        seconds = [r["seconds"] for r in results]
        logger.info("")
        logger.info(
            "  %-7s %3d gaps, %3d anchored (%s) | %.1fs/call | %.0f reasoning tokens/call | %.0fs wall-clock",
            label,
            gaps,
            anchored,
            _pct(anchored, gaps),
            sum(seconds) / len(seconds),
            sum(tokens) / len(tokens) if tokens else 0,
            payload["wall_clock_seconds"],
        )

    logger.info("")
    logger.info("STRATIFIED BY CALL TYPE (%s's call_type)", l_label)
    logger.info("  %-24s %5s  %8s %8s %8s", "call_type", "n", "type", "score", "card")
    for bucket in sorted(per_type_total):
        n = per_type_total[bucket]
        logger.info(
            "  %-24s %5d  %8s %8s %8s",
            bucket,
            n,
            _pct(per_type_agree[bucket]["call_type"], n),
            _pct(per_type_agree[bucket]["call_score"], n),
            _pct(per_type_agree[bucket]["card_type"], n),
        )

    if disagreements:
        logger.info("")
        logger.info("EVERY DISAGREEMENT (%d)", len(disagreements))
        for recording_id, bucket, field_name, lv, rv in disagreements:
            logger.info(
                "  %s  [%s]  %s: %s=%r -> %s=%r",
                recording_id[:20], bucket, field_name, l_label, lv, r_label, rv,
            )
    logger.info("=" * 78)


def _configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)

    logger.setLevel(logging.INFO)
    logger.handlers = [stream, file_handler]
    logger.propagate = False

    # The gateway client's own retries are worth seeing in the log when a call
    # is slow, but httpx's per-request chatter would bury the per-call lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--effort", default="medium", choices=["none", "minimal", "low", "medium", "high"])
    parser.add_argument("--limit", type=int, default=None, help="only the first N calls, ordered by recording id")
    parser.add_argument("--out-dir", type=Path, default=Path("docs/eval"))
    parser.add_argument(
        "--nonce",
        default=None,
        help=(
            "string appended to each transcript to force fresh computation past the "
            "gateway response cache; use the SAME value on both sides of a comparison"
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="override analyser.max_concurrent_calls for this run (for tuning it)",
    )
    parser.add_argument("--compare", type=Path, default=None, help="skip the run; re-print the diff from a results file")
    parser.add_argument(
        "--compare-runs",
        type=Path,
        nargs=2,
        default=None,
        metavar=("LEFT", "RIGHT"),
        help="skip the run; diff two results files against each other",
    )
    args = parser.parse_args(argv)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.compare_runs:
        left_path, right_path = args.compare_runs
        _configure_logging(out_dir / f"reasoning-effort-runs-{stamp}.log")
        compare_runs(
            json.loads(left_path.read_text(encoding="utf-8")),
            json.loads(right_path.read_text(encoding="utf-8")),
        )
        return

    if args.compare:
        payload = json.loads(args.compare.read_text(encoding="utf-8"))
        _configure_logging(out_dir / f"reasoning-effort-compare-{stamp}.log")
        baselines, _ = load_baseline(None)
        compare(payload["results"], baselines, payload["effort"])
        return

    results_path = out_dir / f"reasoning-effort-{args.effort}-{stamp}.json"
    _configure_logging(out_dir / f"reasoning-effort-{args.effort}-{stamp}.log")

    results = asyncio.run(
        run_side(
            effort=args.effort,
            limit=args.limit,
            out_path=results_path,
            concurrency=args.concurrency,
            nonce=args.nonce,
        )
    )
    baselines, _ = load_baseline(args.limit)
    compare(results, baselines, args.effort)


if __name__ == "__main__":
    main()
