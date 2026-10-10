"""FR-81 / ASK-15..ASK-17: custom split on asks. An allocation can hold a fixed fraction of its line
(share_num / share_den) instead of a weight (ADR-0030). Additive: old code ignores the columns.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("allocations", sa.Column("share_num", sa.BigInteger(), nullable=True))
    op.add_column("allocations", sa.Column("share_den", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        op.f("ck_allocations_share_valid"),
        "allocations",
        "(share_num IS NULL AND share_den IS NULL) OR (share_num > 0 AND share_den > 0 AND share_num <= share_den)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_allocations_share_valid"), "allocations", type_="check")
    op.drop_column("allocations", "share_den")
    op.drop_column("allocations", "share_num")
