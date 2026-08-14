"""Scores the call_type prompt against labelled transcripts.

    uv run python -m eval.call_type_accuracy --dir Ground_Truth_call_type
    uv run python -m eval.call_type_accuracy --labels labels.json --nonce r1

**Read this before quoting a number from the `Ground_Truth_call_type` directory.**
Those six transcripts are the ones `app/prompts/call_type/v1.txt` was *written
from* — they are the six sections of `Call_examples.md`. Scoring the prompt on
them measures nothing about accuracy, because the type descriptions were authored
to make exactly those six come out right. It is a **floor check**: a failure
means the prompt is broken, a pass means nothing. Treating it as accuracy would
repeat `problems-and-fixes.md` 8.6, where a probe scored 0/25 on material the
model had generated for itself and was mistaken for a result.

For a real measurement, point `--labels` at a JSON file mapping
`avoma_recording_id -> expected call type` for calls that are **not** in
`Call_examples.md`, and transcripts are read from `call_storage`.

Never writes to `analysis`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.models import CallStorage
from app.db.session import SessionLocal
from app.domain.call_type import classify_call_type
from app.domain.transcript import Transcript
from app.llm.factory import build_llm_client
from app.llm.gateway_config import load_llm_gateway_config
from app.prompts.registry import PromptRegistry
from app.services.batch.run import _PROMPTS_ROOT

logger = logging.getLogger(__name__)

# Filename stem -> the exact enum value the prompt must return. Kept explicit
# rather than fuzzy-matched: a silent mismatch here would read as a wrong
# prediction and send someone rewriting a prompt that was already correct.
FILENAME_TO_TYPE = {
    "demo": "Demo",
    "discovery": "Discovery",
    "followupdemo": "Follow-up Demo",
    "kickoff": "Kick-off",
    "pricingnegotiation": "Pricing/Negotiation",
    "techinal": "Technical Integration",
    "technical": "Technical Integration",
    "technicalintegration": "Technical Integration",
}


def _stem_key(path: Path) -> str:
    return re.sub(r"[^a-z]", "", path.stem.lower())


def _load_from_dir(directory: Path) -> list[dict]:
    cases = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        key = _stem_key(path)
        expected = FILENAME_TO_TYPE.get(key)
        if expected is None:
            logger.warning("skipping %s — filename maps to no known call type", path.name)
            continue
        cases.append(
            {"name": path.name, "expected": expected, "text": path.read_text(encoding="utf-8")}
        )
    return cases


def _load_from_module() -> list[dict]:
    """The committed hand labels — the only source that can measure real accuracy.

    Strict by construction: `strict_labels()` drops the ambiguous ones, so a
    judgement call where a second reviewer could differ neither inflates nor
    deflates the score.
    """
    from eval.call_type_labels import strict_labels

    return _cases_for(strict_labels())


def _load_from_labels(labels_path: Path) -> list[dict]:
    """`{avoma_recording_id: "Discovery", ...}` — for ad-hoc label sets."""
    return _cases_for(json.loads(labels_path.read_text(encoding="utf-8")))


def _cases_for(labels: dict[str, str]) -> list[dict]:
    """Pairs each label with its transcript from call_storage."""
    session = SessionLocal()
    try:
        rows = session.execute(
            select(CallStorage).where(CallStorage.avoma_recording_id.in_(list(labels)))
        ).scalars().all()
        by_id = {r.avoma_recording_id: r for r in rows}
    finally:
        session.close()

    cases = []
    for recording_id, expected in labels.items():
        row = by_id.get(recording_id)
        if row is None:
            logger.warning("no call_storage row for %s — skipped", recording_id)
            continue
        cases.append({
            "name": (row.call_metadata or {}).get("title") or recording_id,
            "expected": expected,
            "text": Transcript.model_validate(row.transcript).render_for_prompt(),
        })
    return cases


async def _classify(llm_client, prompt, case, nonce):
    text = case["text"] if not nonce else f"{case['text']}\n\n[eval {nonce}]"
    try:
        result = await classify_call_type(llm_client=llm_client, transcript=text, prompt=prompt)
        return {**case, "predicted": result.value.value, "error": None}
    except Exception as exc:
        logger.warning("classify failed for %s: %s", case["name"], exc)
        return {**case, "predicted": None, "error": f"{type(exc).__name__}: {exc}"}


async def _run(source, labels_path, use_module, nonce, concurrency, out_path):
    if use_module:
        cases = _load_from_module()
    elif labels_path:
        cases = _load_from_labels(Path(labels_path))
    else:
        cases = _load_from_dir(Path(source))
    if not cases:
        print("no labelled cases found", file=sys.stderr)
        return

    registry = PromptRegistry(root=_PROMPTS_ROOT)
    prompt = registry.latest(kind="call_type")
    gateway_config = load_llm_gateway_config()
    llm_client = build_llm_client(settings=settings, gateway_config=gateway_config)

    print(f"classifying {len(cases)} labelled transcript(s) "
          f"with call_type {prompt.label} ({prompt.content_hash[:8]})", file=sys.stderr)

    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(case):
        async with semaphore:
            return await _classify(llm_client, prompt, case, nonce)

    try:
        results = await asyncio.gather(*(guarded(c) for c in cases))
    finally:
        await llm_client.aclose()

    if out_path:
        Path(out_path).write_text(
            json.dumps([{k: v for k, v in r.items() if k != "text"} for r in results], indent=1),
            encoding="utf-8",
        )
    _report(results)


def _report(results):
    judged = [r for r in results if r["error"] is None]
    correct = [r for r in judged if r["predicted"] == r["expected"]]
    print(f"\nclassified {len(judged)}/{len(results)}   "
          f"correct: {len(correct)}/{len(judged)}"
          + (f" ({len(correct)/len(judged):.0%})" if judged else ""))

    print("\n  expected              -> predicted")
    for r in results:
        mark = "ok " if r["predicted"] == r["expected"] else "MISS"
        print(f"  {mark} {r['expected']:<22}-> {str(r['predicted']):<22}{r['name'][:40]}")

    wrong = [r for r in judged if r["predicted"] != r["expected"]]
    if wrong:
        print("\n  confusions (expected -> predicted):")
        for (exp, pred), n in Counter((r["expected"], r["predicted"]) for r in wrong).most_common():
            print(f"    {n}x  {exp} -> {pred}")

    per_type = defaultdict(lambda: [0, 0])
    for r in judged:
        per_type[r["expected"]][1] += 1
        if r["predicted"] == r["expected"]:
            per_type[r["expected"]][0] += 1
    if len(per_type) > 1:
        print("\n  per expected type:")
        for t, (ok, n) in sorted(per_type.items()):
            print(f"    {t:<24}{ok}/{n}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        default="Ground_Truth_call_type",
        help="directory of labelled transcripts, one file per call type, the type "
             "taken from the filename. NOTE: the default directory is the prompt's "
             "own training material — see the module docstring.",
    )
    parser.add_argument(
        "--labelled",
        action="store_true",
        help="score against the committed hand labels in "
             "eval/call_type_labels.py (unambiguous ones only). "
             "**This is the only mode that measures real accuracy** — the "
             "default --dir is the prompt's own training material.",
    )
    parser.add_argument(
        "--labels",
        default=None,
        help="ad-hoc JSON {avoma_recording_id: expected_type} read against call_storage",
    )
    parser.add_argument("--nonce", default=None, help="busts the gateway response cache")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    asyncio.run(
        _run(args.dir, args.labels, args.labelled, args.nonce, args.concurrency, args.out)
    )


if __name__ == "__main__":
    main()
