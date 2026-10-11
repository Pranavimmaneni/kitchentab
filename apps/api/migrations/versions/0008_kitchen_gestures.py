"""FR-G02, FR-G08 (docs/features/kitchen-gestures/SPEC.md §5): each member's own item order per
section, and who last moved an item between Freezer, Fridge and Pantry. Additive: old code ignores
the new table and columns.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "member_item_order",
        sa.Column("house_id", sa.UUID(), nullable=False),
        sa.Column("member_id", sa.UUID(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("item_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "location IN ('fridge', 'freezer', 'pantry')", name=op.f("ck_member_item_order_location_enum")
        ),
        sa.CheckConstraint("cardinality(item_ids) <= 1000", name=op.f("ck_member_item_order_item_ids_bounded")),
        sa.ForeignKeyConstraint(
            ["house_id"], ["houses.id"], name=op.f("fk_member_item_order_house_id_houses"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name=op.f("fk_member_item_order_member_id_members"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("house_id", "member_id", "location", name=op.f("pk_member_item_order")),
    )
    op.add_column("inventory_items", sa.Column("moved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("inventory_items", sa.Column("moved_by_member_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_inventory_items_moved_by_member_id_members"),
        "inventory_items",
        "members",
        ["moved_by_member_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_inventory_items_moved_by_member_id_members"), "inventory_items", type_="foreignkey")
    op.drop_column("inventory_items", "moved_by_member_id")
    op.drop_column("inventory_items", "moved_at")
    op.drop_table("member_item_order")
