"""Receipts up to 20 MB (FR-10 as changed by BUILD_ALL C1, ADR-0015).

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_receipts_bytes_max_15mb"), "receipts", type_="check")
    op.create_check_constraint(op.f("ck_receipts_bytes_max_20mb"), "receipts", "bytes > 0 AND bytes <= 20971520")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_receipts_bytes_max_20mb"), "receipts", type_="check")
    op.create_check_constraint(op.f("ck_receipts_bytes_max_15mb"), "receipts", "bytes > 0 AND bytes <= 15728640")
