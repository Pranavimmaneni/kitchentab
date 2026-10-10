"""`POST /push/test` goes through the outbox like every other notification (PRD §20.2, PUSH-17).

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "'asks', 'answers', 'share', 'tab', 'pay', 'remind', 'soon', 'settled', 'joined', 'digest'"


TABLES = ("notifications", "notification_prefs")


def upgrade() -> None:
    for table in TABLES:
        op.drop_constraint(op.f(f"ck_{table}_type_enum"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_type_enum"), table, f"type IN ({OLD}, 'test')")


def downgrade() -> None:
    op.execute("DELETE FROM notifications WHERE type = 'test'")
    op.execute("DELETE FROM notification_prefs WHERE type = 'test'")
    for table in TABLES:
        op.drop_constraint(op.f(f"ck_{table}_type_enum"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_type_enum"), table, f"type IN ({OLD})")
