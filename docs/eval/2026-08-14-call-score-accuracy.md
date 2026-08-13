# Call score, measured against human grades

**Date:** 2026-08-14
**Labels:** `app/services/eval/call_score_labels.py` (47 calls, blind)
**Standard:** `docs/eval/2026-08-14-call-score-grading-standard.md` (pre-registered)
**Reproduce:**

```bash
uv run python -m app.services.eval.call_score_accuracy docs/eval/2026-08-13-call-score-phaseA.json
uv run python -m app.services.eval.call_score_bands  docs/eval/2026-08-13-call-score-phaseA.json --labels
uv run python -m app.services.eval.score_evidence_audit docs/eval/2026-08-13-call-score-phaseA.json
```

## Method

47 calls graded from transcripts carrying the call type and nothing else — no
tier, no subscores, no gaps. 15 by the primary reviewer as calibration anchors,
32 by six independent graders reading the same pre-registered standard and the
same anchors. A seventh grader re-graded 8 calls to measure inter-rater
agreement.

3 calls are `UNGRADABLE` (Joveo is the buyer). 1 is excluded as contaminated.
43 remain.

## Results

| | agrees | too generous | too harsh | opposite tier |
|---|---|---|---|---|
| v1 (original prompt) | **24/43 (56%)** | 10 | 9 | 0 |
| v2 as shipped (High ≥ 4.2) | 23/43 (53%) | **19** | 1 | **2** |
| v2 recalibrated (High ≥ 4.7) | **33/43 (77%)** | 7 | 3 | **0** |

**v2 as shipped was not an improvement.** It was marginally worse than v1, and
worse in a specific way: 19 of its 20 errors graded a call too generously, and
it produced two opposite-tier errors v1 never made.

The cause is mechanical. Excluding N/A categories from the mean — the fix that
worked, taking Pricing/Negotiation from 18/18 runs Low to 9/18 — removed the low
scores that used to drag every mean down. Means rose; the thresholds did not.

### Holdout, not a fit

Splitting the 43 calls in two, choosing edges on one half and testing on the
other: **+14 and +9 points** in the two directions. In-sample best is 77%, so
**~65–75% is the honest expectation**. The winning region is a broad plateau
(High 4.6–4.8, Medium 2.8–3.1 all identical), so no decimal is over-read.

### Everything else moved the right way too

- **Reproducibility** 74% → **81%** (v1 was 68%).
- **Opposite-tier errors** 2 → **0**. Across 86 judgements neither version
  confuses High with Low more than twice.
- **Per call type** evens out: Demo 7/8, Discovery 8/9, Kick-off 4/4,
  Pricing 5/7, Technical Integration 5/7 — and **Follow-up Demo 4/8**, the one
  remaining weak spot.

### Tier reachability is superseded, not failed

By the old label-free criterion the recalibrated scorer looks worse — all six
call types now fail to produce some tier. That criterion was a proxy invented
when there was no ground truth, and the labels retire it:

```text
true (43 hand grades)   High  9%   Medium 63%   Low 28%
recalibrated v2         High 10%   Medium 73%   Low 17%
```

High is genuinely rare, so a call type of n=8 producing no High is the base
rate, not bias. The residual gap is Low: the scorer still under-calls it
(17% vs 28%), consistent with 7 remaining too-generous errors. Raising the
Medium edge is the obvious next lever and scores identically on accuracy — a
reason to test it, not to ship it.

## Evidence groundedness

Every v2 subscore cites transcript words. Checked with the same word-run matcher
gap citations must pass:

```text
quotes checked            : 826
fully in the transcript   : 749 (91%)
>= 60% coverage (gap bar) : 826 (100%)
< 20% (fabrication shape) : 0
```

v1 emitted no evidence, so this is a capability it lacked rather than a degree
of improvement.

## The ceiling

Inter-rater agreement between two independent graders on the same standard:
**7/8 (88%)**, zero opposite-tier disagreements. **The overlap sample is almost
all Medium**, so it measures agreement on the easy middle and should be read as
an optimistic ceiling.

At ~65–75%, the recalibrated scorer has closed roughly half the distance from
v1 (56%) to that ceiling.

## Limits, stated plainly

- **All labels are calibrated to one reviewer's bar** — every grader read the
  same 15 anchors. This measures agreement with that reviewer, not with
  Moonlight's standard. ~20 auditor-graded calls would make it checkable and
  remains the highest-value thing to ask the business team for.
- 43 calls. Differences under ~10 points are not separable.
- Kick-off n=4, Pricing n=7.
- The threshold was chosen on this corpus. The holdout supports it; a fresh
  batch of labelled calls would confirm it.
