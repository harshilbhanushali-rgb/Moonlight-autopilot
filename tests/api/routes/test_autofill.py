import asyncio

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from app.api.deps import (
    get_card_table_client,
    get_card_type_prompt,
    get_gap_fill_prompt,
    get_llm_client,
    get_request_store,
)
from app.api.routes.autofill import router
from app.llm.client import StubLLMClient
from app.prompts.registry import PromptFile
from fastapi import FastAPI


CARD_TYPE_PROMPT = PromptFile(
    kind="card_type", label="v1", content="classify", content_hash="ct1"
)
GAP_FILL_PROMPT = PromptFile(kind="gap_fill", label="v1", content="summarize", content_hash="gf1")


class FakeRequestStore:
    def __init__(self):
        self.created = []
        self.statuses = []
        self.prompt_versions = []
        self._next_id = 1

    def create(self, card_id):
        request_id = self._next_id
        self._next_id += 1
        self.created.append((request_id, card_id))
        return request_id

    def mark_status(self, request_id, status, error_detail=None):
        self.statuses.append((request_id, status, error_detail))

    def record_prompt_versions(self, request_id, *, card_type_prompt=None, gap_fill_prompt=None):
        self.prompt_versions.append((request_id, card_type_prompt, gap_fill_prompt))


class FakeCardTableClient:
    def __init__(self):
        self.updates = []

    def update_card(self, card_id, *, type=None, gap=None):
        self.updates.append({"card_id": card_id, "type": type, "gap": gap})


def make_client(llm, request_store, card_table):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_llm_client] = lambda: llm
    app.dependency_overrides[get_request_store] = lambda: request_store
    app.dependency_overrides[get_card_table_client] = lambda: card_table
    app.dependency_overrides[get_card_type_prompt] = lambda: CARD_TYPE_PROMPT
    app.dependency_overrides[get_gap_fill_prompt] = lambda: GAP_FILL_PROMPT
    return TestClient(app)


def test_autofill_endpoint_returns_202_immediately():
    llm = StubLLMClient(responses={"card_type": '{"card_type": "Coaching"}'})
    request_store = FakeRequestStore()
    card_table = FakeCardTableClient()
    client = make_client(llm, request_store, card_table)

    response = client.post(
        "/cards/card-1/autofill",
        json={"comment_text": "rep missed discovery", "needs_type": True, "needs_gap": False},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"


def test_autofill_endpoint_runs_background_task_that_updates_card():
    llm = StubLLMClient(responses={"card_type": '{"card_type": "Coaching"}'})
    request_store = FakeRequestStore()
    card_table = FakeCardTableClient()
    client = make_client(llm, request_store, card_table)

    client.post(
        "/cards/card-1/autofill",
        json={"comment_text": "rep missed discovery", "needs_type": True, "needs_gap": False},
    )

    assert card_table.updates == [{"card_id": "card-1", "type": "Coaching", "gap": None}]
    assert request_store.statuses[-1][1] == "processed"


def test_autofill_endpoint_rejects_request_with_nothing_to_fill():
    llm = StubLLMClient(responses={})
    request_store = FakeRequestStore()
    card_table = FakeCardTableClient()
    client = make_client(llm, request_store, card_table)

    response = client.post(
        "/cards/card-1/autofill",
        json={"comment_text": "x", "needs_type": False, "needs_gap": False},
    )

    assert response.status_code == 400
    assert card_table.updates == []


def test_autofill_endpoint_creates_audit_row_before_ack():
    llm = StubLLMClient(responses={"card_type": '{"card_type": "Coaching"}'})
    request_store = FakeRequestStore()
    card_table = FakeCardTableClient()
    client = make_client(llm, request_store, card_table)

    client.post(
        "/cards/card-9/autofill",
        json={"comment_text": "x", "needs_type": True, "needs_gap": False},
    )

    assert request_store.created == [(1, "card-9")]


def test_the_background_task_handed_to_starlette_is_sync_so_it_runs_off_the_loop(monkeypatch):
    """The autofill work must never be scheduled as a coroutine.

    Starlette runs a sync background callable in its threadpool but an async one
    directly on the event loop, and this work is mostly *blocking* despite the
    async LLM calls inside it — the request store uses a sync SQLAlchemy session
    and the card table client a sync HTTP call. Handing over a coroutine
    function would stall the API for the length of every autofill, which is
    exactly the regression that turning the domain async invites.
    """
    captured = []
    original_add_task = BackgroundTasks.add_task

    def capturing_add_task(self, func, *args, **kwargs):
        captured.append(func)
        return original_add_task(self, func, *args, **kwargs)

    monkeypatch.setattr(BackgroundTasks, "add_task", capturing_add_task)

    llm = StubLLMClient(responses={"card_type": '{"card_type": "Coaching"}'})
    card_table = FakeCardTableClient()
    client = make_client(llm, FakeRequestStore(), card_table)

    client.post(
        "/cards/card-10/autofill",
        json={"comment_text": "x", "needs_type": True, "needs_gap": False},
    )

    assert len(captured) == 1
    assert not asyncio.iscoroutinefunction(captured[0])
    # And it still did the work, so "sync" isn't achieved by not running.
    assert card_table.updates == [{"card_id": "card-10", "type": "Coaching", "gap": None}]
