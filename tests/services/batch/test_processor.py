"""Per-row isolation and concurrency in the batch processor.

A row whose processing blows up in a way the per-step handlers don't cover
(a legacy transcript that fails contract validation, a gateway HTTP 500, a
missing call_storage row) used to propagate out of process_batch and abort
the whole run — every other row claimed in the same batch was left in
`processing` and never retried. These tests pin the fixed behaviour: the
failing row is recorded as visibly failed and the batch keeps going.

Most tests here run at `max_concurrency=1` deliberately, which is what
`run_with` defaults to. The circuit-breaker tests in particular assert on
*exactly* how many rows were attempted before the batch stopped, and that
number is only deterministic when one call is in flight at a time — with
several, rows already started when the breaker opens are allowed to finish, so
the count depends on completion order. Pinning concurrency keeps those tests
asserting on breaker semantics rather than on scheduling luck, and it also
proves the concurrent path didn't change the sequential behaviour. The tests
that are *about* concurrency set it explicitly and assert on properties that
hold at any width.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError

from app.db.models import (
    STATUS_FAILED,
    STATUS_FAILED_PERMANENT,
    STATUS_PENDING,
    STATUS_PROCESSED,
)
from app.llm.client import StubLLMClient
from app.prompts.registry import PromptFile
from app.services.batch import repository as real_repository
from app.services.batch.circuit_breaker import GatewayUnavailableError
from app.services.batch.orchestrator import StepPrompts
from app.services.batch.processor import process_batch

_REQUEST = httpx.Request("POST", "http://gateway.internal/v1/chat/completions")


class DeadGatewayClient:
    """Every call raises the error app/llm/client.py raises once its own retry
    loop is exhausted — i.e. the gateway is unreachable, not misbehaving."""

    def __init__(self):
        self.call_count = 0

    async def complete_structured(self, **kwargs):
        self.call_count += 1
        raise APITimeoutError(request=_REQUEST)


class FlakyGatewayClient:
    """Fails every other call. The gateway is up and answering, so however many
    failures accumulate they are never *consecutive*."""

    def __init__(self, responses):
        self._stub = StubLLMClient(responses=responses)
        self.call_count = 0

    async def complete_structured(self, **kwargs):
        self.call_count += 1
        if self.call_count % 2 == 1:
            raise APITimeoutError(request=_REQUEST)
        return await self._stub.complete_structured(**kwargs)


class ConcurrencyTrackingClient:
    """Tracks in-flight requests, both overall and per call-under-analysis.

    Every row's transcript carries a unique marker (see `marked_transcript`),
    and that text reaches every one of the four steps, so the marker found in a
    request's messages identifies which call it belongs to. That is what makes
    it possible to assert the two halves of the design separately: different
    calls overlap, the steps inside one call never do.

    The `asyncio.sleep(0)` is what makes any of it observable — without a
    suspension point mid-request the event loop would run each coroutine
    start-to-finish and even a genuinely concurrent batch would look
    sequential.
    """

    def __init__(self, responses, markers):
        self._stub = StubLLMClient(responses=responses)
        self._markers = markers
        self._per_call: dict[str, int] = {}
        self.in_flight = 0
        self.max_in_flight = 0
        self.max_in_flight_per_call = 0

    def _marker_of(self, messages):
        text = " ".join(m.content for m in messages)
        found = [marker for marker in self._markers if marker in text]
        assert len(found) == 1, f"expected exactly one marker, got {found}"
        return found[0]

    async def complete_structured(self, **kwargs):
        marker = self._marker_of(kwargs["messages"])
        self.in_flight += 1
        self._per_call[marker] = self._per_call.get(marker, 0) + 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.max_in_flight_per_call = max(
            self.max_in_flight_per_call, self._per_call[marker]
        )
        try:
            await asyncio.sleep(0)
            return await self._stub.complete_structured(**kwargs)
        finally:
            self.in_flight -= 1
            self._per_call[marker] -= 1


SPEAKERS = [{"id": 0, "name": "rep", "email": "rep@joveo.com", "is_rep": True}]


def marked_transcript(marker: str) -> dict:
    return {
        "speakers": SPEAKERS,
        "turns": [
            {"speaker": "rep", "speaker_id": 0, "text": f"{marker} speaking", "start_s": 0.0}
        ],
    }

ALL_GOOD_RESPONSES = {
    "call_type": '{"call_type": "Demo"}',
    "scoring": '{"call_score": "High"}',
    "gap_analysis": '{"gaps": []}',
    "card_type": '{"card_type": "Coaching"}',
}

GOOD_TRANSCRIPT = {
    "speakers": SPEAKERS,
    "turns": [{"speaker": "rep", "speaker_id": 0, "text": "hi there", "start_s": 0.0}],
}
# The pre-timestamp shape: stored before start_s became required, so it
# fails Transcript validation on read.
LEGACY_TRANSCRIPT = {"turns": [{"speaker": "rep", "text": "hi there"}]}


def make_prompts():
    gap_prompt = PromptFile(
        kind="gap_rubric",
        call_type="Demo",
        mode="descriptiononly",
        label="v1",
        content="rubric",
        content_hash="h4",
    )
    return StepPrompts(
        call_type=PromptFile(kind="call_type", label="v1", content="ct", content_hash="h1"),
        scoring_for=lambda call_type: PromptFile(
            kind="scoring", label="v1", content="sc", content_hash="h2"
        ),
        card_type=PromptFile(kind="card_type", label="v1", content="cd", content_hash="h3"),
        gap_rubric_for=lambda call_type: gap_prompt,
    )


def make_analysis_row(recording_id: str, **overrides):
    values = dict(
        avoma_recording_id=recording_id,
        call_type=None,
        call_score=None,
        risk_gap_analysis=None,
        card_type=None,
        call_type_status=STATUS_PENDING,
        scoring_status=STATUS_PENDING,
        gap_status=STATUS_PENDING,
        card_type_status=STATUS_PENDING,
        call_type_error=None,
        scoring_error=None,
        gap_error=None,
        card_type_error=None,
        retry_count=0,
        call_type_retry_count=0,
        scoring_retry_count=0,
        gap_retry_count=0,
        card_type_retry_count=0,
        dead_letter_at=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeRepository:
    """Stubs only the DB-touching helpers. Transcript rendering and the
    row->dict shaping are the real implementations, so the failure under
    test is a real pydantic ValidationError, not a synthetic one."""

    load_transcript = staticmethod(real_repository.load_transcript)
    analysis_row_to_record_dict = staticmethod(real_repository.analysis_row_to_record_dict)

    def __init__(self, transcripts_by_id: dict, rows: list | None = None):
        self._transcripts = transcripts_by_id
        self.claimed = rows if rows is not None else [
            make_analysis_row(rid) for rid in transcripts_by_id
        ]
        self.persisted = []
        self.prompts_by_hash = []
        self.released = []

    def seed_missing_analysis_rows(self, session):
        return 0

    def claim_rows(self, session, limit, **kwargs):
        return self.claimed

    def get_call_storage_map(self, session, avoma_recording_ids):
        return {
            rid: SimpleNamespace(transcript=self._transcripts[rid], call_metadata={})
            for rid in avoma_recording_ids
            if rid in self._transcripts
        }

    def persist_analysis_result(self, session, record, *, prompts_by_hash=None):
        self.persisted.append(record)
        self.prompts_by_hash.append(prompts_by_hash)

    def release_claims(self, session, avoma_recording_ids):
        self.released = list(avoma_recording_ids)
        return len(self.released)


class FakeSession:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


async def run_with(
    monkeypatch,
    repo,
    *,
    max_retries=3,
    llm_client=None,
    breaker_threshold=6,
    max_concurrency=1,
):
    """`max_concurrency` defaults to 1 so the assertions in this module stay
    deterministic — see the module docstring. Tests about concurrency pass it
    explicitly."""
    monkeypatch.setattr("app.services.batch.processor.repository", repo)
    return await process_batch(
        session=FakeSession(),
        llm_client=llm_client or StubLLMClient(responses=ALL_GOOD_RESPONSES),
        prompts=make_prompts(),
        limit=10,
        max_retries=max_retries,
        circuit_breaker_threshold=breaker_threshold,
        max_concurrency=max_concurrency,
    )


def persisted_by_id(repo) -> dict:
    return {record.avoma_recording_id: record for record in repo.persisted}


def marked_repo(count: int) -> tuple:
    markers = [f"rec-{i}" for i in range(count)]
    return FakeRepository({m: marked_transcript(m) for m in markers}), markers


# --- concurrency -------------------------------------------------------------
#
# The point of the whole change: several calls are analysed at once, while the
# four steps inside any one call stay sequential because they depend on each
# other's output.


async def test_several_calls_are_analysed_at_the_same_time(monkeypatch):
    repo, markers = marked_repo(6)
    client = ConcurrencyTrackingClient(ALL_GOOD_RESPONSES, markers)

    attempted = await run_with(monkeypatch, repo, llm_client=client, max_concurrency=3)

    assert attempted == 6
    # Would be 1 if the batch were still walking the rows one at a time.
    assert client.max_in_flight > 1


async def test_no_more_than_max_concurrency_calls_are_ever_in_flight(monkeypatch):
    repo, markers = marked_repo(8)
    client = ConcurrencyTrackingClient(ALL_GOOD_RESPONSES, markers)

    await run_with(monkeypatch, repo, llm_client=client, max_concurrency=3)

    # The gateway's rate limits are the reason this bound has to hold, not just
    # trend in the right direction.
    assert client.max_in_flight <= 3


async def test_the_four_steps_of_one_call_never_overlap_each_other(monkeypatch):
    """Scoring and gap analysis pick their prompt using the call type resolved
    by step one, and card_type reads the score. Overlapping them would mean
    running a step before its input exists."""
    repo, markers = marked_repo(4)
    client = ConcurrencyTrackingClient(ALL_GOOD_RESPONSES, markers)

    await run_with(monkeypatch, repo, llm_client=client, max_concurrency=4)

    assert client.max_in_flight > 1  # calls did overlap...
    assert client.max_in_flight_per_call == 1  # ...but never within one call


async def test_concurrency_of_one_still_processes_every_row(monkeypatch):
    repo, markers = marked_repo(4)
    client = ConcurrencyTrackingClient(ALL_GOOD_RESPONSES, markers)

    attempted = await run_with(monkeypatch, repo, llm_client=client, max_concurrency=1)

    assert attempted == 4
    assert client.max_in_flight == 1


async def test_a_concurrent_batch_still_isolates_the_row_that_fails(monkeypatch):
    repo = FakeRepository(
        {
            "rec-a": marked_transcript("rec-a"),
            "rec-legacy": LEGACY_TRANSCRIPT,
            "rec-b": marked_transcript("rec-b"),
        }
    )

    attempted = await run_with(monkeypatch, repo, max_concurrency=3)

    assert attempted == 3
    records = persisted_by_id(repo)
    assert records["rec-legacy"].overall_status == STATUS_FAILED
    assert records["rec-a"].overall_status == STATUS_PROCESSED
    assert records["rec-b"].overall_status == STATUS_PROCESSED


async def test_a_dead_gateway_stops_a_concurrent_batch_early_too(monkeypatch):
    """The breaker still has to cut a concurrent batch short. How many rows get
    attempted first is not exact — rows already in flight when it opens are let
    finish — so this asserts the properties that hold at any width instead of a
    count."""
    repo = FakeRepository({f"rec-{i}": GOOD_TRANSCRIPT for i in range(12)})

    with pytest.raises(GatewayUnavailableError):
        await run_with(
            monkeypatch,
            repo,
            llm_client=DeadGatewayClient(),
            breaker_threshold=6,
            max_concurrency=3,
        )

    attempted = set(persisted_by_id(repo))
    released = set(repo.released)
    assert attempted, "the breaker must not fire before anything was tried"
    assert released, "rows never started must be handed back, not stranded"
    # Every claimed row ends up in exactly one of the two buckets: nothing is
    # left in `processing` for stale reclamation to pick up hours later, and
    # nothing is both recorded as failed and released for a fresh attempt.
    assert attempted | released == {f"rec-{i}" for i in range(12)}
    assert not (attempted & released)
    assert len(attempted) < 12


async def test_a_row_that_raises_does_not_stop_later_rows_from_being_attempted(monkeypatch):
    repo = FakeRepository(
        {
            "rec-ok-before": GOOD_TRANSCRIPT,
            "rec-legacy": LEGACY_TRANSCRIPT,
            "rec-ok-after": GOOD_TRANSCRIPT,
        }
    )

    attempted = await run_with(monkeypatch, repo)

    assert attempted == 3
    records = persisted_by_id(repo)
    assert set(records) == {"rec-ok-before", "rec-legacy", "rec-ok-after"}
    assert records["rec-ok-after"].overall_status == STATUS_PROCESSED
    assert records["rec-ok-after"].call_type.value == "Demo"


async def test_a_row_that_raises_is_recorded_as_visibly_failed_with_the_error_text(monkeypatch):
    repo = FakeRepository({"rec-legacy": LEGACY_TRANSCRIPT})

    await run_with(monkeypatch, repo)

    record = persisted_by_id(repo)["rec-legacy"]
    assert record.overall_status == STATUS_FAILED
    assert record.call_type_status == STATUS_FAILED
    assert record.scoring_status == STATUS_FAILED
    assert record.gap_status == STATUS_FAILED
    assert record.card_type_status == STATUS_FAILED
    assert "start_s" in record.call_type_error
    assert record.retry_count == 1
    assert record.dead_letter_at is None


async def test_a_row_that_keeps_raising_is_dead_lettered_after_max_retries(monkeypatch):
    # Escalation reads the PER-STEP counters, not the row-level one.
    repo = FakeRepository(
        {"rec-legacy": LEGACY_TRANSCRIPT},
        rows=[
            make_analysis_row(
                "rec-legacy",
                retry_count=2,
                call_type_retry_count=2,
                scoring_retry_count=2,
                gap_retry_count=2,
                card_type_retry_count=2,
            )
        ],
    )

    await run_with(monkeypatch, repo, max_retries=2)

    record = persisted_by_id(repo)["rec-legacy"]
    assert record.call_type_status == STATUS_FAILED_PERMANENT
    assert record.overall_status == STATUS_FAILED_PERMANENT
    assert record.dead_letter_at is not None


async def test_steps_already_processed_are_not_reset_by_a_row_level_failure(monkeypatch):
    repo = FakeRepository(
        {"rec-legacy": LEGACY_TRANSCRIPT},
        rows=[
            make_analysis_row(
                "rec-legacy",
                call_type="Demo",
                call_type_status=STATUS_PROCESSED,
            )
        ],
    )

    await run_with(monkeypatch, repo)

    record = persisted_by_id(repo)["rec-legacy"]
    assert record.call_type_status == STATUS_PROCESSED
    assert record.call_type_error is None
    assert record.scoring_status == STATUS_FAILED
    assert record.overall_status == STATUS_FAILED


async def test_a_claimed_row_with_no_call_storage_row_is_recorded_as_failed_not_left_claimed(monkeypatch):
    repo = FakeRepository({}, rows=[make_analysis_row("rec-orphan")])

    attempted = await run_with(monkeypatch, repo)

    assert attempted == 1
    record = persisted_by_id(repo)["rec-orphan"]
    assert record.overall_status == STATUS_FAILED
    assert "call_storage" in record.call_type_error


async def test_a_healthy_batch_still_processes_every_row(monkeypatch):
    repo = FakeRepository({"rec-a": GOOD_TRANSCRIPT, "rec-b": GOOD_TRANSCRIPT})

    attempted = await run_with(monkeypatch, repo)

    assert attempted == 2
    assert all(r.overall_status == STATUS_PROCESSED for r in repo.persisted)


async def test_persist_receives_the_prompt_content_for_every_hash_on_the_record(monkeypatch):
    """The repository resolves hash -> prompt_versions.id, so it needs the
    content behind each hash the record carries. A hash with no PromptFile
    would mean unrecordable provenance."""
    repo = FakeRepository({"rec-a": GOOD_TRANSCRIPT})

    await run_with(monkeypatch, repo)

    record = repo.persisted[0]
    lookup = repo.prompts_by_hash[0]
    hashes = {
        record.call_type_prompt_hash,
        record.scoring_prompt_hash,
        record.gap_rubric_hash,
        record.card_type_prompt_hash,
    }
    assert None not in hashes
    assert hashes <= set(lookup)
    assert all(lookup[h].content_hash == h for h in hashes)


async def test_a_dead_gateway_stops_the_batch_instead_of_grinding_through_every_row(monkeypatch):
    """The point of the breaker. Per-row isolation means a dead gateway would
    otherwise burn timeout x (max_retries+1) on every step of every row."""
    repo = FakeRepository({f"rec-{i}": GOOD_TRANSCRIPT for i in range(6)})
    client = DeadGatewayClient()

    with pytest.raises(GatewayUnavailableError) as exc_info:
        await run_with(monkeypatch, repo, llm_client=client, breaker_threshold=6)

    # A fresh row contributes only 2 failures, not 4: when call_type fails there
    # is no call_type to choose a scoring prompt or gap rubric with, so those
    # two steps are skipped. 6 consecutive failures therefore lands in row 3.
    assert len(repo.persisted) == 3
    assert repo.released == ["rec-3", "rec-4", "rec-5"]
    assert "unreachable" in str(exc_info.value)
    assert "3 claim(s) released" in str(exc_info.value)


async def test_rows_attempted_before_the_breaker_tripped_keep_their_failure_state(monkeypatch):
    repo = FakeRepository({f"rec-{i}": GOOD_TRANSCRIPT for i in range(6)})

    with pytest.raises(GatewayUnavailableError):
        await run_with(monkeypatch, repo, llm_client=DeadGatewayClient(), breaker_threshold=6)

    # Their failures were real and are recorded as such — the breaker doesn't
    # retroactively excuse them.
    for record in repo.persisted:
        assert record.overall_status == STATUS_FAILED
        assert "APITimeoutError" in record.call_type_error
        assert record.retry_count == 1


async def test_a_retried_row_can_contribute_four_failures_so_the_threshold_must_exceed_four(monkeypatch):
    """A row whose call_type is already known from an earlier pass runs all four
    steps, so one row alone can produce 4 consecutive failures. This is why the
    default threshold is > 4 — at 4 a single bad call could halt a batch."""
    repo = FakeRepository(
        {"rec-known": GOOD_TRANSCRIPT},
        rows=[make_analysis_row("rec-known", call_type="Demo")],
    )
    client = DeadGatewayClient()

    attempted = await run_with(monkeypatch, repo, llm_client=client, breaker_threshold=6)

    assert attempted == 1
    assert client.call_count == 4  # all four steps ran
    assert repo.released == []  # 4 < 6, so the breaker stayed closed


async def test_malformed_responses_never_trip_the_breaker(monkeypatch):
    """A gateway that answers with content we reject is alive. Counting those
    would let bad prompt output halt an otherwise healthy batch."""
    repo = FakeRepository({f"rec-{i}": GOOD_TRANSCRIPT for i in range(6)})
    # EVERY step must fail, so LLMOutputErrors accumulate with no success in
    # between to reset the count. If they were counted as transport failures
    # this would trip well before the 6th row. Valid JSON, wrong enum values ->
    # StructuredOutputError -> LLMOutputError.
    all_bad = {
        "call_type": '{"call_type": "Brainstorm"}',
        "scoring": '{"call_score": "Excellent"}',
        "gap_analysis": '{"gaps": [{"theme": "t"}]}',
        "card_type": '{"card_type": "Neutral"}',
    }

    attempted = await run_with(
        monkeypatch, repo, llm_client=StubLLMClient(responses=all_bad), breaker_threshold=6
    )

    assert attempted == 6
    assert repo.released == []
    assert all(r.call_type_status == STATUS_FAILED for r in repo.persisted)
    assert all(r.card_type_status == STATUS_FAILED for r in repo.persisted)


async def test_an_intermittently_failing_gateway_does_not_trip_the_breaker(monkeypatch):
    repo = FakeRepository({f"rec-{i}": GOOD_TRANSCRIPT for i in range(6)})

    attempted = await run_with(
        monkeypatch,
        repo,
        llm_client=FlakyGatewayClient(ALL_GOOD_RESPONSES),
        breaker_threshold=6,
    )

    assert attempted == 6
    assert repo.released == []


async def test_the_breaker_still_raises_when_it_opens_on_the_very_last_row(monkeypatch):
    """Nothing is left to release, but the run must not report success — a night
    where the gateway was down and every row failed would otherwise be recorded
    as a completed run in the scheduled_run ledger."""
    repo = FakeRepository({f"rec-{i}": GOOD_TRANSCRIPT for i in range(3)})

    with pytest.raises(GatewayUnavailableError):
        await run_with(monkeypatch, repo, llm_client=DeadGatewayClient(), breaker_threshold=6)

    assert len(repo.persisted) == 3  # all three were attempted
    assert repo.released == []  # nothing left to hand back


async def test_the_breaker_can_be_disabled(monkeypatch):
    repo = FakeRepository({f"rec-{i}": GOOD_TRANSCRIPT for i in range(6)})

    attempted = await run_with(monkeypatch, repo, llm_client=DeadGatewayClient(), breaker_threshold=0)

    assert attempted == 6  # grinds through everything, as before the breaker
    assert repo.released == []


async def test_a_healthy_batch_is_unaffected_by_the_breaker(monkeypatch):
    repo = FakeRepository({f"rec-{i}": GOOD_TRANSCRIPT for i in range(6)})

    attempted = await run_with(monkeypatch, repo, breaker_threshold=6)

    assert attempted == 6
    assert repo.released == []
    assert all(r.overall_status == STATUS_PROCESSED for r in repo.persisted)


async def test_one_steps_failures_do_not_drain_another_steps_retry_budget(monkeypatch):
    """Case A. With a single row-level counter, scoring failing on three earlier
    nights would leave gap with nothing left even though gap had never failed.
    Each step now carries its own budget."""
    repo = FakeRepository(
        {"rec-a": GOOD_TRANSCRIPT},
        rows=[
            make_analysis_row(
                "rec-a",
                call_type="Demo",
                call_type_status=STATUS_PROCESSED,
                # Three earlier nights of scoring failures drained the shared
                # row-level counter to its limit...
                retry_count=3,
                scoring_retry_count=3,
                # ...but gap itself has never failed once.
                gap_retry_count=0,
            )
        ],
    )
    # Only gap fails this pass.
    responses = dict(ALL_GOOD_RESPONSES, gap_analysis='{"gaps": [{"theme": "t"}]}')

    await run_with(
        monkeypatch,
        repo,
        llm_client=StubLLMClient(responses=responses),
        max_retries=3,
    )

    record = persisted_by_id(repo)["rec-a"]
    assert record.gap_status == STATUS_FAILED  # NOT failed_permanent
    assert record.gap_retry_count == 1
    assert record.scoring_retry_count == 3  # untouched: it succeeded this pass
    assert record.dead_letter_at is None


async def test_a_step_dies_only_once_its_own_budget_is_gone_and_that_kills_the_row(monkeypatch):
    """The user's explicit choice: no partial completion. Once any step is
    permanently failed the whole row is dead — overall_status goes
    failed_permanent, which claim_rows no longer picks up."""
    repo = FakeRepository(
        {"rec-a": GOOD_TRANSCRIPT},
        rows=[
            make_analysis_row(
                "rec-a",
                call_type="Demo",
                call_type_status=STATUS_PROCESSED,
                gap_retry_count=3,
            )
        ],
    )
    responses = dict(ALL_GOOD_RESPONSES, gap_analysis='{"gaps": [{"theme": "t"}]}')

    await run_with(monkeypatch, repo, llm_client=StubLLMClient(responses=responses), max_retries=3)

    record = persisted_by_id(repo)["rec-a"]
    assert record.gap_status == STATUS_FAILED_PERMANENT
    assert record.gap_retry_count == 4
    assert record.overall_status == STATUS_FAILED_PERMANENT
    assert record.dead_letter_at is not None


async def test_an_outage_does_not_spend_any_step_budget(monkeypatch):
    """Case B. Nothing answered all run, so the gateway is down and these calls
    are bystanders. Four nights of this must not dead-letter them."""
    repo = FakeRepository({f"rec-{i}": GOOD_TRANSCRIPT for i in range(3)})

    with pytest.raises(GatewayUnavailableError):
        await run_with(monkeypatch, repo, llm_client=DeadGatewayClient(), breaker_threshold=6)

    for record in repo.persisted:
        assert record.call_type_status == STATUS_FAILED
        assert record.call_type_retry_count == 0
        assert record.card_type_retry_count == 0
        assert record.dead_letter_at is None


async def test_a_step_specific_gateway_failure_does_spend_budget(monkeypatch):
    """Case C. The gateway answered other steps, so a failure isolated to one
    step is that step's own problem and must be able to exhaust its budget —
    otherwise a permanently broken step retries silently forever."""

    class OneStepDeadClient:
        def __init__(self, responses):
            self._stub = StubLLMClient(responses=responses)

        def complete_structured(self, **kwargs):
            if kwargs.get("response_key") == "gap_analysis":
                raise APITimeoutError(request=_REQUEST)
            return self._stub.complete_structured(**kwargs)

    repo = FakeRepository({"rec-a": GOOD_TRANSCRIPT})

    await run_with(
        monkeypatch,
        repo,
        llm_client=OneStepDeadClient(ALL_GOOD_RESPONSES),
        breaker_threshold=6,
    )

    record = persisted_by_id(repo)["rec-a"]
    assert record.gap_status == STATUS_FAILED
    assert record.gap_retry_count == 1  # charged, because the gateway was alive
    assert record.call_type_status == STATUS_PROCESSED


async def test_prompt_lookup_omits_call_type_scoped_prompts_when_call_type_is_unknown(monkeypatch):
    """A row that never resolved a call_type has no scoring prompt or gap
    rubric to name — asking StepPrompts for them would be guessing."""
    repo = FakeRepository({}, rows=[make_analysis_row("rec-orphan")])

    await run_with(monkeypatch, repo)

    lookup = repo.prompts_by_hash[0]
    assert {p.kind for p in lookup.values()} == {"call_type", "card_type"}
