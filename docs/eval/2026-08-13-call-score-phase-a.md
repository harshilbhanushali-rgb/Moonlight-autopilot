# Phase A — score by category: what it measured

**Date:** 2026-08-13
**Spec:** `docs/superpowers/specs/2026-08-13-call-score-redesign-design.md`
**Raw data:** `docs/eval/2026-08-13-call-score-phaseA.json` (+ `.log`)
**Reproduce:**

```bash
uv run python -m app.services.eval.call_score_ab --nonce phaseA1 --repeats 2 --concurrency 8 \
    --out docs/eval/2026-08-13-call-score-phaseA.json
uv run python -m app.services.eval.call_score_bands docs/eval/2026-08-13-call-score-phaseA.json
```

47 calls × 2 prompt versions × 2 repeats = 188 scoring requests, one run, zero
errors. Every call scored with the prompt for its **stored** call type, so
prompt selection is constant and the prompt version is the only variable. Both
arms and both repeats ran in the same pass — a fresh arm compared against the
stored `analysis` values would attribute run-to-run churn to the rewrite.

## Verdict against the criteria fixed before the run

| # | criterion | target | v1 | v2 | |
|---|---|---|---|---|---|
| A-1 | tier agreement, two identical re-runs | ≥ v1 + 10pp | 68% (32/47) | **74% (35/47)** | **missed** |
| A-2 | Pricing/Negotiation Low rate | < 60% | 100% (18/18) | **50% (9/18)** | **passed** |
| A-3 | call types with an unreachable tier | ≤ 1 of 6 | 1 of 6 | **3 of 6** | **failed, worse** |
| A-4 | subscores available for every scored call | all | none | **all** | **passed** |
| A-5 | scoring-step latency | ≤ 2× | not captured | not captured | see below |

A-5's timing field was added after this run and is instrumented for Phase B.

## What worked

**N/A fixed the Pricing sink outright, and the mechanism is confirmed.**
Pricing/Negotiation went from **18 of 18 runs Low** to 9 of 18, and it now
reaches High at all. The per-category data shows exactly why: on Pricing calls,
*Value Anchor Defense* is N/A **67%** of the time, *Give-Get Discipline* 67%,
*Handling Price Objections* 67%. Those were the categories v1 was scoring 1 —
not because the reps handled them badly, but because the calls never reached
pricing at all. This was root cause 3 in the spec and it is now measured rather
than argued.

**The tier is auditable.** Every scored call carries its ten
`{name, score, evidence}` rows in `analysis.call_score_categories`, so a flipped
score can be attributed to a category. Everything else in this document is a
consequence of having that data — none of it could have been computed a day ago.

## What did not work

**A-1: reproducibility improved by 6 points, not the 10 required.** 68% → 74%.
Real, but inside what one run of 47 calls can carry, so it should not be quoted
as a reliability win. The flips did shift in the right direction —
`Low↔Medium` fell from 6 to 4 — but `High↔Medium` barely moved (9 → 8).

The reason is visible in the mean distribution: **27% of runs land within 0.15
of a band edge.** Those calls are coin flips no matter how good the subscores
are, because the tier is a threshold on a mean and one category moving one point
shifts the mean by 0.1. Root cause 2 survives Phase A untouched — Phase A fixed
*who does the arithmetic*, not *where the edges sit*.

**A-3: tier reachability got worse, from 1 blocked call type to 3.** v1 could
not produce High or Medium on Pricing; v2 cannot produce Low on Demo, Follow-up
Demo or Technical Integration. This is the direct consequence of the fix that
worked: excluding N/A categories removes the low scores, so every call type's
mean rises, and the thresholds were calibrated for a denominator that included
them. Phase A traded a false-Low problem for a false-High problem.

## The finding that decides Phase B

Median mean per call type, all repeats:

```text
Demo                    4.50        Discovery              3.20
Kick-off                4.40        Pricing/Negotiation    2.86
Technical Integration   4.33
Follow-up Demo          4.30
```

**A 1.5-point gap between call types, under one shared set of band thresholds.**
Two clusters, not a spectrum. It is visible at category level too: on Demo,
seven of ten categories average above 4.1; on Discovery, seven of ten average
below 3.6, and *Agenda Setting & Control* averages **1.90**.

The offline band sweep (`call_score_bands.py`, no gateway cost — possible only
because the subscores are now stored) confirms no threshold escapes this:

| medium / high edge | agreement | unreachable tiers | H/M/L |
|---|---|---|---|
| 2.80 / 4.20 (shipped) | 74% | 3 of 6 | 18/22/7 |
| 2.80 / 4.00 | 85% | 3 of 6 | 26/14/7 |
| 3.20 / 4.00 | 87% | 3 of 6 | 26/10/11 |
| 3.50 / 4.20 | 72% | **0 of 6** | 18/14/15 |

Every setting that restores reachability costs agreement, and every setting that
buys agreement does so by pushing calls into the wide High band — 26 of 47 calls
High is not a credible quality distribution. **Threshold tuning cannot fix a
1.5-point difference in how hard six category sets are to satisfy.** Only
equalising the categories can, which is Phase B.

## Category-level notes worth giving the rubric owners

- **Three categories are nearly always inapplicable and discriminate nothing
  when they do fire.** *Security/Compliance/Data Governance Control* (Technical
  Integration) is N/A 64% of the time and scored exactly 3 on **every** call
  that reached it — zero spread, so it carries no information at all.
  *Engagement of Secondary Stakeholders* (Follow-up Demo) is N/A 61% and scores
  5.00 whenever it applies. *Addressing New/Expanded Buying Committee Members*
  is N/A 72%.
- **The least reproducible categories are the judgement-heavy ones.**
  *Executive Reassurance & Value Realization Setup* returned the same score on
  only 25% of repeats, *Executive Presence under Pressure* 33%, *Preparation &
  Account/ATS Research* 40%, *Root Cause Identification* 40%. The most stable
  are the concrete ones — *Engagement & Check-ins*, *Competitive Positioning*,
  *Agenda & Time Management*, all ≥ 75%.
- **Discovery's *Agenda Setting & Control* averages 1.90 across 20 runs**
  (range 1–3). Either Joveo reps genuinely do not set agendas on discovery
  calls, or the category is written to be unsatisfiable. Worth a human look —
  it is the single harshest category in the whole rubric set.

## Caveats

- **No accuracy claim is made or possible.** There is no human call score in
  either database, so every figure here is self-consistency or calibration.
  Whether a High is a good call is still unmeasured; ~50 auditor-labelled calls
  would be worth more than any further prompt work.
- One run of 47 calls. Differences under ~10 points are not separable at this
  sample size, which is why A-1's 6-point gain is reported as a miss rather
  than a small win.
- The Pricing and Kick-off cells rest on 9 and 4 calls respectively.
