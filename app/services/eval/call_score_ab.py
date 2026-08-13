"""Compares call-score prompt versions on the same real calls, and measures how
reproducible each one is.

Built for Phase A of docs/superpowers/specs/2026-08-13-call-score-redesign-design.md.
There is no human score label anywhere — not in our schema and not in Koushik's
— so nothing here measures accuracy. What it measures is self-consistency and
calibration, both of which need no ground truth:

  * **tier agreement** between two identical re-runs of the same arm, and
  * **tier reachability** — whether a call type can produce all three tiers at
    all, or whether its prompt has effectively pre-decided the answer.

**Both repeats of both arms run here, now, over the same transcripts.** Comparing
a fresh run against the stored `analysis` values would attribute run-to-run churn
to the prompt change — the mistake that produced a confident, wrong "70%
agreement" in the reasoning_effort A/B. Each (call, arm, repeat) gets its own
nonce because the gateway serves a response cache keyed on the request messages;
without one the second repeat replays the first and scores a meaningless 100%.

Each call is scored with the prompt for its **stored** call type, so prompt
selection is held constant and the prompt version is the only thing that varies.

Never writes to `analysis`.

    uv run python -m app.services.eval.call_score_ab --nonce a1 --out a1.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from openai import APIStatusError
from sqlalchemy import select

from app.core.config import settings
from app.db.models import Analysis, CallStorage
from app.db.session import SessionLocal
from app.domain.scoring import score_call, score_call_by_category
from app.domain.transcript import Transcript, TranscriptSpeaker, TranscriptTurn
from app.llm.factory import build_llm_client
from app.llm.gateway_config import load_llm_gateway_config
from app.prompts.registry import PromptRegistry
from app.services.batch.run import _PROMPTS_ROOT

logger = logging.getLogger(__name__)

# call_type as stored in `analysis` -> scoring prompt directory name
CALL_TYPE_DIRS = {
    "Discovery": "discovery",
    "Demo": "demo",
    "Follow-up Demo": "follow_up_demo",
    "Kick-off": "kickoff",
    "Pricing/Negotiation": "pricing_negotiation",
    "Technical Integration": "technical_integration",
}

TIERS = ("High", "Medium", "Low")

# v1 returns the tier and nothing else; v2 onward returns the ten subscores and
# the tier is arithmetic over them. Anything not listed here is treated as v2,
# since v1's contract is the one being replaced.
TIER_ONLY_VERSIONS = {"v1"}


def _load_calls(session, limit: int | None):
    stmt = (
        select(
            Analysis.id,
            Analysis.call_type,
            Analysis.call_score,
            CallStorage.transcript,
            CallStorage.call_metadata,
        )
        .join(CallStorage, CallStorage.avoma_recording_id == Analysis.avoma_recording_id)
        .where(Analysis.call_type.is_not(None))
        .where(CallStorage.excluded_reason.is_(None))
        .order_by(Analysis.id)
    )
    calls = []
    for row in session.execute(stmt).all():
        analysis_id, call_type, stored_score, transcript, metadata = row
        if call_type not in CALL_TYPE_DIRS:
            continue
        try:
            parsed = Transcript.model_validate(transcript)
        except Exception as exc:  # pre-timestamp rows cannot be rendered
            logger.warning("call %s has an unusable transcript: %s", analysis_id, exc)
            continue
        calls.append(
            {
                "id": analysis_id,
                "call_type": call_type,
                "stored_score": stored_score,
                "title": (metadata or {}).get("title"),
                "transcript": parsed,
            }
        )
    return calls[:limit] if limit else calls


def _with_nonce(transcript: Transcript, nonce: str) -> Transcript:
    """Appends a one-line turn so the gateway's response cache misses.

    Marked is_rep so that if this transcript ever reaches the input gate, the
    nonce cannot be mistaken for client speech. Same approach as
    rubric_version_ab.py.
    """
    last = transcript.turns[-1].start_s if transcript.turns else 0.0
    eval_id = max((s.id for s in transcript.speakers), default=-1) + 1
    return Transcript(
        speakers=[
            *transcript.speakers,
            TranscriptSpeaker(id=eval_id, name="eval", email=None, is_rep=True),
        ],
        turns=[
            *transcript.turns,
            TranscriptTurn(speaker="eval", speaker_id=eval_id, text=f"[ab {nonce}]", start_s=last),
        ],
    )


async def _retry_on_throttle(coro_factory, *, attempts=4, base_delay=20.0):
    """Retries a 429 with linear backoff.

    Deliberately here and not in app/llm/client.py, which does not retry
    APIStatusError on purpose — in a batch, throttling should surface as a
    visible per-step failure. An eval sweep is different: a 429 partway through
    silently shrinks the sample, and a comparison run that loses a third of its
    calls cannot answer the question it was started for. That is not
    hypothetical — the first Phase B run lost 80 of 208 requests to Vertex
    RESOURCE_EXHAUSTED and had to be discarded.
    """
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except APIStatusError as exc:
            if exc.status_code != 429 or attempt == attempts - 1:
                raise
            delay = base_delay * (attempt + 1)
            logger.warning("429 from the gateway, retrying in %.0fs", delay)
            await asyncio.sleep(delay)


async def _score_once(llm_client, call, prompt, version, nonce):
    text = _with_nonce(call["transcript"], nonce).render_for_prompt()
    started = time.perf_counter()
    if version in TIER_ONLY_VERSIONS:
        result = await _retry_on_throttle(
            lambda: score_call(llm_client=llm_client, transcript=text, prompt=prompt)
        )
        return {
            "tier": result.value.value,
            "mean": None,
            "categories": None,
            "duration_s": round(time.perf_counter() - started, 1),
            "error": None,
        }

    result = await _retry_on_throttle(
        lambda: score_call_by_category(llm_client=llm_client, transcript=text, prompt=prompt)
    )
    breakdown = result.value
    return {
        "tier": breakdown.tier.value,
        "mean": round(breakdown.mean, 3),
        "categories": [
            {"name": c.name, "score": c.score, "evidence": c.evidence}
            for c in breakdown.categories
        ],
        # Wall-clock under whatever concurrency the run used, so it compares
        # arms within one run and says nothing about absolute latency. v2's
        # response is roughly ten times longer than v1's, which is the cost
        # A-5 exists to bound.
        "duration_s": round(time.perf_counter() - started, 1),
        "error": None,
    }


async def _run_call(llm_client, call, prompts_by_version, versions, repeats, nonce):
    out = {k: call[k] for k in ("id", "call_type", "stored_score", "title")}
    out["arms"] = {}
    for version in versions:
        prompt = prompts_by_version[version][CALL_TYPE_DIRS[call["call_type"]]]
        runs = []
        for repeat in range(repeats):
            try:
                runs.append(
                    await _score_once(
                        llm_client, call, prompt, version, f"{nonce}-{version}-r{repeat}"
                    )
                )
            except Exception as exc:
                logger.warning("call %s %s r%s failed: %s", call["id"], version, repeat, exc)
                runs.append(
                    {
                        "tier": None,
                        "mean": None,
                        "categories": None,
                        "duration_s": None,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        out["arms"][version] = runs
    return out


async def _run(versions, repeats, nonce, out_path, concurrency, limit):
    registry = PromptRegistry(root=_PROMPTS_ROOT)
    prompts_by_version = {
        version: {
            directory: registry.get(kind="scoring", call_type=directory, label=version)
            for directory in CALL_TYPE_DIRS.values()
        }
        for version in versions
    }

    gateway_config = load_llm_gateway_config()
    llm_client = build_llm_client(settings=settings, gateway_config=gateway_config)

    session = SessionLocal()
    try:
        calls = _load_calls(session, limit)
    finally:
        session.close()

    print(
        f"{len(calls)} calls x {len(versions)} versions x {repeats} repeats "
        f"= {len(calls) * len(versions) * repeats} scoring requests",
        file=sys.stderr,
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(call):
        async with semaphore:
            return await _run_call(llm_client, call, prompts_by_version, versions, repeats, nonce)

    try:
        results = await asyncio.gather(*(guarded(c) for c in calls))
    finally:
        await llm_client.aclose()

    Path(out_path).write_text(json.dumps(results, indent=1), encoding="utf-8")
    report(results, versions)
    print(f"\nwrote {out_path}")


def _tiers(result, version):
    return [run["tier"] for run in result["arms"][version]]


def report(results, versions):
    print("\n" + "=" * 78)
    print("TIER DISTRIBUTION  (first repeat of each arm)")
    print("=" * 78)
    header = "".join(f"{v:>26}" for v in versions)
    print(f"{'call type':<24}{header}")
    by_type = defaultdict(list)
    for r in results:
        by_type[r["call_type"]].append(r)
    for call_type in sorted(by_type):
        cells = []
        for version in versions:
            counts = Counter(
                _tiers(r, version)[0] for r in by_type[call_type] if _tiers(r, version)[0]
            )
            cells.append("  ".join(f"{t[0]}{counts.get(t, 0):<2}" for t in TIERS))
        print(f"  {call_type:<22}" + "".join(f"{c:>26}" for c in cells))
    cells = []
    for version in versions:
        counts = Counter(_tiers(r, version)[0] for r in results if _tiers(r, version)[0])
        cells.append("  ".join(f"{t[0]}{counts.get(t, 0):<2}" for t in TIERS))
    print(f"  {'ALL':<22}" + "".join(f"{c:>26}" for c in cells))
    print("\n  H/M/L counts.  Key question: can every call type reach every tier?")

    print("\n" + "=" * 78)
    print("A-3  TIER REACHABILITY  (call types that never produce a given tier)")
    print("=" * 78)
    for version in versions:
        blocked = []
        for call_type in sorted(by_type):
            seen = {t for r in by_type[call_type] for t in _tiers(r, version) if t}
            missing = [t for t in TIERS if t not in seen]
            if missing and len(by_type[call_type]) >= 4:
                blocked.append(f"{call_type} (no {'/'.join(missing)}, n={len(by_type[call_type])})")
        print(f"  {version}: {len(blocked)} of {len(by_type)} call types")
        for entry in blocked:
            print(f"      {entry}")

    print("\n" + "=" * 78)
    print("A-1  REPRODUCIBILITY  (same prompt, same settings, two fresh runs)")
    print("=" * 78)
    for version in versions:
        pairs = [
            _tiers(r, version) for r in results if all(t for t in _tiers(r, version))
        ]
        if not pairs or len(pairs[0]) < 2:
            print(f"  {version}: needs --repeats 2")
            continue
        agree = sum(1 for tiers in pairs if len(set(tiers)) == 1)
        print(f"  {version}: {agree}/{len(pairs)} calls gave the same tier twice "
              f"({agree / len(pairs):.0%})")
        flips = Counter(
            tuple(sorted(set(tiers))) for tiers in pairs if len(set(tiers)) > 1
        )
        for flip, n in flips.most_common():
            print(f"      {' <-> '.join(flip):<24}{n}")

    print("\n" + "=" * 78)
    print("A-2  PRICING/NEGOTIATION LOW RATE  (baseline 8/9 = 89%)")
    print("=" * 78)
    pricing = by_type.get("Pricing/Negotiation", [])
    for version in versions:
        tiers = [t for r in pricing for t in _tiers(r, version) if t]
        if tiers:
            low = sum(1 for t in tiers if t == "Low")
            print(f"  {version}: {low}/{len(tiers)} runs Low ({low / len(tiers):.0%})")

    print("\n" + "=" * 78)
    print("A-5  SCORING-STEP LATENCY  (wall-clock under this run's concurrency)")
    print("=" * 78)
    for version in versions:
        times = [
            run["duration_s"]
            for r in results
            for run in r["arms"][version]
            if run.get("duration_s")
        ]
        if times:
            times.sort()
            print(f"  {version}: median {times[len(times) // 2]:.1f}s   "
                  f"p90 {times[int(0.9 * (len(times) - 1))]:.1f}s   max {times[-1]:.1f}s")
        else:
            print(f"  {version}: not recorded (run predates the timing field)")

    _category_report(results, versions)
    _errors(results, versions)


def _category_report(results, versions):
    """The Phase A diagnostic Phase B is designed from: which categories carry
    real signal, which are always N/A, and which flip between identical runs."""
    for version in versions:
        has_categories = any(
            run.get("categories") for r in results for run in r["arms"][version]
        )
        if not has_categories:
            continue

        print("\n" + "=" * 78)
        print(f"A-4  PER-CATEGORY BREAKDOWN  ({version})")
        print("=" * 78)

        by_type = defaultdict(lambda: defaultdict(list))
        for r in results:
            for run in r["arms"][version]:
                for cat in run.get("categories") or []:
                    by_type[r["call_type"]][cat["name"]].append(cat["score"])

        for call_type in sorted(by_type):
            print(f"\n  {call_type}")
            print(f"    {'category':<58}{'n/a':>6}{'mean':>7}{'spread':>9}")
            for name, scores in by_type[call_type].items():
                scored = [s for s in scores if s is not None]
                na_rate = 1 - len(scored) / len(scores)
                mean = f"{statistics.mean(scored):.2f}" if scored else "-"
                spread = (
                    f"{min(scored)}-{max(scored)}" if scored else "-"
                )
                print(f"    {name[:56]:<58}{na_rate:>5.0%}{mean:>7}{spread:>9}")

        print(f"\n  MEAN DISTRIBUTION ({version}) — where the band edges 2.8 / 4.2 fall")
        means = [
            run["mean"] for r in results for run in r["arms"][version] if run.get("mean")
        ]
        if means:
            means.sort()
            quantiles = [means[int(q * (len(means) - 1))] for q in (0, 0.25, 0.5, 0.75, 1)]
            print("    min/p25/median/p75/max: " + "  ".join(f"{q:.2f}" for q in quantiles))
            near = sum(1 for m in means if abs(m - 2.8) < 0.15 or abs(m - 4.2) < 0.15)
            print(f"    within 0.15 of a band edge: {near}/{len(means)} "
                  f"({near / len(means):.0%})  <- these are the coin flips")

        print(f"\n  CATEGORY VOLATILITY ({version}) — same call, two identical runs")
        deltas = defaultdict(list)
        for r in results:
            runs = r["arms"][version]
            if len(runs) < 2 or not all(run.get("categories") for run in runs[:2]):
                continue
            first = {c["name"]: c["score"] for c in runs[0]["categories"]}
            second = {c["name"]: c["score"] for c in runs[1]["categories"]}
            for name in first.keys() & second.keys():
                a, b = first[name], second[name]
                if a is None or b is None:
                    deltas[name].append(None if a is b else "na-flip")
                else:
                    deltas[name].append(abs(a - b))
        rows = []
        for name, values in deltas.items():
            numeric = [v for v in values if isinstance(v, int)]
            na_flips = sum(1 for v in values if v == "na-flip")
            same = sum(1 for v in numeric if v == 0) + sum(1 for v in values if v is None)
            rows.append((same / len(values), name, len(values), na_flips))
        for stable, name, n, na_flips in sorted(rows):
            print(f"    {name[:56]:<58}{stable:>5.0%} identical   "
                  f"{na_flips} N/A flips of {n}")


def _errors(results, versions):
    print("\n" + "=" * 78)
    print("ERRORS")
    print("=" * 78)
    total = 0
    for version in versions:
        errs = [
            (r["id"], run["error"])
            for r in results
            for run in r["arms"][version]
            if run.get("error")
        ]
        total += len(errs)
        print(f"  {version}: {len(errs)}")
        for analysis_id, err in errs[:10]:
            print(f"      id={analysis_id} {err[:100]}")
    if not total:
        print("  none")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--versions", default="v1,v2", help="scoring prompt labels, in order")
    parser.add_argument(
        "--repeats", type=int, default=2, help="runs per arm; 2 is what A-1 needs"
    )
    parser.add_argument(
        "--nonce", required=True, help="busts the gateway response cache; mandatory"
    )
    parser.add_argument("--out", default="call_score_ab.json")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(
        _run(
            [s.strip() for s in args.versions.split(",")],
            args.repeats,
            args.nonce,
            args.out,
            args.concurrency,
            args.limit,
        )
    )


if __name__ == "__main__":
    main()
