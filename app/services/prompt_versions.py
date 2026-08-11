"""Registration of prompt/rubric content into the append-only
`prompt_versions` table.

Shared by the batch pipeline (app/services/batch/repository.py) and Manual
Card Auto-Fill (app/services/autofill/repository.py) rather than living in
either, because both need the same hash -> id mapping and neither owns it.

Keyed by content hash, not filename: `app/prompts/` files are edited in place
by the business team, so a filename/label alone would silently re-point old
rows at new content. Registering the content itself means an edit produces a
*new* row and every previously written analysis/autofill row keeps pointing at
the exact text that produced it.
"""

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import PromptVersion
from app.prompts.registry import PromptFile

logger = logging.getLogger(__name__)


def resolve_prompt_version_id(session: Session, prompt: PromptFile) -> int:
    """Returns the `prompt_versions.id` for `prompt`'s exact content,
    inserting the row the first time that content is seen.

    Append-only and idempotent: same content -> same id forever, different
    content -> a second row. Uses INSERT ... ON CONFLICT DO NOTHING RETURNING
    id (the pattern already used by app/services/scheduler/ledger.py's
    claim_run) so concurrent runs racing on the same prompt can't both insert;
    the loser reads back the winner's row.

    Does not commit — the caller owns the transaction, so the version row and
    the row referencing it land together or not at all.

    The one uncovered race is a loser whose winner has inserted but not yet
    committed: DO NOTHING doesn't wait, so the read-back finds nothing and
    raises NoResultFound. Left to fail visibly (the step is recorded failed and
    retried) rather than papered over with a NULL provenance id, and reachable
    only if two runs analyse a brand-new prompt within the same instant.
    """
    result = session.execute(
        insert(PromptVersion)
        .values(
            kind=prompt.kind,
            mode=prompt.mode,
            call_type=prompt.call_type,
            label=prompt.label,
            content=prompt.content,
            content_hash=prompt.content_hash,
        )
        .on_conflict_do_nothing(index_elements=["content_hash"])
        .returning(PromptVersion.id)
    )
    row = result.first()
    if row is not None:
        logger.info(
            "Registered new prompt version id=%s kind=%s call_type=%s mode=%s label=%s hash=%s",
            row[0],
            prompt.kind,
            prompt.call_type,
            prompt.mode,
            prompt.label,
            prompt.content_hash[:12],
        )
        return row[0]

    # The no-op path: this exact content is already registered.
    return session.execute(
        select(PromptVersion.id).where(PromptVersion.content_hash == prompt.content_hash)
    ).scalar_one()
