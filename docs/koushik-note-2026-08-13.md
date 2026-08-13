# Draft note to Koushik's team — 2026-08-13

**Status: drafted, NOT sent.** Three items: one thing they need to know, one
question only they can answer, one heads-up.

---

## 1. A new `analysis.status` value: `excluded` (4 rows)

We've added an input gate that stops calls with no assessable conversation from
being analysed. It caught three recordings that were producing real coaching
cards:

- a 1,156-second meeting containing a single 30-word turn
- a meeting whose recording captures only everyone moving to Zoom (271 words)
- a call the client never joined, where two Joveo employees talk to each other

All three had been given a call type, a score of `Low`, and a card — which reads
to a moderator as *"this rep performed badly"* when it actually means *"there was
no conversation."*

**What changes for you:**

- **Calls excluded from now on will simply not appear in `analysis` at all.** They
  get no row, so nothing new shows up on your side. No action needed.
- **Four rows that already existed now carry `status = 'excluded'`.** This is a
  value your code has not seen before. Their `call_type`, `call_score`,
  `risk_gap_analysis` and `card_type` are deliberately unchanged — we kept them
  because they're the baseline for our prompt measurements — so **if your UI
  filters on `status`, these four need excluding; if it doesn't, they will still
  render as cards.** That's the one thing worth checking.

The recording ids are `35f28528…`, `b026da73…`, `7cf8dcfb…`, `4ac4eea2…`.

The gate's two rules, for reference: fewer than 300 words in total, or nobody on
the client's side actually spoke (measured from Avoma's `is_rep` flag against
which speakers have transcript turns — attendance isn't enough). Measured over
all 51 calls we hold: 4 rejections, no false positives.

## 2. Question: why did `moonlight_calls` drop from 296 rows to 197?

On 2026-08-12 the table went from **296 rows to 197 within a few hours** (id range
still 2..296, so ~99 rows were deleted). 19 of the 51 calls we'd fetched vanished
from it, while some accounts *gained* calls — Alexander Mann 4 → 18, Spring Health
3 → 14 — and Talroo and Pratt & Whitney lost all of theirs. All 156 accounts
stayed `active`, so this looks like call-level churn from a sync job rather than
deactivation.

We're treating a call absent from `moonlight_calls` as out of scope, which is
fine. But two things we can't resolve on our side:

- **Is that churn expected?** If rows can disappear and reappear, our backlog
  numbers and any measurement based on corpus size are unstable.
- **Our fetcher has no reconciliation.** It only diffs *new* ids forward, so a
  call deleted at source stays in our `call_storage` and `analysis` forever and
  nothing notices. Worth knowing whether you'd want us to reconcile deletions.

## 3. Heads-up: gap rubric recommendations (separate doc)

`docs/gap-rubric-review-2026-08-12.md` has measured recommendations for two gap
rubric themes that were firing on evidence that *disproved* them. Both rewrites
are measured. The most useful finding for the rubric owners is the negative one:
the same edit that improved the discovery rubric **made the pricing rubric worse**,
so adding preconditions to a theme is not automatically an improvement and each
rubric needs its own measurement.

That one is for Anantu's team rather than yours, but flagging it here so the two
don't arrive as a surprise together.
