"""Registration of prompt content into `prompt_versions`.

Needs a real Postgres: the whole mechanism is INSERT ... ON CONFLICT
(content_hash) DO NOTHING RETURNING id against the
`uq_prompt_versions_content_hash` constraint, which no in-memory fake exercises.
"""

import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.db.models import PromptVersion
from app.db.session import SessionLocal
from app.prompts.registry import PromptFile
from app.services.prompt_versions import resolve_prompt_version_id

pytestmark = pytest.mark.integration


def make_prompt(content: str, **overrides) -> PromptFile:
    values = dict(
        kind="gap_rubric",
        label="v1",
        call_type="demo",
        mode="descriptiononly",
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    values.update(overrides)
    return PromptFile(**values)


@pytest.fixture
def prompt_content():
    """Unique content per test, cleaned up by hash afterwards, so these tests
    never leave rows behind or collide with real registered prompts."""
    hashes: list[str] = []

    def make(suffix: str = "") -> PromptFile:
        prompt = make_prompt(f"test rubric {uuid.uuid4()}{suffix}")
        hashes.append(prompt.content_hash)
        return prompt

    yield make

    with SessionLocal() as session:
        session.query(PromptVersion).filter(PromptVersion.content_hash.in_(hashes)).delete(
            synchronize_session=False
        )
        session.commit()


def test_the_same_content_resolves_to_the_same_row_twice(prompt_content):
    prompt = prompt_content()

    with SessionLocal() as session:
        first = resolve_prompt_version_id(session, prompt)
        session.commit()
    with SessionLocal() as session:
        second = resolve_prompt_version_id(session, prompt)
        session.commit()

    assert first == second

    with SessionLocal() as session:
        rows = (
            session.execute(
                select(PromptVersion).where(PromptVersion.content_hash == prompt.content_hash)
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1


def test_edited_content_gets_a_second_row_rather_than_replacing_the_first(prompt_content):
    """The point of hashing content instead of trusting the filename: the
    business team edits app/prompts/ files in place, and rows written before
    the edit must keep pointing at the text that produced them."""
    original = prompt_content()
    edited = prompt_content(suffix=" — plus a new instruction")

    with SessionLocal() as session:
        original_id = resolve_prompt_version_id(session, original)
        edited_id = resolve_prompt_version_id(session, edited)
        session.commit()

    assert original_id != edited_id

    with SessionLocal() as session:
        still_there = session.get(PromptVersion, original_id)
        assert still_there.content == original.content


def test_the_registered_row_stores_every_identifying_field(prompt_content):
    prompt = prompt_content()

    with SessionLocal() as session:
        version_id = resolve_prompt_version_id(session, prompt)
        session.commit()

    with SessionLocal() as session:
        row = session.get(PromptVersion, version_id)

    assert (row.kind, row.call_type, row.mode, row.label) == (
        prompt.kind,
        prompt.call_type,
        prompt.mode,
        prompt.label,
    )
    assert row.content == prompt.content
    assert row.content_hash == prompt.content_hash


def test_resolving_does_not_commit_so_the_caller_owns_the_transaction(prompt_content):
    """persist_analysis_result writes the version row and the analysis row that
    references it in one transaction — a resolver that committed on its own
    could leave a registered version with no row pointing at it."""
    prompt = prompt_content()

    with SessionLocal() as session:
        resolve_prompt_version_id(session, prompt)
        session.rollback()

    with SessionLocal() as session:
        found = session.execute(
            select(PromptVersion.id).where(PromptVersion.content_hash == prompt.content_hash)
        ).first()

    assert found is None
