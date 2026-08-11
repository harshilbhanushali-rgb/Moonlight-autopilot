import logging
from dataclasses import dataclass
from typing import Callable

from openai import APIError

from app.domain.call_type import classify_call_type
from app.domain.card_type import classify_card_type
from app.domain.errors import LLMOutputError
from app.domain.gap_analysis import analyse_gaps
from app.domain.scoring import score_call
from app.domain.types import CallScore, CallType, CardType, CardTypeContext, Gap
from app.db.models import STATUS_FAILED, STATUS_FAILED_PERMANENT, STATUS_PROCESSED
from app.prompts.registry import PromptFile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StepPrompts:
    call_type: PromptFile
    scoring_for: Callable[[CallType], PromptFile]
    card_type: PromptFile
    gap_rubric_for: Callable[[CallType], PromptFile]

    def by_content_hash(self, call_type: CallType | None) -> dict[str, PromptFile]:
        """Every PromptFile reachable for `call_type`, keyed by content hash.

        The persistence layer receives content hashes on the AnalysisRecord and
        needs the content itself to register a prompt version, so this is the
        lookup that closes that gap. Scoped by call_type because the scoring
        prompt and gap rubric are chosen by it — passing the call_type the
        record ended up with yields exactly the prompts that produced it.
        """
        prompts = [self.call_type, self.card_type]
        if call_type is not None:
            prompts += [self.scoring_for(call_type), self.gap_rubric_for(call_type)]
        return {p.content_hash: p for p in prompts}


@dataclass
class AnalysisRecord:
    avoma_recording_id: str
    call_type: CallType | None
    call_score: CallScore | None
    risk_gap_analysis: list[Gap] | None
    card_type: CardType | None
    call_type_status: str
    scoring_status: str
    gap_status: str
    card_type_status: str
    call_type_error: str | None
    scoring_error: str | None
    gap_error: str | None
    card_type_error: str | None
    retry_count: int
    dead_letter_at: object | None
    # Per-step retry budget. `retry_count` above is row-level and kept only as
    # "how many passes has this row had" for observability — escalation to
    # failed_permanent is driven by these, because a single shared counter meant
    # a failure in one step permanently consumed every other step's chances.
    call_type_retry_count: int = 0
    scoring_retry_count: int = 0
    gap_retry_count: int = 0
    card_type_retry_count: int = 0
    # Content hash of the prompt each step used, set only for a step that
    # SUCCEEDED on this pass. Deliberately a hash and not a prompt_versions.id:
    # advance_analysis stays I/O-free, and hash -> id resolution happens in
    # app/services/batch/repository.py where the session lives.
    #
    # None means "this pass has nothing to say about that step's provenance" —
    # it failed, or it was skipped (already processed, or call_type unknown so
    # scoring/gap never ran). The persistence layer must therefore leave the
    # existing column value alone rather than writing NULL, or a partial re-run
    # would erase provenance a previous pass recorded correctly.
    call_type_prompt_hash: str | None = None
    scoring_prompt_hash: str | None = None
    gap_rubric_hash: str | None = None
    card_type_prompt_hash: str | None = None

    @property
    def overall_status(self) -> str:
        statuses = {
            self.call_type_status,
            self.scoring_status,
            self.gap_status,
            self.card_type_status,
        }
        if statuses == {STATUS_PROCESSED}:
            return STATUS_PROCESSED
        if STATUS_FAILED_PERMANENT in statuses:
            return STATUS_FAILED_PERMANENT
        return STATUS_FAILED


def _as_call_type(value) -> CallType | None:
    if value is None or isinstance(value, CallType):
        return value
    return CallType(value)


def _as_call_score(value) -> CallScore | None:
    if value is None or isinstance(value, CallScore):
        return value
    return CallScore(value)


def _as_card_type(value) -> CardType | None:
    if value is None or isinstance(value, CardType):
        return value
    return CardType(value)


def _as_gaps(value) -> list[Gap] | None:
    if value is None:
        return value
    return [g if isinstance(g, Gap) else Gap(**g) for g in value]


_STEP_KEYS = (
    ("call_type_status", "call_type_error", "call_type_retry_count"),
    ("scoring_status", "scoring_error", "scoring_retry_count"),
    ("gap_status", "gap_error", "gap_retry_count"),
    ("card_type_status", "card_type_error", "card_type_retry_count"),
)


def _prompt_hash(result) -> str | None:
    """`_run_step` hands back a ClassificationResult only for a step that ran
    and succeeded on this pass — a failed step, an already-processed one and a
    skipped one all yield None. That makes this the single place enforcing
    "provenance only for output that actually exists"."""
    return result.prompt_content_hash if result is not None else None


def _failed_step(record: dict, retry_key: str, max_retries: int, *, spend_budget: bool):
    """The failed outcome for one step: its new status and its new retry count.

    Budget is **per step**, not per row. A row-level counter meant a failure in
    scoring permanently consumed gap's chances too, so a call whose steps each
    failed twice on different nights got dead-lettered while every individual
    step still had budget left.

    `spend_budget=False` records the failure without charging for it — used when
    the gateway never answered at all this run, because dead-lettering a call
    over an outage blames the call for infrastructure. The failure is still
    recorded in `{step}_status`/`{step}_error`, so nothing becomes invisible;
    only the counter is spared.
    """
    if not spend_budget:
        return STATUS_FAILED, record[retry_key]

    next_retry_count = record[retry_key] + 1
    status = STATUS_FAILED_PERMANENT if next_retry_count > max_retries else STATUS_FAILED
    return status, next_retry_count


async def _run_step(
    record: dict,
    *,
    status_key: str,
    error_key: str,
    retry_key: str,
    max_retries: int,
    fn,
    breaker=None,
):
    """Returns (status, error, result, retry_count) for one step.

    `fn` is a zero-arg callable returning a coroutine — the plain lambdas in
    advance_analysis still work, since calling an async function just builds the
    coroutine that gets awaited here.
    """
    status = record[status_key]
    if status in (STATUS_PROCESSED, STATUS_FAILED_PERMANENT):
        return record[status_key], record[error_key], None, record[retry_key]

    try:
        result = await fn()
    except (LLMOutputError, APIError) as exc:
        # APIError covers the transport/gateway half of "this step didn't
        # work": a timeout that outlived app.llm.client's own retry loop, a
        # connection failure, or an HTTP 5xx from the gateway (which is how a
        # rejected reasoning_effort value surfaces). These aren't
        # LLMOutputError, so they used to escape this handler, propagate out
        # of process_batch and abort the whole run. They belong on the same
        # per-step status/error/retry path as a malformed response: the step
        # produced nothing usable, and the other three steps can still run.
        logger.warning("Analysis step %s failed: %s: %s", status_key, type(exc).__name__, exc)
        is_transport_failure = isinstance(exc, APIError)
        if breaker is not None:
            # Only a transport failure is evidence the gateway is gone. An
            # LLMOutputError means it answered and we rejected the content, so
            # it counts as reachable — otherwise a run of malformed responses
            # would trip the breaker on a perfectly healthy gateway.
            if is_transport_failure:
                breaker.record_gateway_unreachable(exc)
            else:
                breaker.record_gateway_reachable()

        # A transport failure only costs the step its budget if the gateway
        # answered something, somewhere in this run. Nothing answering means an
        # outage, and an outage is not this call's fault — see
        # CircuitBreaker.gateway_proved_alive.
        spend_budget = True
        if is_transport_failure and breaker is not None:
            spend_budget = breaker.gateway_proved_alive

        failed_status, retry_count = _failed_step(
            record, retry_key, max_retries, spend_budget=spend_budget
        )
        return failed_status, f"{type(exc).__name__}: {exc}", None, retry_count

    if breaker is not None:
        breaker.record_gateway_reachable()
    return STATUS_PROCESSED, None, result, record[retry_key]


async def advance_analysis(
    *,
    llm_client,
    transcript_text: str,
    call_metadata: dict,
    prompts: StepPrompts,
    record: dict,
    max_retries: int = 3,
    breaker=None,
) -> AnalysisRecord:
    """Runs one call's four analyser steps, awaiting them **in order**.

    The sequencing is not incidental: scoring and gap analysis select their
    prompt by the call type this function resolves first, and card_type reads
    the score scoring produces. Concurrency in this pipeline is one coroutine
    per call (see app/services/batch/processor.py), not one per step — so this
    function is a straight line and each call still issues at most one request
    to the gateway at a time.
    """
    call_type_status, call_type_error, call_type_result, call_type_retries = await _run_step(
        record,
        status_key="call_type_status",
        error_key="call_type_error",
        retry_key="call_type_retry_count",
        max_retries=max_retries,
        breaker=breaker,
        fn=lambda: classify_call_type(
            llm_client=llm_client, transcript=transcript_text, prompt=prompts.call_type
        ),
    )
    call_type_value = (
        call_type_result.value if call_type_result else _as_call_type(record["call_type"])
    )

    if call_type_value is not None:
        scoring_status, scoring_error, scoring_result, scoring_retries = await _run_step(
            record,
            status_key="scoring_status",
            error_key="scoring_error",
            retry_key="scoring_retry_count",
            max_retries=max_retries,
            breaker=breaker,
            fn=lambda: score_call(
                llm_client=llm_client,
                transcript=transcript_text,
                prompt=prompts.scoring_for(call_type_value),
            ),
        )
        call_score_value = (
            scoring_result.value if scoring_result else _as_call_score(record["call_score"])
        )

        gap_status, gap_error, gap_result, gap_retries = await _run_step(
            record,
            status_key="gap_status",
            error_key="gap_error",
            retry_key="gap_retry_count",
            max_retries=max_retries,
            breaker=breaker,
            fn=lambda: analyse_gaps(
                llm_client=llm_client,
                transcript=transcript_text,
                prompt=prompts.gap_rubric_for(call_type_value),
            ),
        )
        gap_value = gap_result.value if gap_result else _as_gaps(record["risk_gap_analysis"])
    else:
        scoring_status, scoring_error, call_score_value = (
            record["scoring_status"],
            record["scoring_error"],
            _as_call_score(record["call_score"]),
        )
        gap_status, gap_error, gap_value = (
            record["gap_status"],
            record["gap_error"],
            _as_gaps(record["risk_gap_analysis"]),
        )
        scoring_result = gap_result = None
        # Never ran, so they spend nothing.
        scoring_retries = record["scoring_retry_count"]
        gap_retries = record["gap_retry_count"]

    context = CardTypeContext(
        transcript=transcript_text,
        call_metadata=call_metadata or None,
        existing_score=call_score_value.value if call_score_value else None,
    )
    card_type_status, card_type_error, card_type_result, card_type_retries = await _run_step(
        record,
        status_key="card_type_status",
        error_key="card_type_error",
        retry_key="card_type_retry_count",
        max_retries=max_retries,
        breaker=breaker,
        fn=lambda: classify_card_type(
            llm_client=llm_client, context=context, prompt=prompts.card_type
        ),
    )
    card_type_value = (
        card_type_result.value if card_type_result else _as_card_type(record["card_type"])
    )

    all_statuses = (call_type_status, scoring_status, gap_status, card_type_status)
    is_fully_processed = all(s == STATUS_PROCESSED for s in all_statuses)
    retry_count = record["retry_count"] if is_fully_processed else record["retry_count"] + 1

    dead_letter_at = record["dead_letter_at"]
    if any(s == STATUS_FAILED_PERMANENT for s in all_statuses):
        dead_letter_at = dead_letter_at or True

    return AnalysisRecord(
        avoma_recording_id=record["avoma_recording_id"],
        call_type=call_type_value,
        call_score=call_score_value,
        risk_gap_analysis=gap_value,
        card_type=card_type_value,
        call_type_status=call_type_status,
        scoring_status=scoring_status,
        gap_status=gap_status,
        card_type_status=card_type_status,
        call_type_error=call_type_error,
        scoring_error=scoring_error,
        gap_error=gap_error,
        card_type_error=card_type_error,
        retry_count=retry_count,
        dead_letter_at=dead_letter_at,
        call_type_retry_count=call_type_retries,
        scoring_retry_count=scoring_retries,
        gap_retry_count=gap_retries,
        card_type_retry_count=card_type_retries,
        call_type_prompt_hash=_prompt_hash(call_type_result),
        scoring_prompt_hash=_prompt_hash(scoring_result),
        gap_rubric_hash=_prompt_hash(gap_result),
        card_type_prompt_hash=_prompt_hash(card_type_result),
    )


def fail_analysis(*, record: dict, error: str, max_retries: int = 3) -> AnalysisRecord:
    """Builds the failed AnalysisRecord for a row that couldn't get as far as
    reporting per-step outcomes at all — the transcript wouldn't validate, its
    call_storage row is gone, or something genuinely unexpected blew up.

    Every step that hasn't already finished is marked failed with `error`,
    because none of them ran: the row-level failure is recorded through the
    same status/error/retry_count/dead_letter_at machinery as a step-level one
    rather than a parallel mechanism, so it stays visible to whatever queries
    `analysis` and still dead-letters after `max_retries`. Steps that already
    succeeded (or already dead-lettered) keep their outcome, so a retry
    doesn't redo work.

    All four prompt-hash fields stay None: no step produced anything on this
    pass, so there is no provenance to claim, and None is what tells the
    persistence layer to leave any previously recorded version id untouched.
    """
    statuses: dict[str, str] = {}
    errors: dict[str, str | None] = {}
    retries: dict[str, int] = {}
    for status_key, error_key, retry_key in _STEP_KEYS:
        if record[status_key] in (STATUS_PROCESSED, STATUS_FAILED_PERMANENT):
            statuses[status_key] = record[status_key]
            errors[error_key] = record[error_key]
            retries[retry_key] = record[retry_key]
        else:
            # A row-level blow-up is a real problem with this row (an invalid
            # transcript, a missing call_storage row), not an outage, so every
            # unfinished step spends budget for it.
            statuses[status_key], retries[retry_key] = _failed_step(
                record, retry_key, max_retries, spend_budget=True
            )
            errors[error_key] = error

    dead_letter_at = record["dead_letter_at"]
    if any(status == STATUS_FAILED_PERMANENT for status in statuses.values()):
        dead_letter_at = dead_letter_at or True

    return AnalysisRecord(
        avoma_recording_id=record["avoma_recording_id"],
        call_type=_as_call_type(record["call_type"]),
        call_score=_as_call_score(record["call_score"]),
        risk_gap_analysis=_as_gaps(record["risk_gap_analysis"]),
        card_type=_as_card_type(record["card_type"]),
        retry_count=record["retry_count"] + 1,
        dead_letter_at=dead_letter_at,
        **statuses,
        **errors,
        **retries,
    )
