# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Read `Prompt.md` in full before writing any code** — it is the authoritative spec. This file distills its hard constraints plus the real implementation state, so read both.

## What this project is

"Moonlight Autopilot" is the AI/backend layer for Joveo's Moonlight call-coaching platform. Moonlight today is manual: human auditors review sales call recordings (via Avoma) and leave transcript-anchored feedback cards. This project adds an AI layer that reads **every** New Business (NB) sales call automatically and generates the same kind of feedback card, at a scale manual review can't match. AI-generated cards still go through human moderator review — this does not replace that step.

There are two distinct features in scope:

1. **Batch pipeline** (cron-driven): fetches every new NB call's transcript, analyzes it, and writes a row to the Analysis Table.
2. **Manual Card Auto-Fill**: on-demand, triggered when a human auditor saves a manual card with a blank `Type` or `Gap` field — the AI fills in the blank field(s) from the comment text. Shares the Card Type (Coaching/Risk) classification logic with the batch pipeline, but is otherwise a separate on-demand code path, not part of the 24h cron.

There's no separate cron infra (no k8s CronJob, no GitHub Actions schedule) — the "cron" is an in-process `BackgroundScheduler` living inside the same always-on FastAPI server that serves Manual Card Auto-Fill. See "Scheduling" below.

## Hard scope boundaries (do not relitigate)

- **New Business (NB) calls only.** Existing Business (EB) is out of scope for this entire build. On NB/EB ambiguity, assume NB. Confirmed with the user: `moonlight_calls` (the Client Table) is already NB-only end to end — no filtering needed on our side.
- No frontend/UI work — Moonlight's UI is owned by a separate team (Koushik's). This build's responsibility ends at writing correct, complete rows into the **Analysis Table**; nothing here pushes data into Moonlight's PM sheet/UI directly.
- No vector-based selling/account-relationship scoring — that's Phase 2.
- No account-level risk rollup (aggregating risk across an account's calls) — currently unscoped. If work seems to require this, flag it explicitly rather than building a workaround or silently skipping it.
- Gap detection is **purely LLM-based reasoning** — no deterministic/rule-based checks (no hand-coded talk-time-ratio calculators, keyword matching, etc.). No code path may independently re-derive or corroborate a gap call from the transcript text. **Confirmed with the user 2026-08-12:** an *LLM* second pass that only ever drops gaps whose evidence contradicts their claim is within this boundary — it is still LLM reasoning and it never invents a gap. See "Gap entailment verification" below. Deterministic corroboration remains banned.
- The scoring prompt and gap-theme rubric are owned/provided by the business team — treated as pluggable, versioned files under `app/prompts/`. Real content so far: `scoring/<call_type>/v1.txt` and `gap_rubric/<call_type>/*-descriptiononly.yaml` (both call-type-scoped, mirroring `gap_rubric/`'s layout, since the business rubric differs per Call Type); `card_type/v1.txt`; `call_type/v1.txt`. Still **placeholder content** pending the real prompts from Anantu's team: `gap_rubric/*-fewshot.yaml` (the annotated-example counterpart to the descriptiononly rubrics) and `gap_fill/v1.txt`.
  - `scoring/<call_type>/v1.txt` reasons over a rich 10-category rubric internally but must respond with only `{"call_score": "High"|"Medium"|"Low"}` — the category breakdown and qualitative coaching text the business team's prompt also produces are intentionally not parsed or persisted (`analysis.call_score` is a single `String` column); revisit if that richer output needs to be stored.
  - `card_type/v1.txt` classifies Risk vs. Coaching by *where the problem originates* — a deal/client-side fact (Risk) vs. rep execution/skill (Coaching) — not by severity. Risk is meant to be the minority case; if a mix of both is present, Risk wins.
  - `call_type/v1.txt` was built from 6 real labeled transcripts (one per Call Type) in `Call_examples.md` (repo root) — keep that file if the prompt needs revisiting. Classifies by dominant activity/purpose, not keywords, since technical jargon overlaps between Kick-off and Technical Integration, and product-walkthrough content overlaps between Demo and Follow-up Demo.
- `avoma_type_label` on `moonlight_calls` is FYI only — confirmed with the user not to filter on it, despite values like `"Exclude from Review"` looking tempting to.

## Project structure

Layered "SDE(AI)" style. Business logic has zero I/O; everything else is an adapter around it.

```text
app/
  main.py                # FastAPI app entrypoint
  api/
    routes/autofill.py    # POST /cards/{card_id}/autofill
    deps.py                # all FastAPI Depends() providers
  core/                    # cross-cutting: config, logging — NOT business logic
    config.py               # Settings (env-driven)
    logging.py
    analyser_config.py       # loads config.yaml's `analyser:` section
    scheduler_config.py      # loads config.yaml's `scheduler:` section
  domain/                  # pure business logic, zero I/O, fully unit-tested
    call_type.py / scoring.py / gap_analysis.py / card_type.py / gap_fill.py
    classification.py       # shared single-enum-field step runner
    response_models.py      # Pydantic response contract per step (the schema sent to the gateway)
    types.py / errors.py
  services/                # I/O adapters around app/domain
    prompt_versions.py       # content-hash -> prompt_versions.id upsert, shared by batch + autofill
    batch/                   # AI Analyser: repository.py, processor.py, orchestrator.py, run.py
    autofill/                # worker.py (background task), repository.py
    fetcher/                 # Call Fetcher: transform.py, repository.py, fetcher.py, run.py
    eval/                     # few-shot vs description-only comparison harness
    scheduler/                # in-process cron: ledger.py, pipeline.py, scheduler.py
  schemas/                 # Pydantic request/response models
  db/
    models.py               # OUR tables: call_storage, analysis, prompt_versions, autofill_requests, scheduled_run
    session.py               # our Neon-backed engine/session
    client_base.py / client_models.py / client_session.py  # READ-ONLY mapping of Koushik's DB — separate Base/engine, never migrated by us
    migrations/              # Alembic — only ever targets OUR tables
  llm/
    client.py                # OpenAICompatibleLLMClient (openai SDK, ASYNC) + StubLLMClient
                             # schema-constrained only — see "LLM output is schema-constrained"
    gateway_config.py / factory.py
  avoma/
    client.py                # AvomaClient — GET /v1/transcriptions/?meeting_uuid=...
  prompts/                 # versioned prompt/rubric files (business-team-owned content)
tests/                    # mirrors app/ 1:1
  integration/             # hits real Neon + real client RDS DB — see Testing below
```

## The transcript contract carries timestamps — don't drop them again

`call_storage.transcript` is `app/domain/transcript.py::Transcript` (a Pydantic model, validated on both write and read), **not** a loose dict. Each turn carries `start_s` (seconds from call start, from the earliest of Avoma's per-word `timestamps`), and `render_for_prompt()` emits `[mm:ss] Speaker: text`.

`start_s` is **required**, deliberately. The original shape carried only `speaker`/`text` and both sides read it with `.get(key, default)`, so when timestamps went missing nothing failed — the analyser rendered timestamp-free text and the gap step, still asked for `mm:ss` dialogue evidence, **invented** timestamps that looked plausible and pointed at the wrong part of the call (one measured case: cited `00:43`, actual `01:09`). A turn that can't be anchored is now rejected at the fetcher boundary (`TranscriptShapeError`), counted in `FetchSummary.skipped_malformed_transcript`, and logged — one bad call is skipped rather than aborting the run, since the scheduler skips the analyser entirely if the fetcher raises.

Consequences to remember:

- Transcripts stored before this change fail validation and **must be re-fetched** — timestamps cannot be backfilled from `call_storage`.
- **Some calls carry too little conversation to assess, and nothing currently detects it.** Confirmed with the user: these transcripts are **complete** — Avoma is not truncating anything. The meetings are simply left open with long stretches of dead air, so `duration_s` measures wall-clock, not conversation. Do not treat low speech-to-duration ratio as a data-integrity problem, and do not re-fetch or escalate to Avoma over it (their metadata correctly reports `state=completed`, `transcript_ready=True`, `disable_reason=None`).
  - **Gate on word count, not on coverage ratio.** Coverage (`turns[-1].start_s / duration_s`) misses the other thin-content shape: a genuinely short, fully-transcribed meeting. Measured over 26 calls, "Feedback on Joveo's new messaging" had 96% coverage yet only 271 words across 27 turns — invisible to a coverage gate. Word count catches both shapes.
  - Measured distribution: the 5 thinnest calls (30, 183, 271, 1120, 1569 words) produced **1 gap between them and scored `Low` across the board**. Above ~2000 words, scores spread properly across Low/Medium/High with 0-3 gaps. The floor worth excluding is somewhere under ~300 words (3 calls: a 1156s meeting with a single 30-word turn, a 760s meeting with 183 words, and the 385s 271-word one) — there is no conversation there to judge. Between ~1000-2000 words a `Low` may well be legitimate; that band is a judgement call, not obviously an artifact. See Open decisions.
- Measured over **46 calls: 67% of gaps (58/86) are moment-anchored** `dialogue` evidence, up from ~1-in-8 before timestamps were carried through. Anchoring accuracy is good — **median error 1 second**, 15/18 verifiable citations within 5s, 17/18 within 60s. Two earlier notes here claimed accuracy was only approximate and that anchoring hadn't improved; both were artifacts of a 6-call sample and are wrong.
  - **Anchoring rate is a property of the rubric, not of the model or the transcript** — so don't read it as a quality metric. Measured per call type over 46 calls: Demo 86%, Discovery 76%, Follow-up Demo 75%, Pricing/Negotiation 69%, Kick-off 45%, Technical Integration 40%. Word count barely matters (Pearson r = 0.15 between transcript length and per-call anchoring rate; 65%/66%/75% across word-count bands).
  - The cause is **theme shape**. Kick-off and Technical Integration rubrics are dominated by *absence*-shaped themes — "Success Metrics Not Agreed at Start" (0/4 anchored), "Escalation Path Not Established" (1/6), "No Committed Timeline" (3/7). You cannot quote a moment where something *didn't happen*, so `explanation` is the **correct** answer there; forcing a timestamp would be the fabrication this whole section exists to prevent. Demo/Discovery themes are *presence*-shaped ("Slide Reading & Poor Storytelling") and quote naturally.
  - A between-batch swing (56% on the first 26 vs 78% on the next 20) was initially written up here as a transcript-richness effect. That was wrong — it was call-type mix (9 of the low-anchoring types in the first batch vs 2 in the second). Stratify by call type before drawing conclusions from an aggregate anchoring number.
- Evidence fidelity checked and **no fabrication found**: of 41 gaps, 5 `dialogue` quotes failed a naive per-turn fuzzy match, but 4 of those 5 are present in the transcript verbatim (the matcher failed because the quote starts mid-turn or elides with `...`). The 5th is a composite that stitches two speakers' lines together with added `Speaker: "…"` labels. Evidence is quoted, not invented — don't rebuild this check on per-turn similarity alone.
- Gap `confidence` is barely used: 39 of 41 gaps came back `high`, 2 `medium`, none `low`. Treat it as near-constant, not as a filterable signal, until the rubric is tuned.

## LLM output is schema-constrained — don't add a permissive parser

Every LLM call goes through `OpenAICompatibleLLMClient.complete_structured(response_model=...)`, which passes a Pydantic model as the gateway's `response_format`. There is deliberately **no** unconstrained completion method and **no** tolerant JSON parser (`app/domain/parsing.py` was deleted, not refactored). Reasons, all verified against the live gateway:

- Unconstrained, `gemini-2.5-flash` **intermittently** wraps its JSON in ` ```json ` fences. That silently failed all 4 steps on some calls and none on others, which reads like flaky LLM behaviour rather than a bug. Fixing this by stripping fences was explicitly rejected: it treats the symptom and leaves the format a coin flip.
- `temperature: 0` alone does **not** fix the fencing — format and reproducibility are separate problems.
- `response_format: json_object` guarantees parseable JSON but **not the right fields** — it returned `{"theme": ..., "gap": ...}` instead of the gap contract. JSON mode is not sufficient; the schema is.
- Response models live in `app/domain/response_models.py` and reuse the enums in `app/domain/types.py`, so allowed values are declared exactly once and reach the gateway as an enum constraint. The gateway resolves Pydantic's `$defs`/`$ref` and returns real enum members.
- `temperature` is `config.yaml`'s `llm_gateway.temperature` (0). Treat it as best-effort reproducibility, **not** a contract — output *structure* is guaranteed by the schema, not by temperature. **Measured, so don't assume better than this:** at temperature 0 an identical re-run reproduces all four analyser outputs on only 43% of calls. See Open decisions.
- **The gateway serves a response cache, keyed on the request messages.** An identical repeat request returns in **~3s instead of ~55s**, replaying the stored answer *and* its original `usage` token counts. Undocumented; found by accident. Two things follow: re-running identical analyses is near-free, and **any prompt comparison must vary the input (a nonce) or it silently compares a fresh run against a replay** — a same-setting control that hits cache scores a meaningless 100%. The `metadata` in `extra_body` carries a fresh uuid per call and does *not* bust the cache, so only the messages matter.
- **Transport retries cover `APIConnectionError`, not just `APITimeoutError`** (the latter subclasses the former). A bare "Connection error." with no timeout was observed hitting several concurrent requests and then succeeding immediately on repeat, so it is transient; left un-retried it failed a step on the first blip and, because the circuit breaker reads connection failures as "gateway is down", enough simultaneous blips could abandon a whole nightly batch. `APIStatusError` (HTTP 5xx) is deliberately still **not** retried in-client — the gateway answered, so it belongs on the visible per-step failure path.
- **Gemini's thinking level must be sent as OpenAI's `reasoning_effort`** (`config.yaml`'s `llm_gateway.reasoning_effort`, currently `high` on `gemini-3.5-flash`). The native `thinking_config` / `thinking_level` shapes from Google's own SDK docs are **silently dropped** by this gateway — verified by comparing `usage.completion_tokens_details.reasoning_tokens`, which was identical to sending no config at all. A regression here looks wired up while doing nothing, so `tests/llm/test_openai_gateway_client.py` asserts on the wire format. Valid values are `none|minimal|low|medium|high`, validated at config load because the gateway rejects a bad one as an opaque HTTP 500 mid-batch; on this gateway only `medium` and `high` report any reasoning tokens.

Two things the schema can't do, so they stay in code:

- The gap timestamp invariant (`dialogue` requires a timestamp, `explanation` must not have one) is a *conditional* constraint no schema subset the gateway accepts can express — `app/domain/gap_analysis.py` enforces it.
- Whether the cited quote is *in the transcript at all*, and whether its timestamp points at the moment it was said — `app/domain/citation.py`, see below.
- Truncation (token limit), content-filter stops, and refusals raise `StructuredOutputError` from `app/llm/client.py`, which the domain translates to `LLMOutputError` so the step fails visibly and follows the normal retry/dead-letter path. `app/llm` must never import `app/domain` — domain already imports llm, so that direction would be circular.

## Citation validation — checks the citation, never the judgement

`app/domain/citation.py::verify_citations` runs inside `analyse_gaps` on every
`dialogue` gap. It answers two questions a moderator would otherwise have to
answer by hand: are these words in the transcript, and is the timestamp the
moment they were said. It does **not** decide whether the gap is real — that
stays purely LLM reasoning, so the "no code path may re-derive a gap call"
boundary is intact.

- **`analyse_gaps` and `advance_analysis` take a `Transcript`, not rendered
  text**, and render internally. That is deliberate: a caller holding only a
  string cannot verify citations, so the typed input is what makes the check
  unbypassable. `repository.load_transcript` returns the model;
  `render_transcript_text` remains for the three steps that only need text.
- **Matching is on word runs, not exact substrings.** Measured over 46 real
  calls, 12 of 58 citations failed a verbatim match — and *not one was
  fabricated*. Every case was real speech carrying `Speaker:` labels the model
  invented, stitched across turns, or elided with `...`. Rejecting those would
  have failed 9 of 46 gap steps for pure formatting. So a quote is accepted
  when ≥60% of its words appear in runs of ≥4 (the whole quote, for quotes
  shorter than that). Genuine fabrication shares no long run and is rejected;
  a "quote" that is mostly the model's own prose fails on coverage.
- **The timestamp is overwritten, not merely checked** — anchored to the turn
  the earliest matched run starts in. Replaying the 46 calls corrects 13 of 58
  and rejects 0. Do not "fix" a wrong timestamp by failing the step; the quote
  is real, only the anchor was wrong.
- A quote that genuinely isn't there raises `LLMOutputError`, so it takes the
  normal per-step retry/dead-letter path rather than reaching a moderator as an
  uncheckable card.

## Gap entailment verification — the second pass that drops unsupported gaps

`app/domain/gap_verification.py::verify_gap_claims` runs inside `analyse_gaps`,
after citation validation, and removes gaps whose evidence does not bear out
their claim. It only ever *removes* gaps — it never invents one, and no
rule-based code re-derives a gap call, so the "gap detection is purely LLM
reasoning" boundary holds. Confirmed with the user before building.

Why it exists: measured over 46 real calls, only about a third of gaps survived
manual review, and the dominant failure was **evidence that disproves its own
claim** — "No Pre-Call Research" cited to *"I saw you're using Symphony on your
career site"*, "No Committed Timeline" cited to *"I'll send the plan within two
days"*, "Demo Not Customised" cited to the presenter's own honesty disclaimer.
Citation validation cannot see any of this: those quotes are all real speech.

- **Two prompts, because there are two questions.** `dialogue` gaps get the
  quote plus `DEFAULT_WINDOW_TURNS` either side and are asked "does this
  support the claim?". `explanation` gaps get the **entire transcript** and are
  asked "is there a counter-example anywhere?".
- **The narrow window for `dialogue` is load-bearing, not a cost saving.**
  Several real errors were only visible because a colleague resolved the issue
  in the very next turn (id 260: answered 7 seconds later). Handing the whole
  transcript to a dialogue check reintroduces exactly the haystack problem that
  caused these errors.
- **The full transcript for `explanation` is equally load-bearing.** Those gaps
  are almost always absence claims, and an absence cannot be disproved from an
  excerpt. This is where the worst measured error lived (id 274: "no case
  studies" on a call that named Uber and Banfield/Mars with figures). 28 of 86
  gaps are `explanation`, and the saturated boilerplate themes are 83–100%
  explanation-typed, so skipping them would skip the worst bucket.
- **Order matters:** citation validation must run first, because it re-anchors
  each timestamp to the turn its quote starts in and verification locates its
  window by that timestamp. Verifying first would window on the model's own
  wrong anchor.
- **Verdicts are matched by index, not list position**, and the returned index
  set must exactly equal the batch's. A missing verdict would let an unjudged
  gap through as if verified; an unexpected one means the response isn't
  describing the batch we sent. Either raises `LLMOutputError`.
- `analyser.verification_batch_size` (default 5) gaps per request. Observed max
  on one call is 5 (mean 1.9), so this is effectively one request per kind of
  gap per call. Verification runs inside the gap step, so its failures are
  `gap_status` failures and follow the normal retry/dead-letter path — no fifth
  step, no new status columns, no circuit-breaker changes.
- **Both verification prompts get their own provenance columns**
  (`gap_verification_dialogue_version_id`, `..._explanation_version_id`,
  migration `7f1c9a2b4de3`), carried out of the domain on
  `ClassificationResult.extra_prompt_hashes`. They decide which gaps survive,
  so an edit to either changes `risk_gap_analysis` as much as an edit to the
  rubric. Each is NULL when that verifier had no gap of its kind to judge.
- `verification_prompts=None` skips the whole pass. That exists **only** for
  `app/services/eval/harness.py`, which compares rubric wording and must see
  the rubric's raw output — filtering it would measure the verifier instead.
- **Measured, and prompt tuning is exhausted.** Replayed over the 86 stored
  gaps and scored against `app/services/eval/gap_audit_labels.py`: **90% of
  good gaps retained, 67% of bad gaps removed, 98% reproducible run-to-run**
  (verification is a 3-way classification over fixed input, so unlike the
  generator's 43% its numbers can be trusted). Two attempts to improve it by
  prompt means have both failed and **should not be retried**: hardening the
  wording moved 2 of 86 gaps in opposite directions (`problems-and-fixes.md`
  8.7), and reframing the question from "does this quote support this claim?"
  to the neutral "does this call exhibit theme X?" fixed 0 labelled gaps and
  broke 8 (8.13 — the leading question turns out to be load-bearing, because it
  aims the model at the span where the contradiction lives). The remaining
  leverage is in the saturated rubric themes, not the checker.
- **The neutral arm is kept for future comparisons** —
  `app/services/eval/neutral_framing.py` and `verification_replay --framing
  neutral`. Its prompts are Python constants, *not* files under
  `app/prompts/gap_verification/`, because `PromptRegistry.latest()` takes the
  highest version label and a `v2-*.txt` dropped in there would silently
  repoint the production verifier at an unvalidated prompt.

## Card Type sees the gaps

`CardTypeContext.gaps` carries the gaps found on this pass. Before this,
card_type saw only transcript/metadata/score and so contradicted its own row —
one real example scored `Risk` while all three of its gaps were rep-coaching
observations, another returned `Risk` with no gaps at all. `None` (step failed
or was skipped) and `[]` (ran, flagged nothing) are rendered differently and
must stay distinct: rendering a failed step as "no gaps" would let a gateway
error read as a clean call.

## The async boundary — async LLM, sync DB, and the line between them

The LLM path is `async` end to end: `OpenAICompatibleLLMClient` wraps `AsyncOpenAI`, all four domain steps and `advance_analysis`/`process_batch` are `async def`, and `StubLLMClient.complete_structured` is `async` too so it stays substitutable. **The database layer is deliberately still sync SQLAlchemy** — no `AsyncSession`, no asyncpg. That mixture is the whole design, and the two rules below are what keep it safe.

**1. Concurrency is per call, never per step.** `process_batch` runs up to `analyser.max_concurrent_calls` coroutines, one per call-under-analysis; each still awaits its four steps in order. That ordering is load-bearing, not stylistic: scoring and gap analysis select their prompt by the call type step one resolves, and card_type reads scoring's score. Splitting the steps was considered and rejected as the wrong axis — different calls are genuinely independent, the steps inside one are not. (Steps *could* be restructured into a 2-wave DAG later, worth ~2x; call-level concurrency scales with worker count and was the bigger lever.)

**2. Only the LLM work runs in a coroutine; every DB touch stays on the consuming coroutine.** Workers receive a plain `_RowInput` (dicts and strings, materialised off the ORM up front) and hand back a plain `AnalysisRecord`. Two distinct reasons, both easy to reintroduce:

- A sync `Session` used from two places at once corrupts its transaction state. `tests/integration/test_batch_processor.py::test_a_concurrent_batch_processes_every_call_against_the_real_database` is the guard.
- `persist_analysis_result` **commits**, and a commit expires every loaded ORM object on that Session. A worker that reached back into `claimed` or the call_storage map after another row was persisted would silently fire a fresh SELECT mid-analysis.

**Manual Card Auto-Fill must never be scheduled as a coroutine.** Starlette runs a sync background callable in its threadpool but an async one *directly on the event loop*. `run_autofill` is `async` for the LLM calls, but its other work is blocking — `request_store` is a sync Session and `card_table_client` a sync HTTP call — so the route hands `BackgroundTasks` the sync `run_autofill_blocking` wrapper, which owns an `asyncio.run` inside the threadpool thread. Passing `run_autofill` directly would stall the API for the length of every autofill. `tests/api/routes/test_autofill.py::test_the_background_task_handed_to_starlette_is_sync_so_it_runs_off_the_loop` pins it.

Same shape at the batch entrypoint: `batch.run.main()` stays **sync**, wrapping `asyncio.run(_run_batch())`, so the scheduler thread and `python -m app.services.batch.run` are both unchanged and the loop is always closed at the end. The `AsyncOpenAI` client is built *inside* that loop (it binds to whichever loop first uses it) and closed via `aclose()`.

The circuit breaker needs **no lock** — all coroutines share one event-loop thread and none of its mutators contain an `await`. What concurrency does change is the meaning of "consecutive": failures now interleave across in-flight calls, so the threshold is a heuristic, and a batch may attempt up to `max_concurrent_calls - 1` extra rows after it opens (rows already started are allowed to finish; rows not yet started are released un-attempted). Most tests in `tests/services/batch/test_processor.py` therefore pin `max_concurrency=1` so their exact counts stay deterministic and to prove the sequential semantics are unchanged; the concurrency-specific tests assert properties that hold at any width.

## Two databases — do not confuse them

1. **Our app DB** (Neon Postgres, `DATABASE_URL`): `call_storage`, `analysis`, `prompt_versions`, `autofill_requests`. We own this schema and its Alembic migrations (`app/db/migrations/`).
2. **Client Table source DB** (AWS RDS, `weatherman` database, `CONVERSATIONAL_EXPERIENCE_RDS_*` env vars): `moonlight_calls` / `moonlight_accounts`, owned and migrated by Koushik's side. **Read-only, always** — mapped on a separate `ClientBase`/engine (`app/db/client_*.py`) specifically so our Alembic autogenerate never sees these tables. `moonlight_calls.avoma_meeting_uuid` is the Avoma Recording ID; `transcription_uuid` is Avoma's own transcription ID (returned by their API, not queried by).

Avoma's `/v1/transcriptions/` endpoint takes `meeting_uuid` as the query param for a direct single-transcript lookup (confirmed against the live API) — not `transcription_uuid`. `client_record_id` on `call_storage` is populated from `moonlight_accounts.crm_account_id` (the HubSpot company ID), not `crm_deal_id` — confirmed with the user since Prompt.md's "RecordId" was ambiguous between the two.

## Pipeline architecture (batch path) — implemented

```text
moonlight_calls / moonlight_accounts (Client Table, NB-only, Koushik's RDS DB)
        │
        ▼
   Call Fetcher (app/services/fetcher/)  — cron entrypoint: app/services/fetcher/run.py
   - diffs avoma_meeting_uuid against OUR call_storage (indexed WHERE-IN, never a full scan)
   - fetches transcript per new ID from Avoma directly (no date-range query)
   - a call with no transcript yet just stays absent from call_storage — retried automatically
     next run, no separate status/retry column needed for that case
              │
              ▼
      call_storage  (our Neon DB — raw transcripts + metadata, keyed by avoma_recording_id)
              │
              ▼
        AI Analyser (app/services/batch/)  — cron entrypoint: app/services/batch/run.py
   - claims pending/failed/stale-processing rows via SELECT ... FOR UPDATE SKIP LOCKED
     (commits immediately, doesn't hold the lock across slow LLM calls)
   - analyses up to analyser.max_concurrent_calls calls at once (one coroutine each);
     the 4 steps WITHIN a call stay sequential — see "The async boundary" above
   - runs the 4 domain steps per-step (independent status/error per step, not one atomic flag)
   - one row's failure never aborts the batch — see "Failure isolation" below
              │
              ▼
        analysis  (our Neon DB)   [Koushik's side will read from here — build ends there]
```

Implementation invariants (still true, now enforced in code):

- **Fetch by known recording ID, never by date range** — `AvomaClient.get_transcript_by_meeting_uuid`.
- **Dedup via indexed lookup** — `app/services/fetcher/repository.py::filter_new_calls`.
- **Visible failure/retry state** — `analysis` has independent `{step}_status`/`{step}_error` columns plus `retry_count`/`dead_letter_at` (escalates after N failures — see `app/services/batch/orchestrator.py`).
- **Prompt/rubric versioning** — `prompt_versions` table, keyed by content hash (not filename), so editing a prompt file in place never silently invalidates past rows' explainability. See the next section for how it's wired; it was claimed here long before it actually worked.

### Prompt-version provenance — wired end to end, with two rules that look like edge cases and aren't

`app/services/prompt_versions.py::resolve_prompt_version_id` registers prompt content on first sight (`INSERT … ON CONFLICT (content_hash) DO NOTHING RETURNING id`, then `SELECT` on the no-op path — same pattern as `scheduler/ledger.py::claim_run`) and returns its id. It deliberately does **not** commit: the caller owns the transaction so a version row and the row referencing it land together. Shared by both the batch pipeline and Manual Card Auto-Fill.

The chain is: `PromptFile.content_hash` → `ClassificationResult.prompt_content_hash` → `AnalysisRecord.{step}_prompt_hash` → resolved to an id inside `app/services/batch/repository.py::persist_analysis_result`. Hash (not id) is what crosses `advance_analysis`, which stays I/O-free; `StepPrompts.by_content_hash(call_type)` gives the persistence layer the content behind each hash. This chain was silently broken for a long time — the hash was computed, returned, and then dropped for want of a field on `AnalysisRecord` — so all 26 pre-existing `analysis` rows have NULL provenance. **Do not backfill them**; they genuinely were produced without it.

Both rules below exist because `persist_analysis_result` upserts in place, and both have named tests:

1. **A version id is only ever recorded for a step that succeeded on this pass.** `_run_step` returns a `ClassificationResult` only in that case, so `_prompt_hash()` yields `None` for a failed, skipped, or already-processed step, and `fail_analysis` leaves all four hashes `None`. Attaching provenance to a failed step claims a lineage for output that doesn't exist.
2. **A `None` hash omits the column from the UPDATE — it never writes NULL.** On a partial re-run (a step already `processed`, or `call_type` unknown so scoring/gap were skipped) writing NULL would erase provenance an earlier pass recorded correctly.

`autofill_requests.card_type_prompt_version_id` / `gap_fill_prompt_version_id` follow the same rules via `SqlAutofillRequestStore.record_prompt_versions`, called **after** the card write succeeds and **before** the status flips to `processed`, and only for the field(s) actually filled. This is load-bearing while `gap_fill/v1.txt` is a placeholder: it's the only way to answer "which autofilled cards came from the placeholder and need redoing?"

### Failure isolation in the analyser batch — two layers, don't collapse them

Both layers record failure through the *existing* `{step}_status`/`{step}_error`/`retry_count`/`dead_letter_at` columns. Don't add a parallel error mechanism, and don't let either layer produce a row that looks successful.

1. **Per-step** (`app/services/batch/orchestrator.py::_run_step`): catches `LLMOutputError` **and `openai.APIError`**. The transport half matters — `APITimeoutError` (raised by `app/llm/client.py` after its own retry loop is exhausted), `APIConnectionError`, and `APIStatusError` (the gateway really does emit HTTP 500s; a bad `reasoning_effort` surfaced exactly that way) are *not* `LLMOutputError`. They used to propagate out of `advance_analysis` → `process_batch` and abort the run. A step that couldn't reach the gateway is a failed step, not a failed run; the other three steps still run.
2. **Per-row** (`app/services/batch/processor.py`): each row is wrapped in `try/except Exception` → `orchestrator.fail_analysis()`, which marks every not-yet-finished step failed with the error text and continues the loop. This layer exists for failures that happen *outside* the steps — chiefly `Transcript.model_validate` rejecting a pre-timestamp `call_storage` row in `render_transcript_text` — plus anything unanticipated. A claimed row with no `call_storage` row goes down the same path instead of being silently skipped.

**Stale-claim reclamation** (`repository.claim_rows`): `claim_rows` commits `status = processing` immediately and by design, so a run that dies mid-batch used to strand up to `batch_size` rows in `processing` forever — `processing` isn't in the retryable set, so those rows were invisible to retry *and* dead-lettering. `claim_rows` now also claims `processing` rows whose `updated_at` is older than `config.yaml`'s `analyser.stale_claim_minutes` (default 360). No migration was needed: `analysis.updated_at` has `onupdate=func.now()` and the claim is an `update()`, so claiming timestamps the row. Two things to keep in mind if you touch this:

- The cutoff is computed with the **DB clock** (`func.now() - timedelta(...)`), matching the clock that writes `updated_at`. Don't swap in a Python `datetime.now()`.
- The claim timestamp is **batch-wide, not per-row** — all `batch_size` rows share the timestamp written when the batch was claimed, so the threshold must exceed a whole batch's worst-case duration, not one row's. `config.yaml` carries the derivation; re-derive it if `batch_size`, `max_retries`, or the gateway timeout changes.
- **`repository.release_claims` is not an operator tool.** It is safe only when called by the run that owns the claims (the circuit-breaker path in `process_batch`), because it cannot tell whether a claim is still live. Using it to "unstick" rows after an apparently-dead run caused real double-processing: the process was orphaned but still working through the rows it held in memory while a fresh run claimed the ones just marked `pending`. The `stale_claim_minutes` window exists *because* liveness can't be determined from outside. If rows look stuck, confirm no `python -m app.services.batch.run` is alive, or just wait — 6h is well inside the 24h cadence.

### Circuit breaker — the counterweight to failure isolation

Isolation means a dead gateway no longer aborts on row one, so without a breaker the batch spends `llm_gateway.timeout_seconds × (max_retries + 1)` per step re-learning the same thing on every remaining row — ~2.7h of pure timeouts at current settings. `app/services/batch/circuit_breaker.py` supplies the missing "every step of every row is failing identically, stop" signal.

- **Only transport failures count.** `openai.APIError` (timeout / connection / HTTP 5xx) means we couldn't get an answer, so it increments. An `LLMOutputError` means the gateway *answered* and we rejected the content — that is a live gateway, so it **resets** the counter. Counting rejected answers would let malformed prompt output halt a perfectly healthy batch. `tests/services/batch/test_processor.py::test_malformed_responses_never_trip_the_breaker` pins this, and it only bites when *every* step fails — a version of that test where one step still succeeded passed even with the logic inverted.
- **The threshold must exceed 4** (`analyser.circuit_breaker_consecutive_failures`, default 6, validated at config load; 0 disables). A row has 4 steps, so 1..4 is reachable by one oversized transcript timing out on all of its own steps — a per-call problem, not an outage. Note a *fresh* row usually contributes only **2** failures, because a failed `call_type` leaves no call type to select a scoring prompt or gap rubric with, so those two steps are skipped; a **retried** row whose `call_type` is already known runs all four. Both cases are tested.
- **Checked between rows, not between steps** — the row that trips the breaker still finishes its remaining steps. Deliberate: step-granularity would mean marking steps failed that were never attempted.
- **On trip**, `process_batch` releases the rows it claimed but never attempted (`repository.release_claims`, back to `pending`) and raises `GatewayUnavailableError`. Releasing matters because stale reclamation exists for runs that *died*; a breaker trip is a controlled stop that should clean up after itself, and it lets an operator re-run the moment the gateway is back instead of waiting out `stale_claim_minutes`. The exception propagates out of `batch.run.main()` on purpose — `run_daily_pipeline` marks the day's `scheduled_run` row `failed`, which is honest: the run did not do its job.
- Rows attempted **before** the trip keep their real failure state, but **spend no retry budget** — see "Retry budget" below.
- If the breaker opens on the **last** row there is nothing to release, but `process_batch` still raises. Otherwise a night where the gateway was down and every row failed would be recorded as a *completed* run in the ledger.

### Retry budget is per step, and an outage doesn't charge for it

Two rules, both with named tests. Escalation to `failed_permanent` reads the per-step `{step}_retry_count` columns (migration `cddcbee4eb25`), **not** the row-level `retry_count` — that is kept only as "how many passes has this row had".

1. **A step's failure spends only that step's budget.** The old shared counter meant scoring failing on three nights left gap with nothing, even though gap had never failed once — the row dead-lettered while every individual step still had budget. `test_one_steps_failures_do_not_drain_another_steps_retry_budget`.
2. **A transport failure charges nothing unless the gateway proved itself alive during the same run** (`CircuitBreaker.gateway_proved_alive`, latched on the first answer of any kind). Nothing answering means an outage, and dead-lettering a call over an outage blames the call for infrastructure. But if other steps *did* get answers, a failure isolated to one step is that step's own problem and must be able to exhaust its budget — otherwise a permanently broken step retries silently forever. `test_an_outage_does_not_spend_any_step_budget` and `test_a_step_specific_gateway_failure_does_spend_budget` are the two halves; breaking either direction fails one of them.

A row-level blow-up (`fail_analysis` — invalid transcript, missing `call_storage` row) **does** spend budget on every unfinished step: that's a real problem with the row, not an outage.

## Scheduling — implemented

The fetcher and analyser are triggered by an in-process `BackgroundScheduler` (APScheduler), wired into `app/main.py`'s FastAPI `lifespan`, not by external cron infra. Design rationale: `docs/superpowers/specs/2026-08-07-in-process-scheduler-design.md`.

- **Why `BackgroundScheduler`, not `AsyncIOScheduler`** — still correct after the analyser went async, for the same underlying reason. `fetcher.run.main()` is fully synchronous, and `batch.run.main()` is a deliberately **sync wrapper** around `asyncio.run(_run_batch())`: its database access is sync SQLAlchemy throughout, so the job still blocks its thread for the whole pipeline duration. Running it on FastAPI's event loop would freeze the Manual Card Auto-Fill API exactly as before. `BackgroundScheduler` gives the job its own OS thread, which is also what lets `asyncio.run` create a loop there. Do not "simplify" this by making `main()` async and switching to `AsyncIOScheduler` — that reintroduces the stall.
- **Fixed time of day**, not "24h since last restart" — `config.yaml`'s `scheduler:` block (`hour`/`minute`/`timezone`), read by `app/core/scheduler_config.py`.
- **Cross-replica dedup**: `scheduled_run` table, unique on `(job_name, run_date)`. `app/services/scheduler/ledger.py::claim_run` does `INSERT ... ON CONFLICT DO NOTHING RETURNING id` — if this server ever scales to multiple replicas, only the replica whose insert lands runs that day's pipeline; the rest no-op. Also gives a queryable "did today's run happen, did it succeed" record, consistent with this project's existing preference for visible status over silent state (same spirit as `analysis`'s per-step status columns).
- **Ordering/failure policy** (`app/services/scheduler/pipeline.py::run_daily_pipeline`): claim → run fetcher → **if fetcher raises, skip the analyser entirely this cycle** (both retried together tomorrow) → run analyser → mark the ledger row `completed`/`failed`.
- **Failure visibility**: structured logs only, no alerting integration — a deliberate choice, not an oversight; revisit if silent failures become a real problem.

## Per-call outputs (Analysis Table row)

1. **Call Score** — High/Medium/Low (categorical — NOT numeric; `analysis.call_score` is a `String` column, fixed after an earlier modeling mistake), from the business-provided scoring prompt run against the transcript.
2. **Call Type** — one of: Discovery, Demo, Follow-up Demo, Pricing/Negotiation, Technical Integration, Kick-off.
3. **Risk/Gap Analysis** — transcript checked against the call-type-specific gap-theme rubric. Each reported gap carries `evidence_type` (`"dialogue"` or `"explanation"`), `evidence`, an optional `timestamp`, and a `confidence` level. Enforced by `app/domain/gap_analysis.py`: `evidence_type: "dialogue"` requires a `timestamp`; `evidence_type: "explanation"` must not have one — never fabricate a timestamp for a whole-call pattern.
4. **Card Type** — Coaching or Risk, via `app/domain/card_type.py::classify_card_type` — the one shared classifier, structurally reused by both the batch pipeline and Manual Card Auto-Fill (an optional-fields `CardTypeContext` DTO, not two implementations).

## Configuration

- **`.env`** (gitignored, never committed) — secrets and connection strings: `DATABASE_URL` (our Neon DB), `CONVERSATIONAL_EXPERIENCE_RDS_*` (client DB), `LLM_GATEWAY_URL`/`LLM_GATEWAY_KEY`, `AVOMA_BASE_URL`/`AVOMA_API_KEY`.
- **`config.yaml`** (checked in) — non-secret tunables: `llm_gateway:` (model, timeout, retry count, trace metadata), `analyser:` (`gap_rubric_mode: descriptiononly|fewshot` — still an unvalidated experiment, see Open decisions; `batch_size`; `max_retries`; `stale_claim_minutes` — how long a `processing` claim may sit before it's assumed dead, see "Failure isolation"; `max_concurrent_calls` — how many calls are analysed at once, so also the ceiling on simultaneous gateway requests; validated `>= 1` at load because 0 builds a semaphore nothing can acquire and the batch would hang silently), `scheduler:` (`hour`/`minute`/`timezone` — when the in-process cron fires).
  - `max_concurrent_calls: 4` is a **starting point, not a measured optimum** — this gateway's rate limits aren't documented anywhere we control. Raise it while watching for HTTP 429s (which arrive as `APIStatusError`, so they count as transport failures and can trip the circuit breaker). See Open decisions.

## Open decisions — flag, don't silently resolve

- **The pipeline is not reproducible, and that outranks every other open decision here.** Measured 2026-08-11 over 46 calls: re-running the **identical** input at the **identical** settings reproduces all four outputs on only **43% of calls**. `call_score` flips on 22% (10/46), `call_type` on 11%, and gap count differs on 43% (total gaps 86 vs 77 between two `high` runs). Score churn skews toward the middle — `Low→Medium` 5, `High→Low` 2, `Medium→Low`/`High→Medium`/`Medium→High` 1 each — which *hints* the scoring rubric's Low/Medium boundary is underspecified rather than the model being random, but that is a hypothesis, not a result. Not diagnosed: gateway vs. model vs. genuinely ambiguous rubric boundaries. Two consequences that bite immediately:
  - A moderator's card depends on which night the call was processed; two runs can disagree High vs Low. Worth raising with whoever owns the scoring prompt.
  - **Any single-run prompt comparison here reads noise as signal.** The first pass of the `reasoning_effort` A/B did exactly that and produced a confident, wrong "70% agreement" number. Always measure the same-setting control first.
- **Few-shot vs. description-only** gap prompting — both modes are wired (`gap_rubric_mode` in `config.yaml`, distinct `prompt_versions` entries), but which one wins is still unvalidated. `app/services/eval/harness.py` runs both side by side. **A single run of each cannot separate them** at a 43% reproduction rate — this needs the same-setting control and probably several runs per mode. See `docs/eval/2026-08-11-reasoning-effort-ab.md` for the method.
- **~~`reasoning_effort: high` has never been validated against `medium`~~ — RESOLVED 2026-08-11.** Measured over all 46 calls: `medium` and `high` are **indistinguishable on output quality**, every difference falling inside the model's own run-to-run variance. Full writeup and raw data: `docs/eval/2026-08-11-reasoning-effort-ab.md`. Either setting is fine; `medium` saves 22% of reasoning tokens (5,935 vs 7,620/call) for only ~5% of wall-clock. **The old premise here was wrong** — `high` measures **64.7s/call** fresh, not ~82s, and `medium` 61.3s, so switching does *not* halve anything. Don't reopen this expecting a speedup.
  - Use `app/services/eval/reasoning_effort_ab.py` for any repeat. It never writes to `analysis` (the 46 rows are the baseline and must survive), and it takes `--nonce` — mandatory, see the cache note below.
- **Manual Auto-Fill inputs beyond comment text** — `CardTypeContext`/the autofill worker accept optional `transcript`/`call_metadata`, but whether the real integration actually populates those fields is undecided.
- **`CardTableClient`** (writing Type/Gap into Koushik's external manual-card table) — still a `NotImplementedError` in `app/api/deps.py`. Blocked on that table's schema, which hasn't been shared yet.
- **Account-level risk rollup** — surface this if a task seems to need it; not in this build's scope.
- **Partial completion is deliberately NOT pursued** — confirmed with the user: once any step exhausts its own budget the **whole call dies** (`overall_status` → `failed_permanent`, which `claim_rows` no longer picks up), even if other steps still had retries left. A call that can't be fully analysed shouldn't become a moderator's card. The row still physically holds whatever succeeded before that point, marked `failed_permanent` so nothing treats it as complete — don't "fix" this into chasing 3-of-4 rows without asking again.
- **Remaining placeholder prompts** — `gap_rubric/*-fewshot.yaml` and `gap_fill/v1.txt` are still placeholder files under `app/prompts/`; must be swapped for the business team's real versions before any output relying on them is trustworthy. (`scoring/`, `gap_rubric/*-descriptiononly.yaml`, `card_type/`, and `call_type/` now have real content — see "Hard scope boundaries" above.)
- **Thin-content gate** — 3 of 26 sampled calls had under 300 words of actual conversation (one had 30) and were all scored `Low` with 0 gaps, which reads as "bad rep" when it means "nobody said anything". Not an Avoma or data-integrity issue — see the transcript-contract section. Undecided: whether to skip such calls, mark them a distinct status, or analyse them with a caveat, and where the word-count floor sits.
- **Full historical backfill** — the Call Fetcher has been run against 46 of 245 real calls (`--limit`) for validation, not the full `moonlight_calls` backlog. Confirm scope before running it unbounded. **Measured 2026-08-11:** per-call latency is **64.7s** at `reasoning_effort: high` (not the ~82s previously recorded here), and 46 calls take **346s wall-clock at `max_concurrent_calls: 10`**. So the remaining ~199 calls is roughly **25 minutes**, down from ~4.5h serial. `medium` would not help — it is 61.3s/call, ~5% faster. Note the daily-throughput cap below applies to a scheduled run, not to a manual `python -m app.services.batch.run`.
- **`max_concurrent_calls: 10` runs clean but has not been pushed to a limit** — 92 real calls' worth of traffic (two full 46-call A/B sides) produced **zero HTTP 429s and zero step errors** at 10, so 10 is validated as safe rather than merely assumed. Whether more headroom exists is untested. If you raise it, watch for 429s: they surface as `APIStatusError`, which the breaker counts as a transport failure, so throttling presents as an outage rather than as backpressure. A separate probe confirmed 10 simultaneous requests are fine even with 16–25KB transcript bodies.
- **The analyser processes at most `batch_size` calls per scheduled day** — `run_daily_pipeline` calls `batch_run.main()` exactly once, and `main()` calls `process_batch` once with `limit=batch_size` (currently 10). Concurrency made each batch ~4x faster in wall-clock but did **not** change this cap, so it is now the binding constraint: with 245 calls in `moonlight_calls` the backlog drains at 10/day, and if daily NB volume ever exceeds 10 it grows without bound. Surfaced, deliberately not changed — the fix is either looping `process_batch` until it returns 0 or sizing `batch_size` to real volume, and which one is right depends on how much LLM spend per night is acceptable. Ask before changing it.

## Agent workflow preferences

- **Never invoke the `superpowers:writing-plans` skill in this project.** Brainstorming/design work is still welcome (and its spec docs still land under `docs/superpowers/specs/`), but once a design is approved, write the implementation plan directly yourself rather than handing off to that skill.

## Build & test commands

```bash
uv sync                                    # install deps
uv run pytest                              # fast suite (unit + component tests, no network)
uv run pytest -m integration               # hits real Neon + real client RDS DB — slower, real state
uv run alembic revision --autogenerate -m "..."   # after changing app/db/models.py
uv run alembic upgrade head                        # apply migrations to OUR Neon DB only
uv run uvicorn app.main:app --reload       # runs the Manual Card Auto-Fill API AND starts the
                                            # in-process scheduler (fetcher+analyser at config.yaml's
                                            # scheduler: hour/minute/timezone) — no separate cron needed
uv run python -m app.services.fetcher.run [--limit N]   # manual/ad-hoc Call Fetcher run — omit --limit for full run
uv run python -m app.services.batch.run                  # manual/ad-hoc AI Analyser batch pass
```

Integration tests (`tests/integration/`) always clean up their own rows (via fixtures) — never leave test data behind in either database. That now includes `prompt_versions`: any `process_batch`/autofill call registers prompt content, so `tests/integration/test_batch_processor.py`'s autouse `no_prompt_version_residue` fixture deletes whatever a test registered, dropping FK references first so it doesn't depend on fixture teardown order. Never write to `moonlight_calls`/`moonlight_accounts` from any test or code path; they're Koushik's tables.
