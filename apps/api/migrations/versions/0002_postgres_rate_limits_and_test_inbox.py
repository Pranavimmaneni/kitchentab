"""Postgres rate limits and email test inbox (ADR-0014, ADR-0016): no Redis in production.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09 04:33:07.676786
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_hits",
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("count > 0", name=op.f("ck_rate_limit_hits_count_positive")),
        sa.PrimaryKeyConstraint("bucket", "key", "window_start", name=op.f("pk_rate_limit_hits")),
    )
    op.create_index(op.f("ix_rate_limit_hits_window_start"), "rate_limit_hits", ["window_start"], unique=False)
    op.create_table(
        "test_inbox",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("to_address", postgresql.CITEXT(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=True),
        sa.Column(
            "headers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_inbox")),
    )
    op.create_index(
        op.f("ix_test_inbox_to_address_created_at"), "test_inbox", ["to_address", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_test_inbox_to_address_created_at"), table_name="test_inbox")
    op.drop_table("test_inbox")
    op.drop_index(op.f("ix_rate_limit_hits_window_start"), table_name="rate_limit_hits")
    op.drop_table("rate_limit_hits")
