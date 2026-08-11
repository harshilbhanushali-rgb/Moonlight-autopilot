# In-process scheduler for the fetcher + analyser cron

**Date**: 2026-08-07
**Status**: Approved

## Problem

The Call Fetcher (`app/services/fetcher/run.py`) and AI Analyser
(`app/services/batch/run.py`) are implemented and tested, but nothing
actually schedules them. The README describes them as "meant to run as a 24h
cron, fetcher first," but there's no cron infra in the repo — no Dockerfile,
no GitHub Actions workflow, no k8s CronJob manifest.

The Manual Card Auto-Fill FastAPI server (`app/main.py`) already runs
continuously. Rather than stand up separate external scheduling
infrastructure, this design adds an in-process scheduler to that same
server: the fetcher and analyser run on a daily timer inside the FastAPI
process.

## Constraints from discussion

- The server is a single instance today but **could scale to multiple
  replicas later**. The design must not double-run the pipeline if that
  happens.
- Failure visibility is **structured logs only** for now — no new
  alerting/notification integration.
- The pipeline should run at a **fixed time of day** (not "24h since last
  restart"), so behavior is predictable across deploys.
- If the fetcher step fails, the analyser step should **not** run that
  cycle — both are treated as failed for that day and retried on the next
  cycle.
- Both `fetcher.run.main()` and `batch.run.main()` are synchronous/blocking
  (sync DB sessions, sync HTTP clients), so the scheduler must not run them
  on FastAPI's event loop thread.

## Design

### Scheduling

Use APScheduler's `BackgroundScheduler` with a `CronTrigger` fixed to a
configurable time. `BackgroundScheduler` runs jobs in its own worker thread,
so the blocking fetcher/analyser calls never stall the autofill API's event
loop.

Config (`config.yaml`), a new `scheduler:` block:

```yaml
scheduler:
  hour: 2
  minute: 0
  timezone: UTC
```

`app/main.py` gains a `lifespan` context manager: build and start the
scheduler on app startup, call `scheduler.shutdown()` on app shutdown.
Existing tests are unaffected — they build their own bare `FastAPI()`
instance and never import `app.main.app`.

### Cross-replica dedup

New table `scheduled_run`:

| column      | type      | notes                              |
|-------------|-----------|-------------------------------------|
| id          | int, PK   |                                     |
| job_name    | str       | e.g. `"daily_pipeline"`             |
| run_date    | date      |                                     |
| status      | str       | `running` / `completed` / `failed` |
| started_at  | datetime  |                                     |
| finished_at | datetime  | nullable                           |
| error       | text      | nullable                           |

Unique constraint on `(job_name, run_date)`.

When the cron trigger fires (on every replica, roughly simultaneously),
each replica attempts:

```sql
INSERT INTO scheduled_run (job_name, run_date, status, started_at)
VALUES (:job_name, :run_date, 'running', now())
ON CONFLICT (job_name, run_date) DO NOTHING
RETURNING id
```

Only the replica whose insert returns a row proceeds to run the pipeline;
every other replica gets no row back, logs "already claimed for today," and
skips. This mirrors the `SELECT ... FOR UPDATE SKIP LOCKED` claim pattern
already used in `app/services/batch/orchestrator.py`, and — consistent with
this project's existing preference for visible status over silent state —
gives a queryable table of whether a given day's cron actually ran and
whether it succeeded.

### Module layout

New `app/services/scheduler/` (mirrors the existing `fetcher/`/`batch/`
service layout):

- `ledger.py` — `claim_run(session, job_name, run_date) -> int | None`,
  `mark_completed(session, run_id)`, `mark_failed(session, run_id, error)`.
- `pipeline.py` — `run_daily_pipeline()`:
  1. Open a session, call `claim_run`. If no row was claimed, log and return.
  2. Call `fetcher.run.main()`. On exception: log it, `mark_failed`, return
     (do not proceed to the analyser step).
  3. Call `batch.run.main()`. On exception: log it, `mark_failed`, return.
  4. On success of both: `mark_completed`.
- `scheduler.py` — `build_scheduler() -> BackgroundScheduler`: reads the
  `scheduler:` config block, constructs a `BackgroundScheduler` with a
  `CronTrigger(hour=..., minute=..., timezone=...)` job pointed at
  `run_daily_pipeline`.

### New dependency

`apscheduler` added to `pyproject.toml`.

### Migration

One Alembic migration adding `scheduled_run` with the unique constraint
above.

## Testing

- `ledger.py`: claiming the same `(job_name, run_date)` twice returns a row
  the first time and `None` the second; `mark_completed`/`mark_failed`
  update status and timestamps correctly.
- `pipeline.py`: with `fetcher.run.main`/`batch.run.main` mocked —
  - happy path calls fetcher then batch, in order, and marks the run
    completed;
  - a `None` claim (already claimed) skips both steps entirely;
  - a fetcher exception marks the run failed and never calls batch's
    `main`;
  - a batch exception (after a successful fetch) marks the run failed.
- `scheduler.py`: `build_scheduler()` returns a scheduler with exactly one
  job, whose trigger matches the configured hour/minute/timezone.
- No integration test needed beyond the existing fetcher/analyser
  integration tests — this layer only adds claiming + ordering + logging
  around already-tested steps.

## Out of scope

- Alerting/notifications on failure (logs only, per discussion).
- Retrying within the same day if a step fails (next day's cron retries
  both steps together).
- Any change to the fetcher's or analyser's own internal logic.
