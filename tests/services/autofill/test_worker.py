import pytest

from app.services.autofill.worker import AutofillTask, run_autofill, run_autofill_blocking
from app.llm.client import StubLLMClient
from app.prompts.registry import PromptFile


CARD_TYPE_PROMPT = PromptFile(
    kind="card_type", label="v1", content="classify card type", content_hash="ct1"
)
GAP_FILL_PROMPT = PromptFile(
    kind="gap_fill", label="v1", content="summarize gap", content_hash="gf1"
)


class FakeRequestStore:
    def __init__(self):
        self.statuses = []
        self.prompt_versions = []

    def mark_status(self, request_id, status, error_detail=None):
        self.statuses.append((request_id, status, error_detail))

    def record_prompt_versions(self, request_id, *, card_type_prompt=None, gap_fill_prompt=None):
        self.prompt_versions.append((request_id, card_type_prompt, gap_fill_prompt))


class FakeCardTableClient:
    def __init__(self):
        self.updates = []

    def update_card(self, card_id, *, type=None, gap=None):
        self.updates.append({"card_id": card_id, "type": type, "gap": gap})


async def test_run_autofill_fills_only_type_when_only_type_is_blank():
    llm = StubLLMClient(responses={"card_type": '{"card_type": "Coaching"}'})
    request_store = FakeRequestStore()
    card_table = FakeCardTableClient()
    task = AutofillTask(card_id="card-1", comment_text="rep missed discovery", needs_type=True, needs_gap=False)

    await run_autofill(
        llm_client=llm,
        card_type_prompt=CARD_TYPE_PROMPT,
        gap_fill_prompt=GAP_FILL_PROMPT,
        task=task,
        request_id=1,
        request_store=request_store,
        card_table_client=card_table,
    )

    assert card_table.updates == [{"card_id": "card-1", "type": "Coaching", "gap": None}]
    assert request_store.statuses[-1] == (1, "processed", None)


async def test_run_autofill_fills_only_gap_when_only_gap_is_blank():
    llm = StubLLMClient(responses={"gap_fill": '{"gap": "Skipped discovery"}'})
    request_store = FakeRequestStore()
    card_table = FakeCardTableClient()
    task = AutofillTask(card_id="card-2", comment_text="rep missed discovery", needs_type=False, needs_gap=True)

    await run_autofill(
        llm_client=llm,
        card_type_prompt=CARD_TYPE_PROMPT,
        gap_fill_prompt=GAP_FILL_PROMPT,
        task=task,
        request_id=2,
        request_store=request_store,
        card_table_client=card_table,
    )

    assert card_table.updates == [{"card_id": "card-2", "type": None, "gap": "Skipped discovery"}]
    assert request_store.statuses[-1] == (2, "processed", None)


async def test_run_autofill_fills_both_type_and_gap_in_one_update():
    llm = StubLLMClient(
        responses={
            "card_type": '{"card_type": "Risk"}',
            "gap_fill": '{"gap": "Rep conceded on price"}',
        }
    )
    request_store = FakeRequestStore()
    card_table = FakeCardTableClient()
    task = AutofillTask(card_id="card-3", comment_text="conceded on price", needs_type=True, needs_gap=True)

    await run_autofill(
        llm_client=llm,
        card_type_prompt=CARD_TYPE_PROMPT,
        gap_fill_prompt=GAP_FILL_PROMPT,
        task=task,
        request_id=3,
        request_store=request_store,
        card_table_client=card_table,
    )

    assert card_table.updates == [
        {"card_id": "card-3", "type": "Risk", "gap": "Rep conceded on price"}
    ]


async def test_run_autofill_marks_request_failed_and_does_not_update_card_on_llm_error():
    llm = StubLLMClient(responses={"card_type": "not json"})
    request_store = FakeRequestStore()
    card_table = FakeCardTableClient()
    task = AutofillTask(card_id="card-4", comment_text="rep missed discovery", needs_type=True, needs_gap=False)

    await run_autofill(
        llm_client=llm,
        card_type_prompt=CARD_TYPE_PROMPT,
        gap_fill_prompt=GAP_FILL_PROMPT,
        task=task,
        request_id=4,
        request_store=request_store,
        card_table_client=card_table,
    )

    assert card_table.updates == []
    status_id, status, error_detail = request_store.statuses[-1]
    assert (status_id, status) == (4, "failed")
    assert error_detail is not None


async def test_run_autofill_marks_request_processing_before_attempting_work():
    llm = StubLLMClient(responses={"card_type": '{"card_type": "Coaching"}'})
    request_store = FakeRequestStore()
    card_table = FakeCardTableClient()
    task = AutofillTask(card_id="card-5", comment_text="x", needs_type=True, needs_gap=False)

    await run_autofill(
        llm_client=llm,
        card_type_prompt=CARD_TYPE_PROMPT,
        gap_fill_prompt=GAP_FILL_PROMPT,
        task=task,
        request_id=5,
        request_store=request_store,
        card_table_client=card_table,
    )

    assert request_store.statuses[0] == (5, "processing", None)


async def test_only_the_field_that_was_filled_gets_a_prompt_version_recorded():
    """needs_gap is false, so no gap text was produced and gap_fill has no
    lineage to claim — its column must stay NULL rather than be attributed to
    a prompt that never ran."""
    llm = StubLLMClient(responses={"card_type": '{"card_type": "Coaching"}'})
    request_store = FakeRequestStore()
    task = AutofillTask(card_id="card-6", comment_text="x", needs_type=True, needs_gap=False)

    await run_autofill(
        llm_client=llm,
        card_type_prompt=CARD_TYPE_PROMPT,
        gap_fill_prompt=GAP_FILL_PROMPT,
        task=task,
        request_id=6,
        request_store=request_store,
        card_table_client=FakeCardTableClient(),
    )

    assert request_store.prompt_versions == [(6, CARD_TYPE_PROMPT, None)]


async def test_both_prompt_versions_are_recorded_when_both_fields_are_filled():
    llm = StubLLMClient(
        responses={
            "card_type": '{"card_type": "Risk"}',
            "gap_fill": '{"gap": "Rep conceded on price"}',
        }
    )
    request_store = FakeRequestStore()
    task = AutofillTask(card_id="card-7", comment_text="x", needs_type=True, needs_gap=True)

    await run_autofill(
        llm_client=llm,
        card_type_prompt=CARD_TYPE_PROMPT,
        gap_fill_prompt=GAP_FILL_PROMPT,
        task=task,
        request_id=7,
        request_store=request_store,
        card_table_client=FakeCardTableClient(),
    )

    assert request_store.prompt_versions == [(7, CARD_TYPE_PROMPT, GAP_FILL_PROMPT)]


async def test_a_failed_autofill_records_no_prompt_version():
    """gap_fill fails, so nothing was written to the card. Recording the
    card_type prompt that did succeed would attribute output that was thrown
    away."""
    llm = StubLLMClient(
        responses={"card_type": '{"card_type": "Risk"}', "gap_fill": "not json"}
    )
    request_store = FakeRequestStore()
    card_table = FakeCardTableClient()
    task = AutofillTask(card_id="card-8", comment_text="x", needs_type=True, needs_gap=True)

    await run_autofill(
        llm_client=llm,
        card_type_prompt=CARD_TYPE_PROMPT,
        gap_fill_prompt=GAP_FILL_PROMPT,
        task=task,
        request_id=8,
        request_store=request_store,
        card_table_client=card_table,
    )

    assert card_table.updates == []
    assert request_store.prompt_versions == []
    assert request_store.statuses[-1][1] == "failed"


async def test_prompt_versions_are_recorded_before_the_request_is_marked_processed():
    """A request that reads `processed` but has no attribution would be
    indistinguishable from the pre-provenance rows."""
    llm = StubLLMClient(responses={"card_type": '{"card_type": "Coaching"}'})

    order = []

    class OrderTrackingStore(FakeRequestStore):
        def mark_status(self, request_id, status, error_detail=None):
            order.append(f"status:{status}")
            super().mark_status(request_id, status, error_detail)

        def record_prompt_versions(self, request_id, **kwargs):
            order.append("prompt_versions")
            super().record_prompt_versions(request_id, **kwargs)

    await run_autofill(
        llm_client=llm,
        card_type_prompt=CARD_TYPE_PROMPT,
        gap_fill_prompt=GAP_FILL_PROMPT,
        task=AutofillTask(card_id="card-9", comment_text="x", needs_type=True, needs_gap=False),
        request_id=9,
        request_store=OrderTrackingStore(),
        card_table_client=FakeCardTableClient(),
    )

    assert order == ["status:processing", "prompt_versions", "status:processed"]


def test_run_autofill_blocking_drives_the_coroutine_from_a_thread_with_no_loop():
    """Deliberately a *sync* test, matching how Starlette calls it: this is the
    entrypoint the API hands over, and it owns an event loop for the LLM leg so
    the caller's thread needs none. An async test could not exercise it at all —
    asyncio.run refuses to nest inside a running loop."""
    llm = StubLLMClient(responses={"card_type": '{"card_type": "Coaching"}'})
    request_store = FakeRequestStore()
    card_table = FakeCardTableClient()

    run_autofill_blocking(
        llm_client=llm,
        card_type_prompt=CARD_TYPE_PROMPT,
        gap_fill_prompt=GAP_FILL_PROMPT,
        task=AutofillTask(card_id="card-10", comment_text="x", needs_type=True, needs_gap=False),
        request_id=10,
        request_store=request_store,
        card_table_client=card_table,
    )

    assert card_table.updates == [{"card_id": "card-10", "type": "Coaching", "gap": None}]
    assert request_store.statuses[-1] == (10, "processed", None)
