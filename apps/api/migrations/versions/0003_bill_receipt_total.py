"""bills.receipt_total_cents: the printed total that review reconciles against (FR-13).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09 05:02:07.533542
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bills", sa.Column("receipt_total_cents", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("bills", "receipt_total_cents")
