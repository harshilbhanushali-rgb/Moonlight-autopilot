# Implementation plan — input gate

**Design**: `docs/superpowers/specs/2026-08-13-input-gate-design.md` (approved)
**Date**: 2026-08-13

Seven phases. Phase 0 is a read-only probe that can invalidate the design before
any code is written, so it runs first and its result is recorded here. Phases 1–3
are TDD. Phase 5 is the measurement gate — the gate does not enforce until it
passes.

Do not run an eval sweep or a batch while the backfill (phase 4) is running: the
gateway's ceiling is on *total* concurrent requests and a 429 surfaces as an
`APIStatusError`, which the circuit breaker reads as an outage.

---

## Phase 0 — Avoma probe (read-only, throwaway) — **DONE 2026-08-13**

**Result: it killed R2's original mechanism.** Full writeup in
`problems-and-fixes.md` 8.17. Summary:

| question | answer |
|---|---|
| transcripts still return for the 19 dropped calls? | **yes, 51/51** — backfill is safe, eval baseline recoverable |
| `speakers[].email` populated? | **yes, 51/51 calls, every speaker** |
| every `turns[].speaker_id` resolves to a `speakers` entry? | **no — 3 of 51 calls fail**, up to 3,141 of 6,319 words unattributable |

- **Account-domain matching rejects 27/51 calls, 24 of them good.** Causes: `www.`
  and `about.` prefixes on the account domain, the subdomain sitting on the
  account side, and subsidiaries mailing from the parent. Not salvageable —
  normalising both directions still leaves 8 false positives.
- **Replaced by Avoma's `is_rep`**, which agrees with `joveo.com` membership on
  every speaker of all 51 calls. With R2 abstaining when any speech comes from an
  unlinked speaker_id, it has **zero false positives**.
- **The HelloWork call cannot be caught** and is accepted as out of scope.
- The gate rejects **4 of 51**: the 30-word call, the platform-switch artifact,
  the no-show, and `b026da73` at 183 words.

Below is the original phase 0 brief, kept for the record.

---

The whole design rests on three unverified assumptions about Avoma's payload.
Answer them before writing anything that depends on them.

Write a scratchpad script (**not committed**) that, for every
`call_storage.avoma_recording_id`, calls
`AvomaClient.get_transcript_by_meeting_uuid` and reports:

1. **Does the transcript still return?** Especially for the 19 calls no longer in
   `moonlight_calls`. If some do not, the required-`speakers` decision reopens.
2. **Do `speakers[].email` values exist, and how often are they populated?** R2 is
   unbuildable if Avoma rarely resolves emails.
3. **Does every `turns[].speaker_id` resolve to an entry in `speakers`?** R2's
   presence check depends on this linkage.

Also print, per call: the account's `moonlight_accounts.domain`, the set of
speaker domains, and which of those have turns. This is a preview of phase 5's
report using pre-contract data and will show immediately whether the four known
bad calls separate from the other 28.

**Stop and reconsider if:** emails are populated on under ~80% of calls, or
`speaker_id` linkage is unreliable. Either finding means R2 needs a different
mechanism and phases 1–6 change shape.

**Done when:** the three questions have numbers, recorded in
`problems-and-fixes.md`.

---

## Phase 1 — Transcript contract carries identity (TDD)

**Files:** `app/domain/transcript.py`, `app/services/fetcher/transform.py`,
`tests/domain/test_transcript.py`, `tests/services/fetcher/test_transform.py`

Tests first:

- a `Transcript` without `speakers` fails validation
- a `TranscriptTurn` without `speaker_id` fails validation
- `render_for_prompt()` output is **unchanged** — assert the exact expected string
  for a fixture that has speakers and ids. This is the regression guard for "no
  prompt text moves"; write it before touching the model.
- `transcript_to_storage_shape` carries `id` / `name` / `email` / `is_rep` per
  speaker and `speaker_id` per turn
- `transcript_to_storage_shape` raises `TranscriptShapeError` when Avoma returns
  an empty `speakers` list, with a message naming the meeting uuid (mirroring the
  existing no-timestamps error)

Then implement: `TranscriptSpeaker`, `Transcript.speakers`,
`TranscriptTurn.speaker_id`. Keep `TranscriptTurn.speaker` and
`render_for_prompt()` exactly as they are.

**Expected churn:** 17 files construct `Transcript` / `TranscriptTurn` or a
`{"turns": ...}` dict. Every fixture needs `speaker_id` and a `speakers` list.
Heaviest: `tests/services/fetcher/test_transform.py`, `tests/domain/test_transcript.py`,
`tests/domain/test_gap_verification.py`, `tests/domain/test_citation.py`. Update
fixtures mechanically — do not weaken the model to spare them.

**Note:** from this commit until phase 4 completes, the analyser and the eval
harnesses cannot read stored transcripts (`Transcript.model_validate` rejects
them). That is intended and temporary. Do not start a batch run in between.

**Done when:** `uv run pytest` is green.

---

## Phase 2 — The gate itself (TDD)

**Files:** `app/domain/input_gate.py`, `app/domain/types.py`,
`app/core/input_gate_config.py`, `config.yaml`,
`tests/domain/test_input_gate.py`, `tests/core/test_input_gate_config.py`

`ExclusionReason` enum in `types.py`: `NO_CONVERSATION`, `NO_CLIENT_SPEECH`.

```python
@dataclass(frozen=True)
class GateVerdict:
    accepted: bool
    reason: ExclusionReason | None
    detail: str | None            # human-readable evidence, stored as excluded_detail
    client_speech_skipped: bool   # R2 abstained — for the phase 5 report
```

`evaluate_input_gate(transcript, config) -> GateVerdict`. Pure, no I/O, and needs
no account data — both rules read only the transcript.

Tests — one per row, all against hand-built transcripts:

| case | expected |
|---|---|
| 299 words | rejected `no_conversation` |
| 300 words | accepted (boundary is inclusive-pass) |
| a non-rep speaker has ≥1 attributed turn | accepted |
| a non-rep speaker is in `speakers` but has **zero** turns | rejected `no_client_speech` — attendance is not speech |
| every speaker with turns has `is_rep=True` | rejected `no_client_speech` (the no-show shape) |
| all speech is from `speaker_id`s absent from `speakers` | accepted, `client_speech_skipped=True` |
| a non-rep spoke **and** some speech is unlinked | accepted, not skipped — R2 has its answer |
| only reps linked, plus unlinked speech | accepted, `client_speech_skipped=True` (the `e8bfc3fb` shape) |
| `require_client_speech=False`, no client speech | accepted — R2 off, R1 still applies |
| `enabled=False`, 10 words | accepted unconditionally |

No domain-matching tests: phase 0 measured that mechanism at 24 false positives
and it is not in the design. Do not add it back without re-measuring.

R1 is evaluated before R2, so a call that fails both reports `no_conversation` —
the more specific and more actionable reason.

`config.yaml`:

```yaml
input_gate:
  enabled: false
  min_words: 300
  require_client_speech: true
```

`min_words` validated `>= 0` at load, matching how `max_concurrent_calls` and
`circuit_breaker_consecutive_failures` are validated — a bad value must fail at
startup, not mid-run.

**Done when:** `uv run pytest tests/domain/test_input_gate.py` green, full suite green.

---

## Phase 3 — Persist the verdict and skip excluded calls (TDD)

**Files:** `app/db/models.py`, a new Alembic migration,
`app/services/fetcher/fetcher.py`, `app/services/batch/repository.py`,
`tests/services/fetcher/test_fetcher.py`, `tests/services/batch/test_repository.py`

1. Two nullable `String` columns on `CallStorage`: `excluded_reason`,
   `excluded_detail`. Then
   `uv run alembic revision --autogenerate -m "call_storage exclusion columns"`
   and check the generated migration touches **only** `call_storage` — never
   `moonlight_calls` / `moonlight_accounts`.
2. `fetch_and_store_call` evaluates the gate and writes both columns. The call is
   stored either way. `FetchSummary` gains `excluded: int`; log one line per
   exclusion with the reason and detail.
3. `seed_missing_analysis_rows` gains `AND cs.excluded_reason IS NULL`.

Tests:

- a rejected call is still written to `call_storage`, with reason and detail set
- an accepted call has both columns `NULL`
- `FetchSummary.excluded` counts rejections and `fetched` still counts the row
- `seed_missing_analysis_rows` creates no `analysis` row for an excluded call, and
  **does** create one for an accepted call in the same batch (guards a broken
  `WHERE`)
- integration: round-trip against real Neon, both columns persist, no `analysis`
  row appears. Clean up own rows per the existing fixture convention.

**Done when:** `uv run pytest` and `uv run pytest -m integration` green, no schema
drift.

---

## Phase 4 — Backfill stored transcripts

**File:** `scripts/backfill.py`, run as
`uv run python -m scripts.backfill [--limit N] [--dry-run]`

Iterates `call_storage` rows and re-fetches each transcript by its stored
`avoma_recording_id`. Keyed on our table, **not** `moonlight_calls` — that is what
lets the 19 orphaned calls be recovered, since Avoma is unaffected by Koushik's
table churn.

Per row: fetch, transform to the new shape, `UPDATE call_storage SET transcript`,
and re-evaluate the gate so `excluded_reason` / `excluded_detail` are filled in for
history too. Commit per row so a mid-run failure leaves the rows already done.

Handle three outcomes explicitly and count each: updated, Avoma returned nothing,
`TranscriptShapeError`. A row that cannot be recovered is **left untouched** —
never overwritten with a partial transcript — and named in the summary.

Run it. Then confirm no stored transcript fails validation:

```bash
uv run python -c "from app.db.session import ...; validate every call_storage.transcript"
```

**Done when:** every `call_storage` row validates against the new contract, or the
exceptions are listed and understood.

---

## Phase 5 — Measure before enforcing

**File:** `eval/input_gate_report.py`, run as
`uv run python -m eval.input_gate_report [--out report.json]`

Report-only. Runs the gate over stored transcripts with `enabled` forced on,
**writing nothing**. One row per call: `avoma_recording_id`, title, total words,
rep words, non-rep words, unlinked-speaker words, verdict, reason,
`client_speech_skipped`.

Restrict to the 32 in-scope calls — those present in `moonlight_calls`. Print the
19 orphans separately so the scope rule stays visible rather than silently
applied. **Needs Tailscale up**, since the scope split reads Koushik's RDS.

**Success criterion, fixed now: exactly these four are rejected, nothing else** —
`35f28528` (30 words), `7cf8dcfb` (271 words, platform-switch artifact),
`4ac4eea2` (client no-show), and `b026da73` (183 words). The last is not in
`call_type_labels.py::UNCLASSIFIABLE` but is one of the three sub-300-word calls
Part 5 of `problems-and-fixes.md` names as having no conversation in them, so it
is a correct rejection. **`0bbe93f1` (HelloWork) is expected to pass** — it is
undetectable and out of scope, not a miss.

This is a re-confirmation, not a discovery: phase 0 already measured these exact
four against Avoma directly. A different answer here means the code path diverges
from the probe, not that the corpus changed.

Judge the rules separately:

- **R1 clean, R2 clean** → proceed to phase 6 with both on.
- **R2 rejects anything else** → ship R1 only, `require_client_speech: false`, and
  record the false positive here. Phase 0 measured zero, so a new one means the
  code disagrees with the probe — find out which is wrong before changing either.
  The likely candidate is the unlinked-speaker abstention, which is the whole
  reason R2 has no false positives.
- **R1 misses `7cf8dcfb`** → it measured 271 words in phase 0, so a miss means the
  word count is computed differently in code than in the probe. Do not raise
  `min_words` to cover it; that is how a threshold starts eating legitimate calls.

Also read the non-rep word column: if there is a cluster of calls where the client
barely spoke, that is the evidence for a minimum-client-speech threshold. Record
the distribution; do not add the threshold in this build.

**Done when:** the report is committed under `docs/eval/` and the criterion is
either met or the fallback above is chosen explicitly.

---

## Phase 6 — Enable, mark the legacy rows, document

1. Flip `input_gate.enabled: true` in `config.yaml` (and
   `require_client_speech` per phase 5's outcome).
2. One-off script: set `status = 'excluded'` on the `analysis` rows of whichever
   calls the gate rejected (four, per phase 5). Leave `call_type`, `call_score`,
   `risk_gap_analysis`, `card_type` untouched — they are the A/B baseline. Print
   before/after per row; support `--dry-run`. Note `0bbe93f1` is **not** among them
   and keeps its Medium/Risk row.
3. `CLAUDE.md`: new "Input gate" section covering the two rules, the fail-open
   cases, why rejected calls are stored rather than dropped, and that this does not
   breach the LLM-only gap boundary. Update the "input hygiene: F" line and resolve
   the input-gate open decision. Cross-reference the thin-content open decision,
   which the 300-word floor partly answers.
4. `problems-and-fixes.md`: phase 5's report (phase 0's numbers are already in
   8.17).
5. New open decision in `CLAUDE.md`: **supplier/partner calls are analysed as
   sales calls and cannot be detected from the transcript.** The durable fix is a
   buyer/supplier distinction on `moonlight_accounts` — Koushik's schema. Record
   that a hand-maintained job-board denylist was considered and rejected.

**Done when:** full suite green, `enabled: true`, and a fresh fetcher run over a
handful of calls shows the expected exclusions in the summary.

---

## Phase 7 — Tell Koushik's side

Excluded calls produce no `analysis` row, so nothing new appears for new work. The
exception is the four legacy rows now carrying `status = 'excluded'`, a value their
code has never seen. **This is the only part of the change that touches a contract
we do not own** — send it rather than let them find it.

Worth combining with the two other outstanding messages: the rubric review doc
(`docs/gap-rubric-review-2026-08-12.md`, still unsent) and the question about
`moonlight_calls` dropping from 296 rows to 197 in a day.

---

## Out of scope

- the ambiguous ~1000–2000 word band
- a minimum-client-speech threshold (measured in phase 5, not built)
- **supplier/partner calls** — measured as undetectable from the transcript
  (phase 0); needs account-level classification we do not own
- EB detection
- re-gating any `analysis` rows beyond the four the gate rejects
- surfacing exclusions in Moonlight's UI — Koushik's side owns that
