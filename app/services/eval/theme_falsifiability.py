"""Tests whether each gap-rubric theme can be shown to be ABSENT from a call.

Every theme in the rubric describes only what a failing call looks like. None
states what a passing call looks like. A model handed that list has nothing to
match "fine" against, so when unsure it matches — which is why three themes
fired on 100% of the calls of their type while the nine-theme demo rubric
discriminated normally.

The test is a self-consistency check, in two steps per theme:

  1. Ask the model to write a short transcript excerpt in which the theme is
     absent — it defines its own passing case.
  2. Hand that excerpt back, blind, and ask whether the theme is present.

A model that reports the theme present on an excerpt it just built to not have
it cannot recognise the theme's absence. That theme is unfalsifiable, and no
amount of downstream verification will filter it — you cannot reject a claim
that nothing could disprove.

**Use `--adversarial`. The default mode is known not to discriminate.** Asked
for an *unambiguous* clean call it returned 0/25: for "Wrong People on the
Call" it wrote a scene where a named Lead Solutions Engineer answers two
technical questions in depth, then correctly said the theme was absent. That
measures nothing — the model wrote its own easy exam. Real misfires live at the
boundary: on one audited call the rep said "I'll have to check, I don't know"
and a colleague answered seven seconds later, and the theme fired anyway.
`--adversarial` asks for exactly that shape — a call a hurried reviewer would
misread — and is the only variant that has produced signal.

The test stays one-sided either way. FAILING is strong evidence a theme is
prone to false positives. PASSING means "not proven broken", never "healthy".

    uv run python -m app.services.eval.theme_falsifiability --adversarial --nonce adv1

Both modes write JSON for review and touch no database table. The generated
`observable_signals` are useful in their own right: they are the acceptance
criteria every theme currently lacks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path

from pydantic import BaseModel

from app.core.config import settings
from app.llm.client import LLMMessage
from app.llm.factory import build_llm_client
from app.llm.gateway_config import load_llm_gateway_config
from app.services.batch.run import _PROMPTS_ROOT

logger = logging.getLogger(__name__)

_THEME_RE = re.compile(r"^### \d+\.\s*(.+)$")


class NegativeExemplar(BaseModel):
    excerpt: str
    why_absent: str
    observable_signals: list[str]


class ThemePresence(BaseModel):
    present: bool
    reasoning: str


_WRITE_PROMPT = """You are helping audit a sales-call coaching rubric.

You will be given ONE coaching theme — a description of something that can go
wrong on a sales call.

Write a short transcript excerpt (8-14 turns, format `[mm:ss] Speaker: text`)
of a realistic B2B software sales call in which this theme is CLEARLY ABSENT —
a call where a reviewer would say "no, that problem does not apply here."

Do not write a perfect call. Write a normal, plausible call that simply does
not have this particular problem. It may have other unrelated rough edges.

Also return:
  - why_absent: one sentence on what makes the theme inapplicable here.
  - observable_signals: the concrete, checkable things a reviewer would point
    at to show the theme is absent. Each must be something visible in a
    transcript, not an inference about intent.
"""

# The easy version above produced 0/25 failures: asked for an unambiguous clean
# call, the model writes one (a named Solutions Engineer answering in depth for
# "Wrong People on the Call") and then trivially recognises it. That measures
# nothing. Real misfires happen at the boundary — on one audited call the rep
# said "I'll have to check, I don't know" and a colleague answered seven
# seconds later, and the theme fired anyway. This variant aims there.
_ADVERSARIAL_WRITE_PROMPT = """You are helping audit a sales-call coaching rubric.

You will be given ONE coaching theme — a description of something that can go
wrong on a sales call.

Write a short transcript excerpt (8-14 turns, format `[mm:ss] Speaker: text`)
of a realistic B2B software sales call that does NOT exhibit this theme, but
that a careless or hurried reviewer might WRONGLY flag as exhibiting it.

That means: include the surface features that would trigger a false match —
the words, the shape, the moment that looks like the problem — but make the
call one where the problem is genuinely absent. Typical ways to do that:

  - the concern is raised and then resolved a turn or two later
  - someone other than the main speaker handles it competently
  - what looks like an omission is actually addressed at a different point
  - the apparent problem turns out not to apply to this client's situation

The excerpt must be genuinely clean on this theme. A careful reviewer reading
the whole excerpt should conclude the problem is not present. You are testing
whether a hurried reader would be fooled, not writing a call that has the
problem.

Also return:
  - why_absent: one sentence on why the theme is genuinely absent despite the
    misleading surface.
  - observable_signals: the concrete, checkable things in the excerpt that
    show the theme is absent. Each must be visible in the transcript, not an
    inference about intent.
"""

_CHECK_PROMPT = """You are auditing one sales call against ONE coaching theme.

You will be given the theme and a transcript excerpt. Answer a single question:
does this excerpt exhibit the theme?

Answer honestly. Most calls do not exhibit most themes — "no" is a normal and
expected answer. Only answer "present: true" if you can point to something in
the excerpt that actually shows the problem the theme describes.
"""


def load_themes() -> list[dict]:
    themes = []
    for path in sorted(Path(_PROMPTS_ROOT, "gap_rubric").glob("*/v1-descriptiononly.yaml")):
        lines = path.read_text(encoding="utf-8").splitlines()
        current = None
        for line in lines:
            match = _THEME_RE.match(line)
            if match:
                current = {"rubric": path.parent.name, "theme": match.group(1).strip(), "body": []}
                themes.append(current)
            elif current is not None:
                if line.startswith("Reason directly over"):
                    current = None
                elif line.strip():
                    current["body"].append(line.strip())
    for theme in themes:
        theme["body"] = " ".join(theme["body"])
    return themes


async def _probe(llm_client, theme: dict, nonce: str | None, adversarial: bool) -> dict:
    label = f"THEME: {theme['theme']}\nDESCRIPTION: {theme['body']}"
    if nonce:
        label += f"\n(audit run {nonce})"
    try:
        written = await llm_client.complete_structured(
            messages=[
                LLMMessage(
                    role="system",
                    content=_ADVERSARIAL_WRITE_PROMPT if adversarial else _WRITE_PROMPT,
                ),
                LLMMessage(role="user", content=label),
            ],
            response_model=NegativeExemplar,
            response_key="falsifiability_write",
        )
        exemplar = written.parsed

        checked = await llm_client.complete_structured(
            messages=[
                LLMMessage(role="system", content=_CHECK_PROMPT),
                LLMMessage(
                    role="user",
                    content=f"{label}\n\nTRANSCRIPT EXCERPT:\n{exemplar.excerpt}",
                ),
            ],
            response_model=ThemePresence,
            response_key="falsifiability_check",
        )
        return {
            "rubric": theme["rubric"],
            "theme": theme["theme"],
            "body": theme["body"],
            # The theme is falsifiable if the model can recognise its own
            # negative case as negative.
            "falsifiable": not checked.parsed.present,
            "check_reasoning": checked.parsed.reasoning,
            "why_absent": exemplar.why_absent,
            "observable_signals": exemplar.observable_signals,
            "excerpt": exemplar.excerpt,
            "error": None,
        }
    except Exception as exc:
        logger.warning("probe failed for %r: %s", theme["theme"], exc)
        return {
            "rubric": theme["rubric"], "theme": theme["theme"], "body": theme["body"],
            "falsifiable": None, "error": f"{type(exc).__name__}: {exc}",
        }


async def _run(out_path: str, nonce: str | None, concurrency: int, adversarial: bool) -> None:
    themes = load_themes()
    mode = "adversarial (borderline)" if adversarial else "easy (unambiguous)"
    print(f"probing {len(themes)} themes — {mode} negative cases", file=sys.stderr)
    gateway_config = load_llm_gateway_config()
    llm_client = build_llm_client(settings=settings, gateway_config=gateway_config)
    semaphore = asyncio.Semaphore(concurrency)

    async def guarded(theme):
        async with semaphore:
            return await _probe(llm_client, theme, nonce, adversarial)

    try:
        results = await asyncio.gather(*(guarded(t) for t in themes))
    finally:
        await llm_client.aclose()

    Path(out_path).write_text(json.dumps(results, indent=1), encoding="utf-8")

    ok = [r for r in results if r["error"] is None]
    failed = [r for r in ok if not r["falsifiable"]]
    print(f"\nprobed {len(ok)}/{len(results)} themes")
    print(f"UNFALSIFIABLE (model could not see the absence in its own negative "
          f"case): {len(failed)}/{len(ok)}\n")
    for r in sorted(failed, key=lambda r: (r["rubric"], r["theme"])):
        print(f"  [{r['rubric']:<22}] {r['theme']}")
        print(f"       model's reason for still seeing it: {r['check_reasoning'][:150]}")
    print("\nfalsifiable (not proven broken):")
    for r in sorted((r for r in ok if r["falsifiable"]), key=lambda r: (r["rubric"], r["theme"])):
        signals = "; ".join(r["observable_signals"][:2])
        print(f"  [{r['rubric']:<22}] {r['theme']}")
        print(f"       pass criteria: {signals[:150]}")
    print(f"\nwrote {out_path}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="theme_falsifiability.json")
    parser.add_argument("--nonce", default=None, help="busts the gateway response cache")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument(
        "--adversarial",
        action="store_true",
        help="ask for borderline negative cases a hurried reviewer would "
             "misread, instead of unambiguous ones. The easy variant returned "
             "0/25 and measured nothing.",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.out, args.nonce, args.concurrency, args.adversarial))


if __name__ == "__main__":
    main()
