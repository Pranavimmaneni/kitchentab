"""PRD §20.2 (owner's call 2026-10-11): quiet hours are off until a person turns them on. Drops the
22:00-08:00 column default and clears it for everyone still on that untouched default, so held
notifications go out at the next drain. Additive in effect: old code treats NULL as no quiet hours.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("users", "quiet_start", server_default=None)
    op.alter_column("users", "quiet_end", server_default=None)
    op.execute(
        "UPDATE users SET quiet_start = NULL, quiet_end = NULL WHERE quiet_start = '22:00' AND quiet_end = '08:00'"
    )


def downgrade() -> None:
    # The cleared values can't be told apart from people who never had quiet hours; only the
    # default comes back.
    op.alter_column("users", "quiet_start", server_default=sa.text("'22:00'"))
    op.alter_column("users", "quiet_end", server_default=sa.text("'08:00'"))
