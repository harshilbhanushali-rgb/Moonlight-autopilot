# Input gate — reject calls that aren't assessable NB sales conversations

**Date**: 2026-08-13
**Status**: Approved

## Problem

Nothing in the pipeline asks whether a call is worth analysing. Every row the
fetcher writes to `call_storage` gets an `analysis` row, runs all four LLM
steps, and produces a card a human moderator will eventually read.

Measured over the 32 in-scope calls, **4 are not sales calls at all**, and each
one produced a complete, confident-looking row
(`app/services/eval/call_type_labels.py::UNCLASSIFIABLE`):

| the recording | what the pipeline said |
|---|---|
| one 30-word turn across a 1156s meeting | Discovery / Low |
| Google Meet failed, everyone moved to Zoom — 27 turns of waiting, then goodbye | Discovery / Low |
| client never joined; two Joveo employees discuss the deck they meant to show | Follow-up Demo / Low |
| HelloWork — a French job board refusing Joveo *supply* access; Joveo is the buyer | Discovery / **Medium / Risk / 1 coaching gap** |

The first three share a failure: a `Low` score on a call where nobody spoke
reads to a moderator as *"this rep performed badly"* when it means *"there was
no conversation."* Those are opposite messages and the card cannot distinguish
them.

The fourth is worse because it looks fine. All six call types assume Joveo
selling to a hiring employer, so on a supply negotiation every rubric theme
aims at the wrong side of the table — and it still produced a Risk card with a
coaching gap. Nothing downstream catches this: citation validation only asks
whether a quote is real, and the entailment verifier only asks whether a gap's
evidence supports its claim. Neither can question the frame.

## Why this work, and why now

Everything else on the backlog is blocked on measurement. Gap generation
reproduces on only 43% of calls, `call_type` scored 6/8 then 8/8 on *identical*
held-out input, and no R1/R2 rubric output has been reviewed by a human. Any
prompt or rubric change is currently indistinguishable from churn.

The gate has no such problem. Its criteria are mechanical, so its decisions can
be checked by reading transcripts, and the answer does not move between runs.
**It is the only item on the backlog validatable without ground truth.**

It removes wrong output; it does not make the remaining output better. That is
the honest scope.

## Constraints from discussion

- **Scope is both failure shapes**: empty/artifact calls *and* calls where the
  client was never actually in the conversation. The third shape — the right
  *kind* of party is absent, i.e. the counterparty is a supplier rather than a
  buyer — turned out not to be detectable from a transcript at all, and is
  explicitly out of scope (see "The HelloWork call is out of reach").
- **The gate runs in the fetcher**, before an `analysis` row exists, so a
  rejected call costs no LLM spend.
- **Rejected calls are still stored** in `call_storage`, carrying the reason.
  Not storing them was considered and rejected: `filter_new_calls` decides
  novelty purely by absence from `call_storage`, so an unstored call is
  re-fetched from Avoma every run forever, and the rejection leaves no record.
- **Word floor is 300 words.** The three provably-empty calls are 30, 183 and
  271 words; the next-thinnest is 1120, whose `Low` may well be legitimate. A
  ~4x gap of headroom, erring toward analysing a thin call rather than silently
  dropping a real one.
- **The ambiguous 1000–2000 word band is out of scope.** CLAUDE.md already
  records it as a judgement call with no labels to settle it. Importing it would
  reintroduce the exact unverifiability this item was chosen to avoid.
- **Speaker identity is carried on the typed `Transcript` contract**, not in the
  loose `call_metadata` JSONB. The project's worst bug was turn timestamps
  living in a dict read with `.get(key, default)`: the fetcher quietly stopped
  carrying them, nothing failed, and the gap step invented plausible wrong
  timestamps. A gate whose input silently goes missing does not fail loudly — it
  stops rejecting anything and looks like a clean corpus.
- **Existing `analysis` rows keep their outputs.** They are the A/B baseline for
  every rubric measurement so far. The four bad rows get `status = 'excluded'`;
  their `call_type` / `call_score` / `risk_gap_analysis` / `card_type` are left
  exactly as they are.
- **This does not breach the LLM-only boundary.** That constraint forbids code
  re-deriving a *gap* call from transcript text. The gate decides whether a call
  is analysed at all and never inspects it for coaching content.

## Design

### The two rules

`app/domain/input_gate.py::evaluate_input_gate(transcript, config) ->
GateVerdict` — pure, zero I/O, unit-tested like the four existing domain steps.
It needs no account data: everything both rules read is in the transcript.

**R1 — no assessable conversation.** Total words across all turn texts
(whitespace-split) below `min_words` → reject, reason `no_conversation`.

**R2 — no client speech.** At least one speaker Avoma marks `is_rep=False` must
have **attributed turns** in the transcript. If none does → reject, reason
`no_client_speech`.

R2 deliberately checks speech, not attendance. A speakers entry only records
who Avoma *associated* with the meeting — an invitee resolved from the
calendar — which does not establish that they were on the call or said
anything. Every turn carries a `speaker_id`, so presence is answerable exactly:
collect the ids that have turns, look up those speakers, ask whether any is not
a rep. Matching on ids rather than name strings avoids the fragility of
"Marie" / "Marie D." / "Marie Dubois" / a mis-diarized "Speaker 2" all being
one person.

**`is_rep`, not account-domain matching.** The original design compared speaker
email domains against `moonlight_accounts.domain`. Measured over 51 real calls
that rejects **27, of which 24 are good calls** — account domains carry `www.`
and `about.` prefixes, the subdomain sits on the account side rather than the
speaker side, and subsidiaries mail from the parent (account `prattwhitney.com`,
speakers on `rtx.com`). `is_rep` agrees with "is this person on a `joveo.com`
address" on **every speaker of all 51 calls, zero disagreements**, and needs no
normalisation, no subdomain logic and no account data at all. Full measurement:
`problems-and-fixes.md` 8.17. **Do not reintroduce domain matching** without
re-measuring it.

### Fail-open cases

R2 does not reject when it cannot judge. This is recorded as a *skip*, not a
pass:

- **speech from an unlinked speaker.** On 3 of 51 calls some `turns[].speaker_id`
  has no entry in `speakers` — on one, 3,141 of 6,319 words. Those words cannot
  be attributed to either side, so R2 abstains. This single rule removes its only
  measured false positive (`e8bfc3fb`, an NHS demo where the client spoke 2,502
  words through unresolved ids).

Excluding on missing data would silently drop good calls, which is worse than
the problem being fixed.

### The HelloWork call is out of reach — accepted

Three of the four bad calls are caught. The fourth cannot be:

```text
0bbe93f1  "Joveo <> Hellowork <> RTX"   account_domain = hellowork.com
  Laetitia BOERI   hellowork.com   is_rep=False   29 turns   1898 words
  Nargis Roohi     joveo.com       is_rep=True    13 turns    381 words
```

A genuine non-rep spoke 1,898 words, and the account's own registered domain *is*
`hellowork.com`. By every structural signal this is an ordinary client call.
Distinguishing a supplier from a buyer requires account-level classification —
Koushik's data, not ours. An earlier version of this spec claimed all four were
mechanically detectable; that was wrong.

**Decided with the user 2026-08-13: ignore it for this build and document it.**
The durable fix is a buyer/supplier distinction on `moonlight_accounts`; a
hand-maintained denylist of job-board domains was considered and rejected as a
judgement call dressed as a rule, unvalidatable in the way the other two rules
are.

### Not included, and why

- **A "moving to Zoom" text rule.** That call is 27 turns of waiting, so R1
  should already catch it. Validation step 3 below checks this rather than
  assuming it; if the call clears 300 words we will know a third rule is needed
  instead of having guessed.
- **A minimum client-speech threshold.** A client who says only *"yeah, can you
  hear me?"* technically has turns, and there is still no conversation to coach.
  No number is invented here: the validation report prints client-side word
  count for all 32 calls, so a threshold can be added from the observed
  distribution — the same discipline that produced the 300-word floor.
- **EB detection**, and any re-gating of `analysis` rows beyond the four.

### Data flow

```text
moonlight_calls ──► fetcher ──► Avoma transcript (turns + speakers)
                       │
                       ▼
                    evaluate_input_gate(transcript, config)
                       │
        ┌──────────────┴──────────────┐
     accepted                      rejected
        │                              │
        ▼                              ▼
  call_storage row              call_storage row
  excluded_reason = NULL        excluded_reason = 'no_conversation' | 'no_client_speech'
        │                       excluded_detail = '271 words'
        │                                       | 'no non-rep speaker had turns; 2 rep speakers spoke'
        ▼                              │
  seed_missing_analysis_rows ◄─────────┘   (WHERE excluded_reason IS NULL — skipped)
        ▼
  analysis row ──► the four LLM steps
```

### Transcript contract changes

`app/domain/transcript.py`:

- `TranscriptTurn` gains `speaker_id: int` — required
- new `TranscriptSpeaker`: `id: int`, `name: str | None`, `email: str | None`,
  `is_rep: bool`
- `Transcript` gains `speakers: list[TranscriptSpeaker]` — required

`TranscriptTurn.speaker` (the display label) stays as it is, and
`render_for_prompt()` is unchanged — it still emits `[mm:ss] Speaker: text`. The
new fields are for identity, not rendering, so **the text the LLM sees is
byte-identical** and no prompt output shifts because of this change. That matters
given the pipeline reproduces only 43% of runs already; this must not add to it.

Required, not defaulted: a transcript whose participants cannot be identified is
rejected at the fetcher boundary, exactly as an unanchorable turn already raises
`TranscriptShapeError`. All stored transcripts therefore need re-fetching (see
Backfill).

`is_rep` is used by neither rule. It is carried anyway because the expensive part
is the one-time re-fetch, not the field, and a second contract change later would
mean re-fetching again. A deliberate exception to YAGNI, recorded so it is not
read as an oversight.

### Schema changes

Two nullable columns on `call_storage`, plus an Alembic migration:

- `excluded_reason: str | None` — the `ExclusionReason` value, NULL when accepted
- `excluded_detail: str | None` — human-readable evidence for the decision

`ExclusionReason` is an enum in `app/domain/types.py`, alongside the existing
enums. Stored as a string column, consistent with `analysis.call_score` and the
per-step status columns.

No new `analysis` columns and no new `analysis` status for new work — an excluded
call simply never gets a row.

### Config

New `input_gate:` block in `config.yaml`, loaded by
`app/core/input_gate_config.py`, mirroring `analyser_config.py` and
`scheduler_config.py`:

```yaml
input_gate:
  enabled: false
  min_words: 300
  require_client_speech: true
```

`enabled: false` initially — the gate reports before it enforces (see
Validation). `require_client_speech` is a separate switch so R1 can ship if R2's
measurement is unclean.

`min_words` is validated `>= 0` at config load, matching how
`max_concurrent_calls` and `circuit_breaker_consecutive_failures` are validated,
because a bad value should fail at startup rather than mid-run.

### Code changes

| file | change |
|---|---|
| `app/domain/input_gate.py` | new — the two rules, `GateVerdict` |
| `app/domain/types.py` | new `ExclusionReason` enum |
| `app/domain/transcript.py` | `TranscriptSpeaker`, `speakers`, `turn.speaker_id` |
| `app/core/input_gate_config.py` | new — loads the `input_gate:` block |
| `app/services/fetcher/transform.py` | populate speakers and `speaker_id`; raise `TranscriptShapeError` if Avoma returns no speakers |
| `app/services/fetcher/fetcher.py` | evaluate the gate; set the two columns; `FetchSummary.excluded` |
| `app/db/models.py` | the two `call_storage` columns |
| `app/services/batch/repository.py` | `seed_missing_analysis_rows` filters `excluded_reason IS NULL` |

### Backfill and the legacy rows

Two one-off scripts, neither wired into the scheduler.

**Transcript backfill.** Iterates `call_storage.avoma_recording_id` and re-fetches
each transcript from Avoma via `get_transcript_by_meeting_uuid`. Keyed on our own
table rather than `moonlight_calls` deliberately: 19 of the 51 stored calls no
longer appear in Koushik's table, but Avoma is unaffected by that churn, so they
are still retrievable and the eval baseline survives. Avoma-only — no LLM spend.

If Avoma no longer returns some transcript, that call cannot satisfy the new
contract. Its `call_storage` row is left untouched and the failure is reported;
the required-`speakers` decision is revisited only if this actually happens.

**Legacy row marker.** Sets `status = 'excluded'` on the four `analysis` rows for
calls the gate rejects, leaving the four output columns untouched.

## Downstream contract note

Koushik's side reads the `analysis` table. Excluded calls from now on never
produce a row there, so nothing new appears for new work. The single exception is
those four legacy rows, which will carry a `status` value their code has never
seen. **This needs telling them** — it is the only part of this design that
touches a contract we do not own.

## Validation

Measure before enforcing. Order matters.

Phase 0 already did this against Avoma directly, before any code existed
(`problems-and-fixes.md` 8.17) — which is what killed the account-domain rule.
The steps below re-confirm it through the real code path.

1. Run the transcript backfill; confirm all 51 transcripts return, now carrying
   speakers, `speaker_id` and `is_rep`.
2. `app/services/eval/input_gate_report.py` runs the gate in **report-only** mode
   over the 32 in-scope calls, printing per call: total word count, rep and
   non-rep word counts, unlinked-speaker words, verdict, reason, and whether R2
   abstained.
3. **Success criterion, fixed now: the gate rejects exactly these four and
   nothing else** — the 30-word call, the platform-switch artifact, the client
   no-show, and `b026da73` at 183 words. That last one is not in the labelled bad
   set but is one of the three sub-300-word calls Part 5 of
   `problems-and-fixes.md` already identifies as having no conversation in them,
   so it is a correct rejection. The HelloWork call is expected to pass — see
   above.
4. R1 and R2 are judged separately, so one can ship without the other.
5. Only then does `input_gate.enabled` flip to `true`.
6. Run the legacy row marker.

Step 2's non-rep word count is also the evidence for whether a
minimum-client-speech threshold is needed.

## Testing

**Unit** (`tests/domain/test_input_gate.py`) — below, at, and above the word
floor; a non-rep speaker with turns accepted; a non-rep **invitee with zero
turns** rejected (the attendance-vs-speech distinction); all-rep speech rejected
(the no-show shape); speech from an unlinked `speaker_id` causing R2 to abstain
rather than reject; a non-rep who spoke *plus* unlinked speech still accepted;
`require_client_speech: false` disabling R2 while R1 still applies;
`enabled: false` accepting unconditionally.

**Fetcher** (`tests/services/fetcher/`) — a rejected call is still written to
`call_storage` with reason and detail; `FetchSummary.excluded` counts it;
`TranscriptShapeError` when Avoma returns no speakers; `speaker_id` carried
through.

**Repository** (`tests/services/batch/`) — `seed_missing_analysis_rows` creates no
row for an excluded call, and still creates one for an accepted call alongside it.

**Integration** (`tests/integration/`) — round-trip against real Neon: an excluded
`call_storage` row persists both columns and produces no `analysis` row. Cleans up
its own rows, per the existing fixture convention. Never writes to
`moonlight_calls` / `moonlight_accounts`.

## Risks

- **`is_rep` is Avoma's flag, not ours.** It agrees with `joveo.com` membership on
  all 51 calls today, but if a client user were ever added to Joveo's Avoma
  workspace they would read as a rep and their call could be excluded. The reason
  and detail are stored, so such a case is findable.
- **The word floor is corpus-specific.** 300 is right for the 51 calls measured;
  a genuinely short but complete call below it would be wrongly excluded. Again
  visible in `excluded_reason` rather than silent.
- **Supplier and partner calls still get analysed** — the HelloWork case above.
  Accepted for this build.
- **~~Backfill depends on Avoma's retention~~** — resolved in phase 0: all 51
  transcripts returned, including the 19 no longer in `moonlight_calls`.
- **~~R2 false positives from domain matching~~** — resolved by dropping domain
  matching entirely. Zero false positives measured with `is_rep` plus the
  unlinked-speaker abstention.
