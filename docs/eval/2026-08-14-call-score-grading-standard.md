# Call score grading standard — written before reading any transcript

**Date:** 2026-08-14
**Purpose:** hand labels for all 47 scored calls, to test whether the Phase A
scoring rewrite is more *correct*, not merely more consistent.

Committed **before** the first transcript is opened. That is the point of it: a
standard written afterwards is a rationalisation of what was seen, and the
accuracy figure it produces is unfalsifiable.

## The question I answer for each call

> **If I were this rep's manager and had just listened to this call, what would
> I do next?**

- **High** — nothing to correct. I would hold this up to other reps as how it
  should go. Not "flawless"; "nothing I'd coach."
- **Medium** — it worked, but I would give at least one specific, nameable piece
  of coaching. This is the expected default for a competent call.
- **Low** — I would want a conversation about it. The rep left significant value
  on the table, mishandled something material, or damaged the relationship or
  the deal.

Deliberately *not* an average of ten sub-dimensions. That is the machine's
method, and reproducing it by hand would test whether I can imitate the prompt,
not whether the prompt is right.

## Rules, fixed in advance

1. **Judge the rep, not the call's luck.** A hostile client handled well is
   High. A friendly client who would have bought anyway is not High unless the
   rep earned it.
2. **Judge execution, not outcome.** A call that ends without a next step is not
   automatically Low — sometimes there genuinely isn't one. A call that closes
   business despite sloppy work is not automatically High.
3. **Do not penalise a call for not being a different call.** A technical
   integration session that never discusses pricing has not failed; pricing was
   not its job. This is the exact error Phase A found in v1 and it must not be
   reproduced in the labels.
4. **One consistent bar across all six call types.** High must mean the same
   thing on a Demo and on a Pricing call. This is the product requirement, so
   the labels have to embody it or they cannot test it.
5. **Ignore transcription quality.** Garbled ASR, missing punctuation and
   mis-attributed speakers are Avoma's artifacts, not the rep's.
6. **`UNGRADABLE`** for anything that is not a Joveo rep selling to a prospect —
   internal calls, no-shows, supplier negotiations where Joveo is the buyer.
   These get no tier and are excluded from accuracy. Forcing a grade onto them
   is how v1 produced a `Medium/Risk` card for a job board refusing Joveo access.
7. **`borderline: True`** when I genuinely cannot separate two tiers. Reported
   separately rather than silently resolved, the same way
   `gap_audit_labels.py` handles them.
8. **A one-line reason is required for every call.** Without it I am
   pattern-matching, not judging, and nobody can audit the labels later.

## Blinding, and where it is imperfect

Labels are assigned from `docs/eval/blinded/<id>.txt`, which carries the
transcript, the call type and nothing else. No tier from any version, no
subscores, no gap output.

**Two honest contamination disclosures:**

- I have already seen the **full v2 breakdown for one call** — the RTX weekly
  onboarding call classified `Pricing/Negotiation` (v1 `Low`, v2 `Medium`). It
  is flagged `contaminated: True` in the labels and excluded from the headline
  accuracy figure.
- I have seen **aggregate distributions per call type** (e.g. that Demo skews
  High and Pricing skewed Low under v1). That could bias me toward those
  priors. I cannot un-see it; I can only name it. Per-call-type accuracy should
  be read with that in mind, and it is a reason to want an auditor's labels
  rather than mine.

## What these labels can and cannot support

**Can:** whether v2 agrees with a human more often than v1; whether either has a
systematic bias (too harsh, too generous); whether accuracy differs by call
type — which matters, since the whole defect is that call types behave
differently.

**Cannot:** stand in for Moonlight's actual standard. These are one
non-auditor's judgements. They measure agreement with me. If they disagree with
Anantu's team, the team is right and these labels are wrong.

**Sample limits, stated now so they are not glossed later:** 47 calls total, of
which some will be UNGRADABLE. Kick-off has only 4 calls and
Pricing/Negotiation 9, so per-call-type figures for those are indicative at
best. A difference smaller than roughly 10 percentage points overall should not
be called a result.
