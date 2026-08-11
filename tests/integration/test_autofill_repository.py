import hashlib
import uuid

import pytest
from sqlalchemy import select, update

from app.db.models import AutofillRequest, PromptVersion
from app.db.session import SessionLocal
from app.prompts.registry import PromptFile
from app.services.autofill.repository import SqlAutofillRequestStore

pytestmark = pytest.mark.integration


@pytest.fixture
def cleanup_ids():
    ids = []
    yield ids
    if ids:
        with SessionLocal() as session:
            session.query(AutofillRequest).filter(AutofillRequest.id.in_(ids)).delete(
                synchronize_session=False
            )
            session.commit()


@pytest.fixture
def throwaway_prompt():
    """Unique prompt content per test so the prompt_versions rows registered
    here can be removed by hash and real registered content is untouched."""
    hashes: list[str] = []

    def make(kind: str) -> PromptFile:
        content = f"test {kind} prompt {uuid.uuid4()}"
        hashes.append(hashlib.sha256(content.encode("utf-8")).hexdigest())
        return PromptFile(
            kind=kind,
            label="v1",
            content=content,
            content_hash=hashes[-1],
        )

    yield make

    # References dropped before the rows, so this doesn't depend on which
    # fixture pytest tears down first.
    with SessionLocal() as session:
        ids = (
            session.execute(select(PromptVersion.id).where(PromptVersion.content_hash.in_(hashes)))
            .scalars()
            .all()
        )
        if ids:
            for column in (
                AutofillRequest.card_type_prompt_version_id,
                AutofillRequest.gap_fill_prompt_version_id,
            ):
                session.execute(
                    update(AutofillRequest).where(column.in_(ids)).values({column: None})
                )
            session.query(PromptVersion).filter(PromptVersion.id.in_(ids)).delete(
                synchronize_session=False
            )
        session.commit()


def test_create_persists_a_pending_row_and_returns_its_id(cleanup_ids):
    store = SqlAutofillRequestStore()
    card_id = f"test-card-{uuid.uuid4()}"

    request_id = store.create(card_id)
    cleanup_ids.append(request_id)

    with SessionLocal() as session:
        row = session.get(AutofillRequest, request_id)
        assert row.card_id == card_id
        assert row.status == "pending"


def test_mark_status_updates_status_and_error_detail(cleanup_ids):
    store = SqlAutofillRequestStore()
    request_id = store.create(f"test-card-{uuid.uuid4()}")
    cleanup_ids.append(request_id)

    store.mark_status(request_id, "failed", error_detail="boom")

    with SessionLocal() as session:
        row = session.get(AutofillRequest, request_id)
        assert row.status == "failed"
        assert row.error_detail == "boom"


def test_record_prompt_versions_attributes_only_the_field_that_was_filled(
    cleanup_ids, throwaway_prompt
):
    """A request that filled Type but not Gap must leave the gap_fill column
    NULL — otherwise the placeholder gap_fill prompt would look as though it
    had produced text it never produced."""
    store = SqlAutofillRequestStore()
    request_id = store.create(f"test-card-{uuid.uuid4()}")
    cleanup_ids.append(request_id)
    card_type_prompt = throwaway_prompt("card_type")

    store.record_prompt_versions(request_id, card_type_prompt=card_type_prompt)

    with SessionLocal() as session:
        row = session.get(AutofillRequest, request_id)
        assert row.gap_fill_prompt_version_id is None
        version = session.get(PromptVersion, row.card_type_prompt_version_id)
        assert version.content_hash == card_type_prompt.content_hash


def test_record_prompt_versions_attributes_both_fields_when_both_were_filled(
    cleanup_ids, throwaway_prompt
):
    store = SqlAutofillRequestStore()
    request_id = store.create(f"test-card-{uuid.uuid4()}")
    cleanup_ids.append(request_id)
    card_type_prompt = throwaway_prompt("card_type")
    gap_fill_prompt = throwaway_prompt("gap_fill")

    store.record_prompt_versions(
        request_id, card_type_prompt=card_type_prompt, gap_fill_prompt=gap_fill_prompt
    )

    with SessionLocal() as session:
        row = session.get(AutofillRequest, request_id)
        hashes = {
            session.get(PromptVersion, row.card_type_prompt_version_id).content_hash,
            session.get(PromptVersion, row.gap_fill_prompt_version_id).content_hash,
        }
    assert hashes == {card_type_prompt.content_hash, gap_fill_prompt.content_hash}


def test_mark_status_does_not_clear_prompt_versions_already_recorded(
    cleanup_ids, throwaway_prompt
):
    store = SqlAutofillRequestStore()
    request_id = store.create(f"test-card-{uuid.uuid4()}")
    cleanup_ids.append(request_id)
    store.record_prompt_versions(request_id, card_type_prompt=throwaway_prompt("card_type"))

    store.mark_status(request_id, "processed")

    with SessionLocal() as session:
        row = session.get(AutofillRequest, request_id)
        assert row.status == "processed"
        assert row.card_type_prompt_version_id is not None
