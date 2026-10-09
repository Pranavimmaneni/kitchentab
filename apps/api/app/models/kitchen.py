"""Fridge and pantry: inventory, owners, grants and share requests (PRD §19.7)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, Index, Integer, PrimaryKeyConstraint, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import enums
from app.models.base import Base, cents, enum_check, fk, ts, uuid_pk, version_col


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        enum_check("place", enums.PLACES),
        enum_check("status", enums.INVENTORY_STATUSES),
        enum_check("category", enums.CATEGORIES, nullable=True),
        CheckConstraint("char_length(short_name) BETWEEN 1 AND 14", name="short_name_length"),
        CheckConstraint("shelf_life_days > 0", name="shelf_life_positive"),
        CheckConstraint("(bill_id IS NULL) = (item_id IS NULL)", name="bill_and_item_together"),
        CheckConstraint(
            "status <> 'used_up' OR (used_up_at IS NOT NULL AND used_up_by_member_id IS NOT NULL)",
            name="used_up_stamped",
        ),
        Index("ix_inventory_items_house_id_status_place", "house_id", "status", "place"),
        # One inventory item per consumable bill line.
        Index(
            "uq_inventory_items_item_id",
            "item_id",
            unique=True,
            postgresql_where=text("item_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    house_id: Mapped[uuid.UUID] = fk("houses.id")
    bill_id: Mapped[uuid.UUID | None] = fk("bills.id", nullable=True)
    item_id: Mapped[uuid.UUID | None] = fk("items.id", nullable=True)
    place: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text)
    icon_key: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    short_name: Mapped[str] = mapped_column(Text, nullable=False)
    quantity_text: Mapped[str | None] = mapped_column(Text)
    price_cents: Mapped[int | None] = cents(nullable=True)
    stocked_on: Mapped[date] = mapped_column(Date, nullable=False)
    shelf_life_days: Mapped[int] = mapped_column(Integer, nullable=False)
    snoozed_until: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'in_stock'"))
    used_up_by_member_id: Mapped[uuid.UUID | None] = fk("members.id", nullable=True)
    used_up_at: Mapped[datetime | None] = ts(nullable=True)
    created_by: Mapped[uuid.UUID] = fk("members.id")
    version: Mapped[int] = version_col()
    created_at: Mapped[datetime] = ts()
    updated_at: Mapped[datetime] = ts()


class InventoryOwner(Base):
    __tablename__ = "inventory_owners"
    __table_args__ = (PrimaryKeyConstraint("inventory_item_id", "member_id"),)

    inventory_item_id: Mapped[uuid.UUID] = fk("inventory_items.id")
    member_id: Mapped[uuid.UUID] = fk("members.id", index=True)
    open_to_share: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Last-writer stamps for the set-state sharing switch (§21.8)
    updated_by_member_id: Mapped[uuid.UUID | None] = fk("members.id", nullable=True)
    updated_at: Mapped[datetime] = ts()


class InventoryGrant(Base):
    __tablename__ = "inventory_grants"
    __table_args__ = (PrimaryKeyConstraint("inventory_item_id", "member_id"),)

    inventory_item_id: Mapped[uuid.UUID] = fk("inventory_items.id")
    member_id: Mapped[uuid.UUID] = fk("members.id")
    share_request_id: Mapped[uuid.UUID] = fk("share_requests.id")
    granted_by_member_id: Mapped[uuid.UUID] = fk("members.id")
    created_at: Mapped[datetime] = ts()


class ShareRequest(Base):
    __tablename__ = "share_requests"
    __table_args__ = (
        enum_check("amount", enums.ASK_AMOUNTS),
        enum_check("status", enums.REQUEST_STATUSES),
        enum_check("resolution", enums.REQUEST_RESOLUTIONS, nullable=True),
        CheckConstraint("note IS NULL OR char_length(note) <= 140", name="note_length"),
        CheckConstraint("chip_in_cents IS NULL OR chip_in_cents > 0", name="chip_in_positive"),
        CheckConstraint(
            "(status = 'approved') = (resolution IS NOT NULL)", name="resolution_only_when_approved"
        ),
        CheckConstraint(
            "(resolution IS NOT DISTINCT FROM 'chip_in') = (chip_in_cents IS NOT NULL)", name="chip_in_amount"
        ),
        Index(
            "uq_share_requests_one_pending",
            "inventory_item_id",
            "requester_member_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(None, "house_id", "status"),
        Index(None, "requester_member_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    house_id: Mapped[uuid.UUID] = fk("houses.id")
    inventory_item_id: Mapped[uuid.UUID] = fk("inventory_items.id")
    requester_member_id: Mapped[uuid.UUID] = fk("members.id")
    amount: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    resolution: Mapped[str | None] = mapped_column(Text)
    chip_in_cents: Mapped[int | None] = cents(nullable=True)
    answered_by_member_id: Mapped[uuid.UUID | None] = fk("members.id", nullable=True)
    answered_at: Mapped[datetime | None] = ts(nullable=True)
    undo_until: Mapped[datetime | None] = ts(nullable=True)
    expires_at: Mapped[datetime] = ts()
    version: Mapped[int] = version_col()
    created_at: Mapped[datetime] = ts()
