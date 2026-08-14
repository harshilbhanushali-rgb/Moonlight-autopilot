# Call Score redesign — design

**Date:** 2026-08-13
**Status:** approved by the user 2026-08-13, ahead of implementation
**Scope:** `app/prompts/scoring/<call_type>/*.txt`, `app/domain/scoring.py`,
the scoring half of `app/services/batch/orchestrator.py`, `analysis` schema,
and a new eval harness.

## The problem, measured

`analysis` holds 51 rows. Score by call type, one run each:

| call type | High | Medium | Low | n |
|---|---|---|---|---|
| Demo | 5 | 3 | **0** | 8 |
| Follow-up Demo | 4 | 5 | **0** | 9 |
| Discovery | 1 | 4 | 5 | 10 |
| Technical Integration | 2 | 2 | 3 | 7 |
| Kick-off | 1 | 1 | 2 | 4 |
| Pricing/Negotiation | **0** | 1 | **8** | 9 |

Demo and Follow-up Demo produce **zero Low across 17 calls**;
Pricing/Negotiation produces **zero High and 8 Low across 9**. Against the
overall 43% Low base rate, 0-in-17 has p ≈ 1e-4. The score is substantially
reporting *which of six prompts ran*, not how the rep did.

Separately, CLAUDE.md records `call_score` flipping on **22% of calls** when the
identical input is re-run at the identical settings.

### Five root causes

1. **The tier is a threshold on a hidden mean.** The prompt says *"compute the
   average of the 10 category scores, then map it to a tier"*, but the schema
   returns only `{"call_score": "Medium"}`. The ten numbers are never emitted,
   so the model's arithmetic is unaudited and a flip cannot be attributed to a
   category.
2. **The bands are miscalibrated for a mean of ten ordinal scores.** High needs
   ≥ 4.2 — effectively all 4s and 5s across ten dimensions. A mean of ten 1–5
   judgements is strongly central-tendency, so nearly all real mass lands
   2.5–3.5, straddling the 2.7/2.8 boundary. **One category moving one point
   shifts the mean 0.1 and can cross a tier.** That alone is sufficient to
   produce the measured 22% flip rate; no model randomness is required.
3. **There is no N/A.** A Pricing call that never reached procurement is still
   scored on *Give-Get Discipline* and *Securing Closing Commitments*, and an
   absent occasion reads as a 1. Eight such scores drag the mean under 2.7 →
   Low, deterministically. That is the 8-of-9.
4. **Six category sets, one set of bands.** Demo's ten are things any product
   conversation does; Pricing's ten are all closing acts. Identical thresholds
   over non-equivalent lists is why the tier encodes prompt selection.
5. **One category is unevaluable.** Discovery's *Talk Time Balance (Target:
   Prospect 55–70%)* is a ratio the same prompt forbids computing (*"no
   talk-time calculators"*). It is noise in the mean.

## Decisions taken with the user

- **The output classes stay `High` / `Medium` / `Low`.** Everything else —
  categories, wording, thresholds, output shape — is open.
- **"High" must mean the same thing on every call type.** A comparable grade,
  not best-in-class-for-this-stage. This rules out per-type band recalibration
  as the fix.
- **Validation is label-free.** There is no human score anywhere: no
  score/rating/grade column exists in Koushik's schema, and `moonlight_cards`
  holds 3 rows. Success is measured by re-run agreement and tier reachability,
  both of which need no ground truth. This cannot prove the score tracks real
  rep skill, and no claim of that kind may be made from these numbers.
- **The gap-theme constraint (2026-08-13) is untouched.** That fixed gap
  *themes*. This is the scoring prompt, a different artefact, whose categories
  the user has explicitly opened.

### Boundary calls, recorded so they are not re-litigated

- **Computing the tier in code is not a breach of "no deterministic/rule-based
  checks."** That boundary is scoped to *gap detection* and to re-deriving a
  call *from transcript text*. Averaging ten numbers the LLM itself produced is
  arithmetic on its own judgement — the same category as `app/domain/citation.py`
  checking a citation but never the judgement.
- **Injecting a computed talk-time ratio would derive from transcript text.**
  It is for scoring, not gap detection, so the boundary does not formally bite,
  but the scoring prompt itself bans calculators, so it is a deliberate
  reversal. **Phase A takes the cheaper option: the talk-time category becomes
  N/A-able like any other and is not computed.** Revisit only if the subscore
  data shows it is a live noise source.

## Phase A — instrument it

Keep the six category lists as they are. Change four things.

**A1. Return the subscores.** `CallScoreResponse` becomes a list of ten items,
each `{name, score, evidence}` where `score` is `"1".."5"` or `"N/A"`.
`evidence` is ≤ 25 transcript words that decide the score, or `"none"` for N/A.
The evidence requirement mirrors a finding already recorded in
`app/domain/response_models.py`: a step that only has to emit a label defaults
to agreeing — measured at 71% on the gap verifier — and forcing it to produce
the underlying words changed that.

**A2. Add N/A.** Explicitly: N/A when the call never created an occasion for the
behaviour; 1 or 2 when the occasion arose and was mishandled. Absence of an
occasion is not a failure by the rep. N/A categories are excluded from the
denominator.

**A3. Behavioural anchors.** Replace bare *"1 (poor) to 5 (excellent)"* with
explicit 1/3/5 descriptions shared across all categories. This is the largest
per-token reliability lever in rubric design and the thing that makes a 1–5
judgement reproducible at all. Anchors are kept short deliberately —
CLAUDE.md records that a 74%-longer rubric description measurably suppressed
byte-identical themes elsewhere in the same file.

**A4. Compute the tier in code.** Mean over non-N/A categories, mapped by the
band thresholds. **The thresholds stay at the business team's stated 4.2 / 2.8
for Phase A.** Setting them from 47 calls of our own data would be overfitting,
and the honest result of Phase A includes what the mean distribution actually
does. Recalibration, if the data demands it, is a separate documented decision
with its own A/B.

### Phase A success criteria — fixed before any run

Measured by `eval/call_score_ab.py` over every stored call that
has a `call_type`, using each call's **stored** call type so both arms select
the same prompt and the scoring prompt is the only variable. `--nonce` is
mandatory: the gateway serves a response cache keyed on the request messages,
so an un-nonced repeat replays the first answer and scores a meaningless 100%.

**The same-setting control runs first.** v1 is run twice under different nonces
to establish its own flip rate on this corpus. Comparing a fresh v2 against a
single stored v1 run would attribute run-to-run churn to the rewrite — the
mistake that produced a confident, wrong "70% agreement" in the
`reasoning_effort` A/B.

| # | criterion | baseline | target |
|---|---|---|---|
| A-1 | tier agreement between two identical re-runs | v1, measured in-run | ≥ v1 + 10pp |
| A-2 | Pricing/Negotiation Low rate | 8/9 = 89% | < 60% |
| A-3 | call types with a structurally unreachable tier | 2 of 6 | ≤ 1 of 6 |
| A-4 | per-category subscores available for every scored call | none | all |
| A-5 | scoring-step latency | ~15–25s | ≤ 2× |

A-3 is expected to be only partly served by Phase A; full comparability is
Phase B's job. A-1 and A-2 are Phase A's real tests.

**A-1 is the one that can fail honestly.** If v2's agreement is inside v1's, the
instrumentation still ships for its diagnostic value (A-4), but no reliability
claim is made.

## Phase B — universal core plus stage extras

Designed **from Phase A's subscore data, not before it.** The whole point of
sequencing A first is that nobody currently knows which of the sixty categories
discriminate; picking a universal core now would be guesswork of exactly the
kind that made the earlier rubric A/Bs inconclusive.

Shape: roughly six universal categories scored on *every* call — preparation,
listening/probing, value framing, objection handling, call control/agenda, next
steps & commitment — plus roughly four stage-specific ones drawn from the
existing per-type lists, all N/A-able. The tier comes from the universal core;
stage extras modify it.

Two things this buys beyond A:

- **Comparability by construction**, which is what "High means the same on every
  call type" requires.
- **It defuses the `call_type` cascade.** A misclassification perturbs 4 of 10
  categories instead of the entire rubric. This matters because `call_type` is
  the least reliable step in the pipeline (11% flip; one RTX series spread over
  5 types), and it currently selects the scoring prompt outright.

It also matches a finding already in CLAUDE.md: *Demo is the only rubric with
universal themes and the only one that does not saturate.*

**Phase B success criteria:** A-1 through A-3 re-measured against Phase A as the
new baseline, plus B-1: **no call type may have an unreachable tier** (A-3 → 0
of 6). Phase B is adopted only if it beats Phase A on A-1 without losing on A-2.

Phase B changes business-team-owned content, so it ships with a written handover
in `docs/` in the shape of `docs/gap-rubric-review-2026-08-12.md`.

## Phase C — backup only, if A and B both fail

**Two-pass scoring: extract, then judge.** A first pass extracts the observable
behaviours from the transcript without scoring them; a second scores from that
structured extract rather than from raw dialogue. Separating perception from
judgement is the standard remedy when a rater is inconsistent for reasons
wording cannot fix.

Held in reserve, not built, because the costs are real and known:

- It **doubles gateway calls** for the scoring step.
- It **adds a fifth step** to a four-step pipeline. This codebase has refused
  that before on purpose — gap verification was deliberately kept *inside* the
  gap step so its failures stayed on the existing status/retry/dead-letter path
  and needed no new status columns or circuit-breaker changes. Phase C would
  have to follow the same rule: the extract pass lives inside the scoring step,
  and its failures are `scoring_status` failures.
- It is **unvalidated**; nothing here shows it beats an anchored single pass.

**Trigger for reaching for C:** Phase A and Phase B both fail A-1 — i.e. tier
agreement stays inside the v1 baseline after both the mechanical fix and the
structural one. That would mean the variance is in the model's *perception* of
the call, which no amount of rubric wording can reach.

## What changes in the schema

`analysis` gains one nullable `JSONB` column, `call_score_categories`, holding
the ten `{name, score, evidence}` items from the run that produced the current
`call_score`. Nullable and additive, so the 51 existing rows stay valid and
Koushik's side is unaffected — but it is a new column on a table they read, so
it goes on the list of things to tell them, alongside the `status='excluded'`
value already recorded in CLAUDE.md.

`call_score` itself stays a `String` holding `High`/`Medium`/`Low`. The output
contract to Koushik's side does not change.

## What is deliberately not done

- **No per-call-type band recalibration.** It is the obvious fix for the
  distribution and it is ruled out by the user's requirement that High mean the
  same thing everywhere.
- **No backfill of the 51 existing rows.** They were produced without subscores
  and genuinely have none — the same rule already applied to prompt provenance.
- **No accuracy claim.** Every number produced here is self-consistency or
  calibration. Whether a High is a good call remains unmeasured, and the fix for
  that is ~50 auditor-labelled calls, not more prompt work.
