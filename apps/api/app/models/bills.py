"""Bills, receipts, items, guests, allocations, payments and the house ledger (PRD §6, §10, §19.7)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import CHAR, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import enums
from app.models.base import (
    Base,
    cents,
    enum_check,
    exactly_one_participant,
    fk,
    ts,
    uuid_pk,
    version_col,
)


class Bill(Base):
    __tablename__ = "bills"
    __table_args__ = (
        enum_check("state", enums.BILL_STATES),
        enum_check("extraction_status", enums.EXTRACTION_STATUSES),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso"),
        CheckConstraint(f"total_cents BETWEEN 0 AND {enums.MAX_BILL_TOTAL_CENTS}", name="total_cents_range"),
        CheckConstraint("tax_cents >= 0", name="tax_nonnegative"),
        CheckConstraint(
            "printed_total_cents IS NULL OR "
            "(printed_total_cents = total_cents AND reconcile_override_reason IS NOT NULL)",
            name="override_has_reason",
        ),
        CheckConstraint(
            "state <> 'voided' OR coalesce(char_length(void_reason), 0) BETWEEN 3 AND 200",
            name="void_has_reason",
        ),
        CheckConstraint("state = 'draft' OR sent_at IS NOT NULL", name="sent_bills_have_sent_at"),
        Index("ix_bills_house_id_state_purchase_date", "house_id", "state", text("purchase_date DESC")),
        Index(
            "ix_bills_store_name_trgm",
            "store_name",
            postgresql_using="gin",
            postgresql_ops={"store_name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    house_id: Mapped[uuid.UUID] = fk("houses.id")
    payer_member_id: Mapped[uuid.UUID] = fk("members.id")
    created_by: Mapped[uuid.UUID] = fk("users.id")
    store_name: Mapped[str | None] = mapped_column(Text)
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default=text("'USD'"))
    subtotal_cents: Mapped[int] = cents(default=0)
    tax_cents: Mapped[int] = cents(default=0)
    total_cents: Mapped[int] = cents(default=0)
    # What the receipt (or the payer) says the total is; reconciliation compares against it (FR-13).
    receipt_total_cents: Mapped[int | None] = cents(nullable=True)
    # FR-13 override: the printed receipt total, kept equal to total_cents (ADR-0002).
    printed_total_cents: Mapped[int | None] = cents(nullable=True)
    reconcile_override_reason: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    extraction_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'none'"))
    link_token_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    sent_at: Mapped[datetime | None] = ts(nullable=True)
    settled_at: Mapped[datetime | None] = ts(nullable=True)
    settled_by: Mapped[uuid.UUID | None] = fk("members.id", nullable=True)
    void_reason: Mapped[str | None] = mapped_column(Text)
    voided_at: Mapped[datetime | None] = ts(nullable=True)
    version: Mapped[int] = version_col()
    created_at: Mapped[datetime] = ts()
    updated_at: Mapped[datetime] = ts()
    deleted_at: Mapped[datetime | None] = ts(nullable=True)


class BillFee(Base):
    __tablename__ = "bill_fees"
    __table_args__ = (
        enum_check("kind", enums.BILL_FEE_KINDS),
        # Discounts are stored positive (kind gives the sign); only adjustments are signed.
        CheckConstraint("kind = 'adjustment' OR amount_cents >= 0", name="amount_sign"),
        Index(
            "uq_bill_fees_one_adjustment_per_bill",
            "bill_id",
            unique=True,
            postgresql_where=text("kind = 'adjustment'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    bill_id: Mapped[uuid.UUID] = fk("bills.id", index=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    amount_cents: Mapped[int] = cents()
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = ts()


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (
        enum_check("mime", enums.RECEIPT_MIMES),
        CheckConstraint("bytes > 0 AND bytes <= 20971520", name="bytes_max_20mb"),
        CheckConstraint("page_index >= 0", name="page_index_nonnegative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    bill_id: Mapped[uuid.UUID] = fk("bills.id", index=True)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    uploaded_by: Mapped[uuid.UUID] = fk("users.id")
    created_at: Mapped[datetime] = ts()
    deleted_at: Mapped[datetime | None] = ts(nullable=True)


class ReceiptExtraction(Base):
    __tablename__ = "receipt_extractions"
    __table_args__ = (enum_check("status", ("pending", "running", "done", "failed")),)

    id: Mapped[uuid.UUID] = uuid_pk()
    bill_id: Mapped[uuid.UUID] = fk("bills.id", index=True)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    # Provider cost for observability, not household money.
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = ts()


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        enum_check("storage", enums.STORAGE_TAGS),
        enum_check("category", enums.CATEGORIES, nullable=True),
        CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="name_length"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("discount_cents >= 0", name="discount_nonnegative"),
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="confidence_range"),
        CheckConstraint("shelf_life_days IS NULL OR shelf_life_days > 0", name="shelf_life_positive"),
        Index("ix_items_bill_id_position", "bill_id", "position"),
        Index("ix_items_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    bill_id: Mapped[uuid.UUID] = fk("bills.id")
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False, server_default=text("1"))
    unit_price_cents: Mapped[int | None] = cents(nullable=True)
    discount_cents: Mapped[int] = cents(default=0)
    # After line-level discounts; may be 0 (promo) or negative (deposit return).
    line_total_cents: Mapped[int] = cents()
    taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Fridge and pantry fields (§19.7)
    storage: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text)
    icon_key: Mapped[str | None] = mapped_column(Text)
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = version_col()
    created_at: Mapped[datetime] = ts()
    updated_at: Mapped[datetime] = ts()
    deleted_at: Mapped[datetime | None] = ts(nullable=True)


class Guest(Base):
    __tablename__ = "guests"
    __table_args__ = (CheckConstraint("char_length(display_name) BETWEEN 1 AND 24", name="display_name_length"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    bill_id: Mapped[uuid.UUID] = fk("bills.id", index=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID | None] = fk("users.id", nullable=True)
    link_token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # Continues after the house's members so rounding tie-breaks stay deterministic.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = ts()


class Allocation(Base):
    __tablename__ = "allocations"
    __table_args__ = (
        exactly_one_participant(),
        CheckConstraint("weight > 0", name="weight_positive"),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "(share_num IS NULL AND share_den IS NULL) OR (share_num > 0 AND share_den > 0 AND share_num <= share_den)",
            name="share_valid",
        ),
        Index("ix_allocations_item_id", "item_id"),
        Index(
            "uq_allocations_item_id_member_id",
            "item_id",
            "member_id",
            unique=True,
            postgresql_where=text("member_id IS NOT NULL"),
        ),
        Index(
            "uq_allocations_item_id_guest_id",
            "item_id",
            "guest_id",
            unique=True,
            postgresql_where=text("guest_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    item_id: Mapped[uuid.UUID] = fk("items.id")
    member_id: Mapped[uuid.UUID | None] = fk("members.id", nullable=True)
    guest_id: Mapped[uuid.UUID | None] = fk("guests.id", nullable=True)
    weight: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False, server_default=text("1"))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))  # reserved for v1.1
    # Custom split (FR-81, ADR-0030): a fixed fraction of the line instead of the weight.
    share_num: Mapped[int | None] = mapped_column(BigInteger)
    share_den: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True)
    created_at: Mapped[datetime] = ts()
    # Last-writer stamps for set-state cells (§21.8)
    updated_by_member_id: Mapped[uuid.UUID | None] = fk("members.id", nullable=True)
    updated_at: Mapped[datetime] = ts()


class BillExclusion(Base):
    __tablename__ = "bill_exclusions"
    __table_args__ = (PrimaryKeyConstraint("bill_id", "member_id"),)

    bill_id: Mapped[uuid.UUID] = fk("bills.id")
    member_id: Mapped[uuid.UUID] = fk("members.id")
    created_at: Mapped[datetime] = ts()


class ShareSnapshot(Base):
    __tablename__ = "share_snapshots"
    __table_args__ = (exactly_one_participant(), Index(None, "bill_id", "computed_at"))

    id: Mapped[uuid.UUID] = uuid_pk()
    bill_id: Mapped[uuid.UUID] = fk("bills.id")
    member_id: Mapped[uuid.UUID | None] = fk("members.id", nullable=True)
    guest_id: Mapped[uuid.UUID | None] = fk("guests.id", nullable=True)
    share_cents: Mapped[int] = cents()
    bill_version: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = ts()
    reason: Mapped[str] = mapped_column(Text, nullable=False)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        exactly_one_participant(),
        enum_check("method", enums.PAYMENT_METHODS),
        enum_check("direction", enums.PAYMENT_DIRECTIONS),
        enum_check("status", enums.PAYMENT_STATUSES),
        CheckConstraint(f"amount_cents BETWEEN 1 AND {enums.MAX_BILL_TOTAL_CENTS}", name="amount_range"),
        CheckConstraint("note IS NULL OR char_length(note) <= 280", name="note_length"),
        Index("ix_payments_bill_id", "bill_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    bill_id: Mapped[uuid.UUID] = fk("bills.id")
    # The non-payer participant. direction=to_payer: they paid the payer; refund: payer paid them back.
    member_id: Mapped[uuid.UUID | None] = fk("members.id", nullable=True)
    guest_id: Mapped[uuid.UUID | None] = fk("guests.id", nullable=True)
    to_member_id: Mapped[uuid.UUID] = fk("members.id")
    direction: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'to_payer'"))
    amount_cents: Mapped[int] = cents()
    method: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reject_note: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[uuid.UUID] = fk("users.id")
    confirmed_by: Mapped[uuid.UUID | None] = fk("users.id", nullable=True)
    version: Mapped[int] = version_col()
    created_at: Mapped[datetime] = ts()
    confirmed_at: Mapped[datetime | None] = ts(nullable=True)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        enum_check("source", enums.LEDGER_SOURCES),
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        CheckConstraint("from_member_id <> to_member_id", name="distinct_parties"),
        CheckConstraint("source <> 'chip_in' OR share_request_id IS NOT NULL", name="chip_in_has_request"),
        Index(None, "house_id", "from_member_id", "to_member_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    house_id: Mapped[uuid.UUID] = fk("houses.id")
    from_member_id: Mapped[uuid.UUID] = fk("members.id")
    to_member_id: Mapped[uuid.UUID] = fk("members.id")
    amount_cents: Mapped[int] = cents()
    source: Mapped[str] = mapped_column(Text, nullable=False)
    bill_id: Mapped[uuid.UUID | None] = fk("bills.id", nullable=True)
    share_request_id: Mapped[uuid.UUID | None] = fk("share_requests.id", nullable=True)
    created_at: Mapped[datetime] = ts()


class SeenMarker(Base):
    __tablename__ = "seen_markers"
    __table_args__ = (PrimaryKeyConstraint("user_id", "bill_id"),)

    user_id: Mapped[uuid.UUID] = fk("users.id", ondelete="CASCADE")
    bill_id: Mapped[uuid.UUID] = fk("bills.id")
    last_seen_version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = ts()
