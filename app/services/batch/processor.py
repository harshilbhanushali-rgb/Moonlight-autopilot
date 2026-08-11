import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.analyser_config import (
    DEFAULT_CIRCUIT_BREAKER_CONSECUTIVE_FAILURES,
    DEFAULT_MAX_CONCURRENT_CALLS,
    DEFAULT_STALE_CLAIM_MINUTES,
)
from app.services.batch import repository
from app.services.batch.circuit_breaker import CircuitBreaker, GatewayUnavailableError
from app.services.batch.orchestrator import (
    AnalysisRecord,
    StepPrompts,
    advance_analysis,
    fail_analysis,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RowInput:
    """Everything one row's analysis needs, read off the ORM up front.

    Materialised before any coroutine starts because `persist_analysis_result`
    commits, and a commit expires every loaded ORM object on this Session — so
    a coroutine that reached back into `claimed` or the call_storage map after
    another row had been persisted would silently trigger a fresh SELECT
    mid-analysis. Plain dicts and strings can't do that.
    """

    recording_id: str
    record: dict
    has_call_storage: bool
    transcript: dict | None
    call_metadata: dict


@dataclass(frozen=True)
class _RowResult:
    recording_id: str
    # None means the row was never attempted (the breaker was already open when
    # its turn came), so it must be released rather than persisted.
    record: AnalysisRecord | None
    session_may_be_dirty: bool = False


async def process_batch(
    *,
    session: Session,
    llm_client,
    prompts: StepPrompts,
    limit: int = 10,
    max_retries: int = 3,
    stale_claim_minutes: int = DEFAULT_STALE_CLAIM_MINUTES,
    circuit_breaker_threshold: int = DEFAULT_CIRCUIT_BREAKER_CONSECUTIVE_FAILURES,
    max_concurrency: int = DEFAULT_MAX_CONCURRENT_CALLS,
) -> int:
    """Claims up to `limit` pending/retryable analysis rows and runs the AI
    Analyser on each. Returns how many rows were attempted this call.

    Up to `max_concurrency` calls are analysed at once — one coroutine per
    call, each still running its own four steps strictly in order (see
    `advance_analysis`). Concurrency is per call and not per step because the
    steps have real data dependencies on each other, while different calls have
    none at all.

    Only the LLM work is concurrent. Every database touch happens on this
    coroutine, before the workers start or as their results arrive, so the
    single sync Session is never used from two places at once — the workers
    receive plain dicts (`_RowInput`) and hand back plain records.

    Each row is isolated: a failure that the per-step handlers in
    advance_analysis don't cover (a transcript that fails contract validation,
    a missing call_storage row, an unanticipated bug) is recorded against that
    row and the rest of the batch continues.

    That isolation is what makes the circuit breaker necessary: with it, a dead
    gateway no longer aborts on row one, so the batch would otherwise spend
    hours failing every step of every row the same way. Once the breaker opens,
    rows that have not started are abandoned un-attempted and released; rows
    already in flight finish and record their real outcome. Raises
    GatewayUnavailableError afterwards.
    """
    repository.seed_missing_analysis_rows(session)
    claimed = repository.claim_rows(session, limit, stale_claim_minutes=stale_claim_minutes)
    if not claimed:
        return 0

    call_storage_map = repository.get_call_storage_map(
        session, [row.avoma_recording_id for row in claimed]
    )

    row_inputs = []
    for row in claimed:
        recording_id = row.avoma_recording_id
        call_storage_row = call_storage_map.get(recording_id)
        row_inputs.append(
            _RowInput(
                recording_id=recording_id,
                record=repository.analysis_row_to_record_dict(row),
                has_call_storage=call_storage_row is not None,
                transcript=call_storage_row.transcript if call_storage_row else None,
                call_metadata=(call_storage_row.call_metadata or {}) if call_storage_row else {},
            )
        )

    breaker = CircuitBreaker(threshold=circuit_breaker_threshold)
    semaphore = asyncio.Semaphore(max_concurrency)

    async def analyse_row(row_input: _RowInput) -> _RowResult:
        async with semaphore:
            if breaker.is_open:
                # Checked at admission rather than mid-analysis, so a row is
                # either attempted in full or not at all — marking steps failed
                # that were never sent to the gateway would be a lie.
                return _RowResult(row_input.recording_id, None)

            if not row_input.has_call_storage:
                # Only reachable if the call_storage row was deleted after the
                # analysis row was seeded from it. Recorded as a failure rather
                # than skipped, so the row doesn't sit in `processing` and get
                # reclaimed-then-skipped forever.
                logger.error(
                    "No call_storage row for claimed analysis row %s; recording it as failed.",
                    row_input.recording_id,
                )
                return _RowResult(
                    row_input.recording_id,
                    fail_analysis(
                        record=row_input.record,
                        error=f"no call_storage row for {row_input.recording_id}",
                        max_retries=max_retries,
                    ),
                )

            try:
                record = await advance_analysis(
                    llm_client=llm_client,
                    transcript_text=repository.render_transcript_text(row_input.transcript),
                    call_metadata=row_input.call_metadata,
                    prompts=prompts,
                    record=row_input.record,
                    max_retries=max_retries,
                    breaker=breaker,
                )
            except Exception as exc:
                logger.exception(
                    "Analysis of call %s failed outside the per-step handlers; "
                    "recording it as failed and continuing with the rest of the batch.",
                    row_input.recording_id,
                )
                return _RowResult(
                    row_input.recording_id,
                    fail_analysis(
                        record=row_input.record,
                        error=f"{type(exc).__name__}: {exc}",
                        max_retries=max_retries,
                    ),
                    session_may_be_dirty=True,
                )

            return _RowResult(row_input.recording_id, record)

    tasks = [asyncio.create_task(analyse_row(row_input)) for row_input in row_inputs]

    attempted = 0
    unattempted: list[str] = []
    try:
        for completed in asyncio.as_completed(tasks):
            result = await completed

            if result.record is None:
                unattempted.append(result.recording_id)
                continue

            if result.session_may_be_dirty:
                session.rollback()

            repository.persist_analysis_result(
                session,
                result.record,
                # The record carries a content hash per successful step; the
                # repository needs the prompt content itself to register a
                # version. Scoped by the call_type the record ended up with,
                # which is the one that chose the scoring prompt and gap rubric
                # actually used.
                prompts_by_hash=prompts.by_content_hash(result.record.call_type),
            )
            attempted += 1
    finally:
        # Only reachable if persisting itself blew up. Without this the
        # abandoned tasks would be garbage-collected while pending, which loses
        # their outcome and logs "Task was destroyed but it is pending".
        await _cancel_pending(tasks)

    if breaker.is_open:
        released = repository.release_claims(session, unattempted)
        logger.error(
            "Circuit breaker open after %d consecutive gateway failures; "
            "stopped this batch after %d of %d row(s) and released %d claim(s). "
            "Last gateway error: %s",
            breaker.consecutive_failures,
            attempted,
            len(claimed),
            released,
            breaker.last_error,
        )
        raise GatewayUnavailableError(
            f"LLM gateway unreachable: {breaker.consecutive_failures} consecutive "
            f"failures (threshold {breaker.threshold}). Stopped after {attempted} of "
            f"{len(claimed)} row(s); {released} claim(s) released for retry. "
            f"Last error: {breaker.last_error}"
        )

    return attempted


async def _cancel_pending(tasks) -> None:
    pending = [task for task in tasks if not task.done()]
    if not pending:
        return
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
