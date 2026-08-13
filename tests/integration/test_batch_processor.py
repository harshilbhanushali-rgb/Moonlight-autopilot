import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, update

from app.db.models import Analysis, CallStorage, PromptVersion
from app.db.session import SessionLocal
from app.domain.types import CallType
from app.llm.client import StubLLMClient
from app.prompts.registry import PromptFile, PromptRegistry
from app.services.batch.orchestrator import StepPrompts
from app.services.batch.processor import process_batch
from app.services.batch.run import _PROMPTS_ROOT, build_step_prompts

pytestmark = pytest.mark.integration

GOOD_RESPONSES = {
    "call_type": '{"call_type": "Demo"}',
    "scoring": '{"call_score": "High"}',
    "gap_analysis": '{"gaps": []}',
    "card_type": '{"card_type": "Coaching"}',
}


def _cleanup(recording_ids: list[str]) -> None:
    with SessionLocal() as session:
        session.query(Analysis).filter(Analysis.avoma_recording_id.in_(recording_ids)).delete(
            synchronize_session=False
        )
        session.query(CallStorage).filter(CallStorage.avoma_recording_id.in_(recording_ids)).delete(
            synchronize_session=False
        )
        session.commit()


@pytest.fixture
def recording_id():
    rid = f"test-{uuid.uuid4()}"
    yield rid
    _cleanup([rid])


@pytest.fixture
def new_recording_id():
    created: list[str] = []

    def make() -> str:
        rid = f"test-{uuid.uuid4()}"
        created.append(rid)
        return rid

    yield make
    _cleanup(created)


DEFAULT_SPEAKERS = [
    {"id": 0, "name": "rep", "email": "rep@joveo.com", "is_rep": True},
    {"id": 1, "name": "buyer", "email": "buyer@acme.com", "is_rep": False},
]


def seed_call_storage(
    recording_id: str,
    transcript_turns: list[dict],
    *,
    speakers: list[dict] | None = None,
) -> None:
    """`speaker_id` defaults to 0 (the rep) so existing call sites need not
    care about speaker identity. A turn may still override it, and a caller
    that omits `start_s` deliberately still produces a transcript that fails
    validation — that shape is the subject of one of these tests."""
    turns = [{"speaker_id": 0, **turn} for turn in transcript_turns]
    with SessionLocal() as session:
        session.add(
            CallStorage(
                avoma_recording_id=recording_id,
                client_record_id="test-client",
                transcript={
                    "turns": turns,
                    "speakers": DEFAULT_SPEAKERS if speakers is None else speakers,
                },
                call_metadata={},
                fetched_at=datetime.now(timezone.utc),
            )
        )
        session.commit()


@pytest.fixture(autouse=True)
def no_prompt_version_residue():
    """Every process_batch call in this module now registers prompt versions —
    including the tests that deliberately run the *real* registry content. That
    is correct behaviour but it's still test-created state, so anything
    registered during a test is removed afterwards.

    References are dropped before the rows so this doesn't depend on fixture
    teardown order; only rows this module wrote can point at these versions.
    """
    with SessionLocal() as session:
        before = session.execute(select(func.max(PromptVersion.id))).scalar() or 0

    yield

    with SessionLocal() as session:
        ids = (
            session.execute(select(PromptVersion.id).where(PromptVersion.id > before))
            .scalars()
            .all()
        )
        if not ids:
            return
        for column in (
            Analysis.call_type_prompt_version_id,
            Analysis.scoring_prompt_version_id,
            Analysis.gap_rubric_version_id,
            Analysis.card_type_prompt_version_id,
        ):
            session.execute(update(Analysis).where(column.in_(ids)).values({column: None}))
        session.query(PromptVersion).filter(PromptVersion.id.in_(ids)).delete(
            synchronize_session=False
        )
        session.commit()


@pytest.fixture
def throwaway_prompts():
    """StepPrompts whose content is unique per test, so the prompt_versions
    rows these tests register can be deleted by hash afterwards and the real
    registered prompt content is never disturbed."""
    run = uuid.uuid4()

    def prompt(kind: str, **extra) -> PromptFile:
        content = f"test {kind} prompt {run}"
        return PromptFile(
            kind=kind,
            label="v1",
            content=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            **extra,
        )

    # Rows are cleaned up by no_prompt_version_residue; content is unique here
    # so assertions can name exact hashes without colliding with real prompts.
    return StepPrompts(
        call_type=prompt("call_type"),
        scoring_for=lambda call_type: prompt("scoring", call_type=call_type.name.lower()),
        card_type=prompt("card_type"),
        gap_rubric_for=lambda call_type: prompt(
            "gap_rubric", call_type=call_type.name.lower(), mode="descriptiononly"
        ),
    )


def version_ids(recording_id: str) -> dict[str, int | None]:
    with SessionLocal() as session:
        row = session.query(Analysis).filter_by(avoma_recording_id=recording_id).one()
        return {
            "call_type": row.call_type_prompt_version_id,
            "scoring": row.scoring_prompt_version_id,
            "gap": row.gap_rubric_version_id,
            "card_type": row.card_type_prompt_version_id,
        }


async def test_a_fully_processed_call_records_a_prompt_version_for_every_step(
    recording_id, throwaway_prompts
):
    """The end-to-end chain the provenance design promised: prompt file ->
    content hash -> prompt_versions row -> FK on the analysis row."""
    seed_call_storage(recording_id, [{"speaker": "rep", "text": "hi there", "start_s": 0.0}])
    llm = StubLLMClient(responses=GOOD_RESPONSES)

    with SessionLocal() as session:
        await process_batch(session=session, llm_client=llm, prompts=throwaway_prompts, limit=100)

    ids = version_ids(recording_id)
    assert all(v is not None for v in ids.values()), ids

    expected = throwaway_prompts.by_content_hash(CallType.DEMO)
    with SessionLocal() as session:
        rows = {
            step: session.get(PromptVersion, version_id) for step, version_id in ids.items()
        }
    assert {row.content_hash for row in rows.values()} == set(expected)
    assert rows["call_type"].kind == "call_type"
    assert rows["scoring"].kind == "scoring"
    assert rows["gap"].kind == "gap_rubric"
    assert rows["card_type"].kind == "card_type"


async def test_a_failed_step_leaves_its_prompt_version_null(recording_id, throwaway_prompts):
    """Correctness rule (a), end to end: no lineage is claimed for a step that
    produced nothing, while the steps that did succeed are still attributed."""
    seed_call_storage(recording_id, [{"speaker": "rep", "text": "hi there", "start_s": 0.0}])
    responses = dict(GOOD_RESPONSES)
    responses["scoring"] = "not valid json"
    llm = StubLLMClient(responses=responses)

    with SessionLocal() as session:
        await process_batch(session=session, llm_client=llm, prompts=throwaway_prompts, limit=100)

    ids = version_ids(recording_id)
    assert ids["scoring"] is None
    assert ids["call_type"] is not None
    assert ids["gap"] is not None
    assert ids["card_type"] is not None


async def test_a_retry_does_not_null_the_versions_recorded_by_the_first_pass(
    recording_id, throwaway_prompts
):
    """Correctness rule (b), end to end. persist_analysis_result upserts in
    place, so the second pass — where call_type/gap/card_type are already
    `processed` and therefore skipped — must leave their version ids alone
    instead of overwriting them with NULL.
    """
    seed_call_storage(recording_id, [{"speaker": "rep", "text": "hi there", "start_s": 0.0}])
    failing = dict(GOOD_RESPONSES)
    failing["scoring"] = "not valid json"

    with SessionLocal() as session:
        await process_batch(
            session=session,
            llm_client=StubLLMClient(responses=failing),
            prompts=throwaway_prompts,
            limit=100,
        )
    after_first_pass = version_ids(recording_id)
    assert after_first_pass["scoring"] is None

    # Second pass: the row is `failed`, so it gets re-claimed. Only scoring
    # actually runs this time.
    with SessionLocal() as session:
        await process_batch(
            session=session,
            llm_client=StubLLMClient(responses=GOOD_RESPONSES),
            prompts=throwaway_prompts,
            limit=100,
        )
    after_second_pass = version_ids(recording_id)

    assert after_second_pass["scoring"] is not None
    for step in ("call_type", "gap", "card_type"):
        assert after_second_pass[step] == after_first_pass[step]

    with SessionLocal() as session:
        row = session.query(Analysis).filter_by(avoma_recording_id=recording_id).one()
        assert row.status == "processed"


async def test_two_calls_analysed_with_the_same_prompts_share_one_prompt_version_row(
    new_recording_id, throwaway_prompts
):
    first = new_recording_id()
    second = new_recording_id()
    seed_call_storage(first, [{"speaker": "rep", "text": "hi there", "start_s": 0.0}])
    seed_call_storage(second, [{"speaker": "rep", "text": "hi there", "start_s": 0.0}])

    with SessionLocal() as session:
        await process_batch(
            session=session,
            llm_client=StubLLMClient(responses=GOOD_RESPONSES),
            prompts=throwaway_prompts,
            limit=100,
        )

    assert version_ids(first) == version_ids(second)


async def test_process_batch_fully_processes_a_new_call(recording_id):
    seed_call_storage(recording_id, [{"speaker": "rep", "text": "hi there", "start_s": 0.0}])
    llm = StubLLMClient(responses=GOOD_RESPONSES)
    registry = PromptRegistry(root=_PROMPTS_ROOT)
    prompts = build_step_prompts(registry, "descriptiononly")

    with SessionLocal() as session:
        await process_batch(session=session, llm_client=llm, prompts=prompts, limit=100)

    with SessionLocal() as session:
        row = session.query(Analysis).filter_by(avoma_recording_id=recording_id).one()
        assert row.status == "processed"
        assert row.call_type == "Demo"
        assert row.call_score == "High"
        assert row.card_type == "Coaching"
        assert row.risk_gap_analysis == []


async def test_process_batch_does_not_reprocess_an_already_processed_row(recording_id):
    seed_call_storage(recording_id, [{"speaker": "rep", "text": "hi there", "start_s": 0.0}])
    llm = StubLLMClient(responses=GOOD_RESPONSES)
    registry = PromptRegistry(root=_PROMPTS_ROOT)
    prompts = build_step_prompts(registry, "descriptiononly")

    with SessionLocal() as session:
        await process_batch(session=session, llm_client=llm, prompts=prompts, limit=100)

    calls_after_first_run = len(llm.calls)

    with SessionLocal() as session:
        await process_batch(session=session, llm_client=llm, prompts=prompts, limit=100)

    assert len(llm.calls) == calls_after_first_run


async def test_process_batch_marks_failing_step_as_failed_and_retryable(recording_id):
    seed_call_storage(recording_id, [{"speaker": "rep", "text": "hi there", "start_s": 0.0}])
    bad_responses = dict(GOOD_RESPONSES)
    bad_responses["scoring"] = "not valid json"
    llm = StubLLMClient(responses=bad_responses)
    registry = PromptRegistry(root=_PROMPTS_ROOT)
    prompts = build_step_prompts(registry, "descriptiononly")

    with SessionLocal() as session:
        await process_batch(session=session, llm_client=llm, prompts=prompts, limit=100)

    with SessionLocal() as session:
        row = session.query(Analysis).filter_by(avoma_recording_id=recording_id).one()
        assert row.status == "failed"
        assert row.scoring_status == "failed"
        assert row.scoring_error is not None
        assert row.call_type_status == "processed"


async def test_a_row_that_cannot_be_rendered_fails_alone_and_the_batch_carries_on(new_recording_id):
    """A transcript stored before start_s became required fails validation on
    read. That used to propagate out of process_batch and abort the run,
    stranding every other row claimed in the same batch in `processing`."""
    legacy_id = new_recording_id()
    healthy_id = new_recording_id()
    seed_call_storage(legacy_id, [{"speaker": "rep", "text": "hi there"}])
    seed_call_storage(healthy_id, [{"speaker": "rep", "text": "hi there", "start_s": 0.0}])
    llm = StubLLMClient(responses=GOOD_RESPONSES)
    registry = PromptRegistry(root=_PROMPTS_ROOT)
    prompts = build_step_prompts(registry, "descriptiononly")

    with SessionLocal() as session:
        attempted = await process_batch(session=session, llm_client=llm, prompts=prompts, limit=100)

    assert attempted == 2

    with SessionLocal() as session:
        legacy = session.query(Analysis).filter_by(avoma_recording_id=legacy_id).one()
        healthy = session.query(Analysis).filter_by(avoma_recording_id=healthy_id).one()

    assert legacy.status == "failed"
    assert legacy.retry_count == 1
    assert "start_s" in legacy.call_type_error
    assert healthy.status == "processed"
    assert healthy.call_type == "Demo"


async def test_a_concurrent_batch_processes_every_call_against_the_real_database(
    new_recording_id, throwaway_prompts
):
    """Several calls analysed at once, one sync Session between them.

    The design keeps every database touch on the consuming coroutine — the
    workers only do LLM calls and hand back plain records — because a sync
    Session used from two places at once corrupts its transaction state. This is
    the test that would catch that being broken, along with the prompt-version
    upsert being hit repeatedly for the same content while rows land one by one.
    """
    ids = [new_recording_id() for _ in range(5)]
    for rid in ids:
        seed_call_storage(rid, [{"speaker": "rep", "text": "hi there", "start_s": 0.0}])

    with SessionLocal() as session:
        attempted = await process_batch(
            session=session,
            llm_client=StubLLMClient(responses=GOOD_RESPONSES),
            prompts=throwaway_prompts,
            limit=100,
            max_concurrency=4,
        )

    assert attempted >= len(ids)

    with SessionLocal() as session:
        rows = (
            session.query(Analysis).filter(Analysis.avoma_recording_id.in_(ids)).all()
        )
    assert len(rows) == len(ids)
    assert all(row.status == "processed" for row in rows)
    assert all(row.call_type == "Demo" for row in rows)

    # Identical prompt content across all five, so they must share one
    # prompt_versions row per step rather than racing in duplicates.
    assert len({version_ids(rid)["call_type"] for rid in ids}) == 1
    assert all(v is not None for rid in ids for v in version_ids(rid).values())
