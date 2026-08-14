# Gap rubric review — findings and recommendations

**Date:** 2026-08-12
**Audience:** whoever owns `app/prompts/gap_rubric/` (business team)
**Status:** recommendation. No rubric file has been changed.

---

## What this is

Every New Business sales call is read by an AI that checks it against a rubric of
coaching themes and reports the ones it finds, each with a quote from the call as
evidence. Those reports become the feedback cards a human moderator reviews.

We audited the output over **46 real calls / 86 reported gaps**, by reading the
transcripts against what the AI said about them. This document covers what we found
about the **rubric** specifically — which themes are working, which are not, and why.

Two things to know about how confident to be in each finding:

- **Verifier measurements are trustworthy.** We built a second AI pass that re-checks
  each gap against its own evidence, and ran it over the same 86 gaps twice. The two
  runs agreed on **84 of 86** (98%). Gap *generation* by contrast reproduces on only
  43% of calls, so anything measured about the generator is noisy and anything measured
  about the verifier is not.
- **Human labels are one reviewer's.** 28 gaps were hand-labelled by reading the
  surrounding transcript. Where this document leans on those, it says so. They are not
  business ground truth and should get a second pair of eyes.

Raw data and the harnesses that produced it: `eval/verification_replay.py`,
`eval/theme_falsifiability.py`.

---

## Summary of recommendations

| # | Recommendation | Evidence strength |
|---|---|---|
| R1 | Rewrite 2 themes that fire on their own counter-example | **Strong** — 0/9 survived, two runs, human-confirmed |
| R2 | Add a "what passing looks like" clause to every theme | **Strong** — structural; drafts supplied below |
| R3 | Reduce theme overlap: 3 themes restate one idea | **Strong** — textual |
| R4 | Decide the fate of 3 themes that have never fired | **Moderate** — 46-call sample |
| R5 | Consider splitting the 3-theme rubrics | **Moderate** — correlational |
| R6 | Seed the empty `*-fewshot.yaml` files with 9 validated examples | **Strong** — real calls, triple-validated |

Nothing here asks for a change to how the pipeline works. R1–R5 are edits to theme
wording; R6 fills files that are currently placeholders.

---

## R1 — Two themes fire on evidence that disproves them

These two were reported 9 times between them. **All 9 were rejected**, in both
verifier runs, and human review independently agreed on the ones it checked.

### `No Pre-Call Research on Client's Current Stack` (discovery) — 0/5 kept

Current text:

> Discovery calls begin without surfacing what the competitor is doing for the client
> today. Joveo positions generically rather than specifically against the known
> incumbent's gaps.

**Two problems.** The title says *pre-call research*; the body describes *in-call
surfacing*. Those are different things, and the model anchors on the title. And the
text fuses two separate claims — (a) the incumbent was not surfaced, (b) positioning
was generic — so it can fire when either half looks true.

What it actually produced:

| call | quote offered as proof |
|---|---|
| 57 | *"I saw you're using Symphony on your career site."* |
| 257 | *"I know you mentioned in one of your emails that you've been having a hard time with Indeed."* |
| 274 | a legitimate discovery question about the partner's end clients' vendors |

The first two are the rep **demonstrating** research, cited as proof he did none.

**Suggested replacement** — one claim, precondition stated, pass criteria included:

> **Incumbent Vendor Not Probed**
> Applies only when the client has an existing programmatic or recruitment-marketing
> vendor. The rep does not ask what that vendor does not solve for them, so Joveo's
> positioning stays generic instead of aimed at a named weakness.
> Do **not** report this theme if the rep names the incumbent, references prior
> research about the client's stack, or asks any question about the incumbent's
> performance or gaps.

### `No Competitive Framing at Pricing Stage` (pricing) — 0/4 kept

Current text:

> The client's current programmatic provider is known but Joveo does not establish why
> switching makes commercial sense. Value framing should anchor pricing to ROI, not cost
> alone.

**Problem.** "is known" is a precondition that nothing enforces, and the second
sentence is guidance to the seller rather than a description of a gap, so the model has
no clear test to apply.

Two of the four fired on calls with no incumbent-pricing situation at all. On call 43
the client had *just asked for* a Joveo-vs-Broadbeam comparison slide — competitive
framing was actively happening.

**Suggested replacement:**

> **Switching Case Not Made at Pricing Stage**
> Applies only when the client has a current paid provider AND pricing or commercial
> terms are actually discussed on this call. Joveo gives a price without quantifying
> what the client gains by switching — no comparison of cost, waste, or performance
> against what they pay today.
> Do **not** report this theme if any comparison against the current provider is made
> or requested, or if no incumbent pricing exists to compare against.

### Not included, deliberately

`Imprecise Messaging & Stats` (demo) was also rejected in both runs — but it fired
**once** in 46 calls, and the human reviewer judged that one instance arguably valid.
One data point is not a finding. Leave as-is; revisit if it recurs.

---

## R2 — Every theme describes failure and none describes success

All 25 themes state only what a bad call looks like. None states what a good one looks
like. That matters because the AI is handed the list and asked to find matches: with no
description of "fine" to compare against, an ambiguous call tends to match.

We generated candidate pass criteria for all 25 themes by asking the model to describe
a call where each theme is clearly absent, and to name what a reviewer would actually
see. A sample:

| theme | what a passing call looks like |
|---|---|
| `Wrong People on the Call` | technical questions are answered in the moment — by anyone present — and the client confirms no separate technical call is needed |
| `No Committed Timeline` | specific calendar dates named for each milestone, verbally agreed by the client, written into a shared plan during the call |
| `Success Metrics Not Agreed at Start` | named metrics with targets, an agreed reporting cadence, and a scheduled QBR date |
| `Escalation Path Not Established` | a named individual for blockers, a named channel, and a named backup |
| `No Pre-Call Research` | the rep names the incumbent unprompted and asks about a specific limitation of it |

**Recommendation:** add a `Do not report this theme if…` clause to each theme, drawn
from these. Full set for all 25 themes is in the harness output — ask and we'll format
it as a paste-ready diff.

**Caveat.** These were generated from model-written example calls, not from real Joveo
calls, so they describe the clear-cut end of each theme. They are a starting point for
the rubric owner to sharpen, not finished text.

---

## R3 — Three themes across three rubrics restate one idea

- `No Pre-Call Research on Client's Current Stack` (discovery)
- `Competitive Intelligence Not Used` (demo)
- `No Competitive Framing at Pricing Stage` (pricing)

All three reduce to *"the rep did not probe what the incumbent does badly."* Two of the
three are the worst-performing themes in the corpus (R1); the demo version is the only
one that works (kept 1/2, and its one good instance is in R6).

**Recommendation:** keep one well-specified version, scoped by what the call is for —
in discovery it's a question the rep should ask; at pricing it's a number the rep should
quantify. Right now the same idea is stated three times, loosely, in three places.

---

## R4 — Three themes have never fired

Across 46 calls, 25 themes are defined and only 22 have ever been reported. Never once:

- `Slide Reading & Poor Storytelling` (demo)
- `Irrelevant or Inaccurate Content Shown` (demo)
- `Unclear Ownership of Action Items` (follow-up demo)

Two readings, and we can't distinguish them from 46 calls: either these problems are
genuinely rare in Joveo's calls, or they are worded so they never match. Worth the
rubric owner's judgement — `Slide Reading` in particular is the kind of thing a human
auditor would expect to see sometimes.

---

## R5 — Rubrics with three themes report a gap on nearly every call

A theme reported on every call of its type carries no information — it cannot separate a
good call from a bad one.

| theme | fires on |
|---|---|
| `IC-Level Only at Pricing Stage` | **7 of 7** pricing calls |
| `No Committed Timeline` | **7 of 7** technical integration calls |
| `Escalation Path Not Established` | 6 of 7 technical integration calls |
| `Scope Not Locked Before Kick-off` | **4 of 4** kick-off calls |
| `Success Metrics Not Agreed at Start` | **4 of 4** kick-off calls |
| `Kick-off Agenda Not Proactively Set` | 3 of 4 kick-off calls |

Every kick-off call receives the identical three gaps. All four `Success Metrics` gaps
share **the same sentence**; five of six `Escalation Path` gaps do too.

The pattern tracks **how many themes the rubric offers**:

| rubric | themes | outcome |
|---|---|---|
| demo | **9** | discriminates normally, no theme saturated |
| kick-off | 3 | all three saturate |
| technical integration | 3 | two saturate |
| pricing / negotiation | 3 | one saturates |
| follow-up demo | 3 | *does not saturate* |

Nine options give the model something to choose between; three leave it matching
whatever is closest. Follow-up demo breaks the pattern, so theme count is not the whole
explanation — treat this as a strong hint, not a proven mechanism.

**Recommendation:** expand the three-theme rubrics toward the demo rubric's granularity,
and state explicitly in the rubric that most calls will exhibit **none** of its themes.

---

## R6 — Nine validated examples, ready for the empty `fewshot` files

`app/prompts/gap_rubric/*/v1-fewshot.yaml` currently contain placeholder content. These
nine gaps are real moments from real Joveo calls, each confirmed correct by a human
reading the transcript **and** kept by the verifier in both independent runs.

| theme | call | timestamp | evidence |
|---|---|---|---|
| Technical Prerequisites Not Validated | 53 | 02:12 | *"I'll have to ask Rich if it will support too… I really don't know."* |
| IC-Level Only at Pricing Stage | 263 | 27:53 | *"It is not [budgeted]… I'd have to go and talk to my VPs and to finance and procurement."* |
| IC-Level Only at Pricing Stage | 43 | — | whole-call: only the programme manager present, who says the decision is *"way above me"* |
| Seller-Dominated / Poor Time Management | 41 | 67:50 | *"We're way over."* — next steps squeezed into overtime |
| Missed Strategic Deal-Driving Moments | 260 | 86:35 | decision-maker closes with *"I'm gonna be on vacation for the next couple of weeks"*, no date set |
| Unanswered / Poorly Handled Questions | 260 | 50:51 | *"I'd have to ask engineering… Don't know. We'd have to check on that."* |
| Outcome-Based Positioning Missing | 274 | 06:19 | a 15-minute capability tour with no outcome framing |
| Demo Not Customised to Client | 264 | 14:02 | a retail example shown to a healthcare buyer |
| Competitive Intelligence Not Used | 41 | — | whole-call: client signals dissatisfaction with Radancy twice, never probed |

**These cover 8 of 22 themes. Fourteen have no validated example** — and that set
includes every theme in R1 and every saturating theme in R5. That overlap is the point:
a theme with no correct instance in 46 calls either isn't real or isn't defined well
enough to be recognised. Examples cannot fix a bad definition, so R1–R3 should land
before the fewshot files are filled out.

---

## What we changed on our side (context, not a request)

Three fixes shipped during this audit, none of which touch the rubric:

- **Citation checking.** Every quoted gap is now confirmed to appear in the transcript,
  and its timestamp is corrected to the moment it was actually said. 13 of 58 timestamps
  were wrong, one by 74 seconds; 4 pointed at a different speaker. **No quote was ever
  fabricated** — the model quotes real speech.
- **Evidence verification.** A second AI pass re-checks each gap against its own
  evidence and drops the ones it contradicts. Measured: **90% of good gaps kept, 67% of
  bad gaps removed.** This is a filter, not a cure — it spends effort deleting output
  that shouldn't have been produced. R1–R5 are the actual cure.
- **Card Type** now sees the gaps, so a card can no longer be labelled Risk while all of
  its gaps are rep-coaching observations.

## One thing we got wrong, recorded so it isn't repeated

We hypothesised that themes the verifier never rejects (`Wrong People on the Call`,
`Outcome-Based Positioning Missing`, and three others) were **unfalsifiable** — worded
so that no evidence could disprove them. We tested it twice, including a version using
deliberately misleading example calls. **Both tests cleared all 25 themes.** The model
distinguishes present from absent reliably, even on hard cases.

The real cause appears to be the *question we were asking it*: production asks "does
this quote support this claim someone already made?", which pulls toward agreement,
where the test asked the open question "does this call exhibit this theme?" and got 0
false positives out of 25. That is our design problem, not a rubric problem, and it is
being followed up separately.

Recorded here because the earlier version of this document would have asked the rubric
owner to rewrite five themes that turn out to be fine.

---

## Suggested order

1. **R1** — 2 theme rewrites. Highest value, strongest evidence, smallest edit.
2. **R2** — pass criteria on every theme. Mechanical; drafts ready.
3. **R3** — de-duplicate the three incumbent-probing themes.
4. **R5** — expand the three-theme rubrics.
5. **R4** — decide on the three never-fired themes.
6. **R6** — fill the fewshot files, once 1–3 have landed.

Any of R1–R3 can be validated before adoption: the eval harness can run old and new
wording over the same 46 calls and diff the result. Because gap generation reproduces on
only 43% of calls, that comparison needs several runs per version, not one.
