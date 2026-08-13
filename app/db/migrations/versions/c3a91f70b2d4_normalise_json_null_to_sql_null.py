"""normalise JSON null to SQL NULL on analysis JSONB columns

Revision ID: c3a91f70b2d4
Revises: e74f5a5b804e
Create Date: 2026-08-13

SQLAlchemy's JSONB stores Python None as the JSON value `null` unless the column
is declared `none_as_null=True`, which `analysis.risk_gap_analysis` never was and
`analysis.call_score_categories` was not when it was added. A row written that
way answers `IS NULL` with **false**, so "which calls have no breakdown" and
"which calls have no gap answer" both return wrong sets.

It matters most for `risk_gap_analysis`, where None ("the gap step produced no
answer") and [] ("it ran and flagged nothing") are a distinction the pipeline
depends on — see CardTypeContext.gaps. A JSON `null` row reads as neither.

Five rows currently hold JSON `null` in both columns, from the repair script that
reset the calls an integration test had overwritten (see
app/services/batch/revert_stub_written_rows.py). The model now declares
none_as_null on both columns, so this only cleans up what predates that.

Data-only: no schema change, and it cannot touch a row holding a real array.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c3a91f70b2d4"
down_revision: Union[str, Sequence[str], None] = "e74f5a5b804e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for column in ("call_score_categories", "risk_gap_analysis"):
        op.execute(
            f"UPDATE analysis SET {column} = NULL "
            f"WHERE jsonb_typeof({column}) = 'null'"
        )


def downgrade() -> None:
    """Deliberately not reversed.

    SQL NULL is what these rows always meant; re-introducing JSON `null` would
    restore a bug, and the two states are indistinguishable to every consumer
    that is behaving correctly.
    """
