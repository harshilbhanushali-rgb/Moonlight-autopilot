# `reasoning_effort`: high vs medium — resolved

**Date:** 2026-08-11 · **Model:** `gemini-3.5-flash` · **n:** 46 calls (every `processed`
row in `analysis`, all six call types) · **Harness:**
`app/services/eval/reasoning_effort_ab.py` · **Raw data:** `docs/eval/reasoning-effort-*.json`

## Verdict

**`medium` and `high` are indistinguishable on output quality.** Every difference
between them is inside the model's own run-to-run variance. `medium` costs 22%
fewer reasoning tokens for ~5% less wall-clock.

**But the premise of the open decision was wrong.** It assumed `high` cost ~82s/call
and that `medium` would roughly halve the backfill. Measured fresh, `high` is
**64.7s/call** and `medium` is **61.3s/call** — a 5% difference, not 2x. Switching
to `medium` does not meaningfully speed anything up.

**The far bigger finding is that the pipeline is not reproducible at all.** Re-running
the *identical* input at the *identical* settings reproduces all four outputs on
only **43% of calls**. That dwarfs `reasoning_effort` and is the thing worth acting on.

## The measurement that mattered: a same-effort control

The A/B as originally specified (re-run the 46 with `medium`, diff against the
stored rows) cannot answer the question, because a diff against the baseline
conflates the effort change with plain run-to-run drift. `temperature: 0` is
documented as best-effort, not a contract — so the noise floor has to be measured
before any effort comparison means anything.

Both rows below are fresh computations on the same 46 calls with the same
cache-busting nonce, so the only difference in the TEST row is `reasoning_effort`.

| comparison | `call_type` | `call_score` | `card_type` | gap count | **all four** |
|---|---|---|---|---|---|
| **CONTROL** — high vs high | 89% | 78% | 96% | 57% | **43%** |
| **TEST** — high vs medium | 83% | 83% | 96% | 57% | **46%** |

Read the two rows against each other:

- **`call_score`: `medium` agrees with `high` (83%) *more* than `high` agrees with itself (78%).** The effort setting has no detectable effect on scoring.
- **`card_type` and gap count are identical across both rows** (96% / 57%). No effect.
- **`call_type` is 6pp lower** in the TEST row — 3 calls out of 46, within sampling noise at this n. The only hint of a real effect, and it concentrates on Technical Integration (50% agreement), the type CLAUDE.md already flags as hardest to classify because its jargon overlaps Kick-off and Demo.

## Cost and latency

| | latency/call | reasoning tokens/call | gaps | anchoring | 46 calls @ concurrency 10 |
|---|---|---|---|---|---|
| `high` | 64.7s | 7,620 | 77 | 64% | 346s |
| `medium` | 61.3s | 5,935 | 70 | 60% | 313s |

`medium` saves **22% of reasoning tokens** for **5% of wall-clock**. If token spend
matters, `medium` is defensible. If latency is the goal, it buys almost nothing —
concurrency already did that work (346s for 46 calls vs ~63 min serial).

## The real problem: the pipeline is not reproducible

The CONTROL row is not a methodological footnote, it is the finding:

- **`call_score` flips on 22% of calls** (10/46) at identical settings. Churn is directionally biased toward the middle: `Low→Medium` 5, `High→Low` 2, `Medium→Low` 1, `High→Medium` 1, `Medium→High` 1.
- **`call_type` flips on 11%** (5/46).
- **Gap count differs on 43%** (20/46); total gaps 86 → 77 between two `high` runs.
- **Only 43% of calls reproduce all four outputs.**

Consequences worth raising with whoever owns the prompts:

1. A moderator's card depends on which night the call happened to be processed. Two runs of the same call can disagree on High vs Low.
2. Any future prompt-change evaluation needs this same control, or it will read noise as signal — which is exactly what the first pass of this A/B did.
3. `temperature: 0` is doing much less than its name suggests on this gateway. `analyser.gap_rubric_mode` (few-shot vs description-only) is still unvalidated and will hit the identical problem: at a 43% reproduction rate, a single-run comparison of the two modes cannot distinguish them.

Not diagnosed here: whether the instability is the gateway, the model, or genuinely
ambiguous rubric boundaries. `Low→Medium` dominating the score churn hints the
scoring rubric's Low/Medium boundary is underspecified rather than the model being
random, but that is a hypothesis, not a result.

## Incidental discovery: the gateway serves a response cache

Re-running an identical request returns in **~3s instead of ~55s**, replaying the
stored answer *and* its original token counts. This was never documented.

It invalidated the first control run outright — `high` vs `high` scored 100% on
three fields at 13.0s/call, having computed essentially nothing. The harness now
takes `--nonce` to force fresh computation, and **any comparison must use the same
nonce policy on both sides** or it is comparing a fresh run against a replay.

Usefully, it also means re-running identical analyses is near-free.

## Two harness bugs found along the way

Both were caught by the control disagreeing in a way the model could not explain.

1. **`call_metadata` was missing from the card_type step.** Production builds `CardTypeContext` with `call_metadata or None` (`orchestrator.py`), carrying call title, account name, deal stage and duration. The harness omitted it, so it was sending a different prompt than the one that produced the baseline. Every `card_type` number measured before the fix was against the wrong input.
2. **`APIConnectionError` was never retried** — a real production bug, not a harness one. The client's retry loop caught only `APITimeoutError`, so a transient connection blip failed a step with zero retries. Concurrency makes it likelier, and the circuit breaker reads connection failures as "gateway is down", so a few simultaneous blips could abandon a nightly batch. Fixed in `app/llm/client.py`; `APIStatusError` (HTTP 5xx) is still deliberately not retried.

## Recommendation

- **Keep `reasoning_effort: high`,** or switch to `medium` purely to save 22% of reasoning tokens. Output quality is not a reason to prefer either. Do not switch expecting a speedup.
- **Do not use the earlier "70% score agreement" figure** from the first pass of this A/B. It was measured against the stored baseline with no control, 3 cache-replayed calls, and the `call_metadata` bug. It was noise.
- **Escalate the reproducibility finding.** A 43% all-four reproduction rate matters more than any effort setting, and it blocks the `gap_rubric_mode` decision too.

## Reproducing

```bash
# fresh both sides, same nonce -- the only valid form of this comparison
N="fresh-$(date +%s)"
uv run python -m app.services.eval.reasoning_effort_ab --effort high   --nonce "$N"
uv run python -m app.services.eval.reasoning_effort_ab --effort medium --nonce "$N"
uv run python -m app.services.eval.reasoning_effort_ab --compare-runs <high.json> <medium.json>

# the control: fresh high vs the stored rows
uv run python -m app.services.eval.reasoning_effort_ab --compare <high.json>
```

The harness never writes to `analysis` — the 46 stored rows are the baseline and
must survive the experiment.
