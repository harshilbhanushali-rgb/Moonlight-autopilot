# Moonlight Autopilot

AI/backend layer for Joveo's Moonlight call-coaching platform. Reads every New Business (NB) sales call automatically and generates the same kind of transcript-anchored feedback card a human auditor would — Call Score, Call Type, Risk/Gap Analysis, Card Type — for human moderator review. Also powers Manual Card Auto-Fill: filling in a blank `Type`/`Gap` field on a manually-created card from its comment text.

See [Prompt.md](Prompt.md) for the full spec and [CLAUDE.md](CLAUDE.md) for implementation details, hard scope boundaries, and open decisions.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Access to two Postgres databases (see Configuration below) and the Avoma API

## Setup

```bash
uv sync
```

Create a `.env` file (gitignored) with:

```bash
# Our app DB (Neon Postgres) — call_storage, analysis, prompt_versions, autofill_requests
DATABASE_URL=postgresql+psycopg://...

# Client Table source DB (Koushik's side, read-only — moonlight_calls / moonlight_accounts)
CONVERSATIONAL_EXPERIENCE_RDS_URL=...
CONVERSATIONAL_EXPERIENCE_RDS_USER=...
CONVERSATIONAL_EXPERIENCE_RDS_WEATHERMAN_PASSWORD=...
CONVERSATIONAL_EXPERIENCE_RDS_WEATHERMAN_DB=...

# LLM gateway (OpenAI-compatible internal proxy)
LLM_GATEWAY_URL=...
LLM_GATEWAY_KEY=...

# Avoma API
AVOMA_BASE_URL=https://api.avoma.com
AVOMA_API_KEY=...
```

Non-secret tunables (model name, timeouts, retry counts, gap-rubric mode, cron schedule time) live in [config.yaml](config.yaml) instead, since they're safe to check in.

Apply migrations to our own DB (never touches the client DB — that one's owned and migrated by Koushik's side):

```bash
uv run alembic upgrade head
```

## Running

```bash
# Manual Card Auto-Fill API — also starts the in-process scheduler, which runs
# the fetcher then the analyser automatically once a day at config.yaml's
# scheduler: hour/minute/timezone. No separate cron infra needed.
uv run uvicorn app.main:app --reload
```

There's no external cron — the fetcher and analyser run inside the same
always-on server process (`app/services/scheduler/`), since both steps are
synchronous/blocking and run on a dedicated background thread rather than
FastAPI's event loop. See [CLAUDE.md](CLAUDE.md)'s "Scheduling" section for
why, and the design doc under `docs/superpowers/specs/` for the full
rationale.

For manual/ad-hoc runs (e.g. validating against a handful of real calls):

```bash
# Call Fetcher — pulls new NB call transcripts from Avoma into call_storage
uv run python -m app.services.fetcher.run          # all new calls
uv run python -m app.services.fetcher.run --limit 5  # scoped run, e.g. for validation

# AI Analyser — scores/classifies fetched calls, writes to analysis
uv run python -m app.services.batch.run
```

## Testing

```bash
uv run pytest                  # fast suite — unit + component tests, no network/DB
uv run pytest -m integration    # hits the real Neon DB + real client RDS DB
```

Integration tests always clean up the rows they create. Nothing in the test suite ever writes to `moonlight_calls`/`moonlight_accounts`.

## Architecture

```text
moonlight_calls / moonlight_accounts  (Client Table — NB-only, Koushik's RDS DB, read-only)
        │
        ▼
   Call Fetcher            — diffs against call_storage, fetches new transcripts from Avoma
        │
        ▼
   call_storage             (our DB — raw transcripts + metadata)
        │
        ▼
   AI Analyser              — Call Score, Call Type, Risk/Gap, Card Type
        │
        ▼
   analysis                 (our DB — Koushik's side reads from here)
```

Business logic (`app/domain/`) is pure and has zero I/O; everything under `app/services/`, `app/api/`, and `app/db/` is an adapter around it. See [CLAUDE.md](CLAUDE.md) for the full project structure and current open decisions.
