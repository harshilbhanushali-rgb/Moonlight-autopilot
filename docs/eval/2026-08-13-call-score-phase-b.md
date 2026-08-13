# Phase B — universal core plus stage extras: inconclusive, directionally positive

**Date:** 2026-08-13
**Raw data:** `docs/eval/2026-08-13-call-score-phaseB.json` (+ `.log`)
**Discarded first attempt:** `...-phaseB-DISCARDED-429.json`
**Reproduce:**

```bash
uv run python -m app.services.eval.call_score_ab --versions v2,v3 --nonce phaseB2 \
    --repeats 2 --concurrency 4 --out docs/eval/2026-08-13-call-score-phaseB.json
```

`v3` scores **six categories on every call** — Preparation & Pre-Call Research,
Agenda Setting & Call Control, Active Listening & Probing, Handling Objections &
Hard Questions, Business Value & Outcome Framing, Next Steps & Mutual Commitment
— plus **four specific to the call stage**, and instructs the model to hold the
shared six to one standard rather than to what the stage makes easy. Which four
stage categories survived was chosen from Phase A's N/A and spread data, not by
taste: *Addressing New/Expanded Buying Committee Members* (72% N/A),
*Security/Compliance/Data Governance Control* (64% N/A and scored exactly 3 on
every call it fired) and *Give-Get Discipline* (67% N/A, mean 1.33) were dropped.

## The sample is compromised — read everything below with that in mind

**33 of 188 requests failed** to Vertex `RESOURCE_EXHAUSTED` even after the
harness's 429 retry (173 retries were absorbed successfully). Only **22 of 47
calls** are complete on all four runs.

Worse than the count: **the losses are not random.** Throttling built up as the
run progressed, so the surviving subset is biased toward calls scored early.
This is not a random 47% subsample and cannot be treated as one.

The clearest evidence of how much that matters: **`v2` measures differently here
than it did in Phase A on the same prompts** — call-type spread 1.80 vs ~1.5,
and 5 unreachable tiers vs 3. Same arm, same corpus, different subset, visibly
different numbers. Any Phase B number below could move as much.

## Results

**Call-type spread — the number Phase B exists to reduce.** Median mean per
call type, all successful runs:

| call type | v2 (n) | v2 median | v3 (n) | v3 median |
|---|---|---|---|---|
| Demo | 14 | 4.28 | 14 | **4.75** |
| Follow-up Demo | 16 | 4.29 | 12 | 4.14 |
| Kick-off | 6 | 4.30 | 5 | 3.90 |
| Technical Integration | 13 | 4.20 | 10 | 3.90 |
| Discovery | 17 | 3.30 | 19 | 3.20 |
| Pricing/Negotiation | 13 | 2.50 | 16 | **3.53** |
| **spread** | | **1.80** | | **1.55** |

**Narrowed, but not solved.** The shared six lifted Pricing/Negotiation
substantially (2.50 → 3.53) and pulled Kick-off and Technical Integration down
toward the middle — all as intended. But **Demo went the wrong way**, 4.28 →
4.75, which cancels most of the gain. Whatever makes Demo calls score well is
not confined to the four stage-specific categories; the shared six reward it
too.

**Tier reachability improved.** Call types (n ≥ 6 runs) that never produce all
three tiers: **v2 5 of 6, v3 3 of 6.** v3 still cannot give Pricing a High, or
Demo and Technical Integration a Low.

**Tier mix moved toward the middle**, which is the right direction given Phase A
pushed everything up: v2 `30 High / 35 Medium / 14 Low`, v3 `22 / 41 / 13`.

**Reproducibility — the most interesting number, and the least trustworthy.**
Per arm, over calls where both repeats of that arm succeeded:

```text
v2   23/33  (70%)      consistent with Phase A's 74% over 47 calls
v3   26/30  (87%)
```

A 17-point gap is much larger than Phase A's 6-point v1→v2 difference, and
`v2` reproducing at 70% here against 74% in Phase A suggests the measurement
itself is stable to within a few points. That makes 87% **worth chasing** — but
n=30, on a throttle-biased subset, is not a result. It is a reason to re-run.

## Decision

**`config.yaml` stays on `scoring_prompt_version: v2`.** v3 is directionally
better on all three measures and not adopted on any of them, because the sample
that produced those measures lost a third of its requests non-randomly. Adopting
on this evidence would repeat the error the `reasoning_effort` A/B already made
once in this repo.

**Re-run Phase B when the Vertex quota has recovered**, at `--concurrency 4` or
lower, ideally when no other eval traffic is running. If v3's reproducibility
holds anywhere near 87% over a full 47 calls, it should ship.

## What Phase B did establish

- **The universal-core idea works on the call type it was aimed at.**
  Pricing/Negotiation's median rose a full point, and it is no longer the
  outlier that a single set of thresholds cannot serve.
- **It is not sufficient on its own.** Demo rising to 4.75 says the shared six
  are still easier to satisfy on some call types than others — the wording is
  stage-neutral but the *calls* are not. Equal questions do not automatically
  mean equal difficulty.
- **Threshold recalibration is still blocked**, for the same reason as after
  Phase A: a 1.55-point spread is not much better than 1.80 for that purpose.

## Next thing worth testing (not yet run)

**Feed the gap analysis into scoring.** Currently the four steps run in order
`call_type → scoring → gap_analysis → card_type`, so the scorer never sees the
gaps. Reordering to `call_type → gap_analysis → scoring → card_type` is
dependency-legal and would give scoring the pipeline's only evidence-verified
signal (gaps pass both citation validation and entailment verification).
Precedent exists: `CardTypeContext.gaps` was added for exactly this reason.

Cheap to test — the 47 stored rows already carry `risk_gap_analysis`, so no gap
re-run is needed. Two risks to measure rather than argue: the score would
inherit the gap rubric's blind spots (Kick-off's themes saturate at 75–100% and
three themes have never fired), and it couples the score's stability to the gap
step's, which differs between identical runs on 43% of calls.
