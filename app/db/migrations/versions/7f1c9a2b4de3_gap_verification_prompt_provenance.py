"""gap verification prompt provenance

The gap step now runs a second pass that checks each gap's evidence actually
supports its claim, and drops the ones that don't (app/domain/gap_verification).
It uses two prompts — one for `dialogue` gaps (quote plus a context window) and
one for `explanation` gaps (the whole transcript, because an absence claim
cannot be disproved from an excerpt).

Those prompts decide which gaps survive, so an edit to either changes
`analysis.risk_gap_analysis` exactly as much as an edit to the rubric does.
They therefore get provenance columns of their own rather than hiding behind
`gap_rubric_version_id`.

Both nullable, following the same rule as every other provenance column: NULL
means "no lineage to claim" — that verifier had no gap of its kind to judge on
this call, the step failed, or the row predates verification entirely. The 46
existing rows are deliberately left NULL rather than backfilled; they really
were produced without this check.

Revision ID: 7f1c9a2b4de3
Revises: cddcbee4eb25
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7f1c9a2b4de3"
down_revision: Union[str, Sequence[str], None] = "cddcbee4eb25"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Named explicitly rather than left to Postgres: create_foreign_key(None, ...)
# lets the server pick a name on the way up, which makes drop_constraint(None,
# ...) fail on the way down. Matches the *_fkey convention of the existing
# analysis FKs.
_DIALOGUE_FK = "analysis_gap_verification_dialogue_version_id_fkey"
_EXPLANATION_FK = "analysis_gap_verification_explanation_version_id_fkey"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "analysis",
        sa.Column("gap_verification_dialogue_version_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "analysis",
        sa.Column("gap_verification_explanation_version_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        _DIALOGUE_FK,
        "analysis",
        "prompt_versions",
        ["gap_verification_dialogue_version_id"],
        ["id"],
    )
    op.create_foreign_key(
        _EXPLANATION_FK,
        "analysis",
        "prompt_versions",
        ["gap_verification_explanation_version_id"],
        ["id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(_EXPLANATION_FK, "analysis", type_="foreignkey")
    op.drop_constraint(_DIALOGUE_FK, "analysis", type_="foreignkey")
    op.drop_column("analysis", "gap_verification_explanation_version_id")
    op.drop_column("analysis", "gap_verification_dialogue_version_id")
