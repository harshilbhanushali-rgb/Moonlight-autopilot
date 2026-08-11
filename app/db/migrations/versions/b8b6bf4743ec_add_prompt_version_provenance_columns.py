"""add prompt version provenance columns

Adds the prompt-version FKs that were missing from the provenance chain:

- `analysis.call_type_prompt_version_id` — Call Type is the fan-out step (it
  selects which scoring prompt and which gap rubric run), so without it the
  chain's root was unattributable even though the other three steps had columns.
- `autofill_requests.card_type_prompt_version_id` / `gap_fill_prompt_version_id`
  — Manual Card Auto-Fill recorded no attribution at all. `gap_fill/v1.txt` is
  still a placeholder, so this is what makes "which autofilled cards came from
  the placeholder and need redoing?" answerable.

All nullable: a step that failed or never ran has no lineage to claim, and the
rows written before provenance was wired up are deliberately left NULL rather
than backfilled with invented values.

Revision ID: b8b6bf4743ec
Revises: 01513aaff3d4
Create Date: 2026-08-10 16:44:21.854712

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8b6bf4743ec'
down_revision: Union[str, Sequence[str], None] = '01513aaff3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Autogenerate emitted these as create_foreign_key(None, ...), which lets
# Postgres pick the name on the way up and makes drop_constraint(None, ...)
# fail on the way down. Named explicitly, matching the *_fkey convention the
# three pre-existing analysis FKs already got from Postgres.
_ANALYSIS_CALL_TYPE_FK = "analysis_call_type_prompt_version_id_fkey"
_AUTOFILL_CARD_TYPE_FK = "autofill_requests_card_type_prompt_version_id_fkey"
_AUTOFILL_GAP_FILL_FK = "autofill_requests_gap_fill_prompt_version_id_fkey"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "analysis", sa.Column("call_type_prompt_version_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        _ANALYSIS_CALL_TYPE_FK,
        "analysis",
        "prompt_versions",
        ["call_type_prompt_version_id"],
        ["id"],
    )
    op.add_column(
        "autofill_requests",
        sa.Column("card_type_prompt_version_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "autofill_requests",
        sa.Column("gap_fill_prompt_version_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        _AUTOFILL_CARD_TYPE_FK,
        "autofill_requests",
        "prompt_versions",
        ["card_type_prompt_version_id"],
        ["id"],
    )
    op.create_foreign_key(
        _AUTOFILL_GAP_FILL_FK,
        "autofill_requests",
        "prompt_versions",
        ["gap_fill_prompt_version_id"],
        ["id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(_AUTOFILL_GAP_FILL_FK, "autofill_requests", type_="foreignkey")
    op.drop_constraint(_AUTOFILL_CARD_TYPE_FK, "autofill_requests", type_="foreignkey")
    op.drop_column("autofill_requests", "gap_fill_prompt_version_id")
    op.drop_column("autofill_requests", "card_type_prompt_version_id")
    op.drop_constraint(_ANALYSIS_CALL_TYPE_FK, "analysis", type_="foreignkey")
    op.drop_column("analysis", "call_type_prompt_version_id")
