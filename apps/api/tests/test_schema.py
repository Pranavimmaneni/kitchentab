"""Baseline schema: migrations round-trip on seeded data, and every PRD constraint exists and bites."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models import Base
from app.seed.scenario import load_scenario
from app.seed.writer import write_scenario
from tests.conftest import alembic_config
from tests.harness.tags import tid

PEPPER = "test-pepper"

EXPECTED_TABLES = {
    # §10
    "users",
    "auth_identities",
    "sessions",
    "otp_codes",
    "houses",
    "members",
    "payment_handles",
    "bills",
    "bill_fees",
    "receipts",
    "receipt_extractions",
    "items",
    "guests",
    "allocations",
    "bill_exclusions",
    "share_snapshots",
    "payments",
    "ledger_entries",
    "activity_events",
    "notifications",
    "seen_markers",
    # §19.7
    "inventory_items",
    "inventory_owners",
    "inventory_grants",
    "share_requests",
    # docs/features/kitchen-gestures/SPEC.md §5.1
    "member_item_order",
    # §20.2
    "push_subscriptions",
    "notification_prefs",
    # §21.8
    "house_events",
    "outbox",
    "idempotency_keys",
    # ADR-0014
    "rate_limit_hits",
    "test_inbox",
}

EXPECTED_INDEXES = {
    "uq_members_one_admin_per_house",  # §10 one admin per house
    "uq_members_house_id_lower_display_name",  # §10 names unique, case-insensitive
    "ix_bills_house_id_state_purchase_date",  # §10 indexes
    "ix_items_bill_id_position",
    "ix_allocations_item_id",
    "ix_activity_events_bill_id_created_at",
    "ix_payments_bill_id",
    "ix_items_name_trgm",
    "ix_bills_store_name_trgm",
    "uq_allocations_item_id_member_id",  # ADR-0003 member-or-guest
    "uq_allocations_item_id_guest_id",
    "uq_bill_fees_one_adjustment_per_bill",  # ADR-0002 reconciliation override
    "ix_inventory_items_house_id_status_place",  # §19.7
    "uq_inventory_items_item_id",
    "uq_share_requests_one_pending",  # §19.7 one pending ask per asker per item
}

VERSIONED = {"houses", "members", "bills", "items", "inventory_items", "share_requests", "payments"}  # §21.8


@tid("DATA-01")
async def test_DATA_01_migrations_round_trip_on_seeded_database(
    migrated: str, sessions: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    async with sessions() as s, s.begin():
        await write_scenario(s, load_scenario("apt4b"), PEPPER)

    cfg = alembic_config(migrated)

    def roundtrip(sync_conn: Any) -> None:
        cfg.attributes["connection"] = sync_conn
        command.downgrade(cfg, "base")
        assert "users" not in inspect(sync_conn).get_table_names()
        command.upgrade(cfg, "head")
        diff = compare_metadata(MigrationContext.configure(sync_conn), Base.metadata)
        assert diff == [], f"models and migrations disagree: {diff}"

    async with engine.begin() as conn:
        await conn.run_sync(roundtrip)
    cfg.attributes.pop("connection", None)

    # Seeding again on the fresh schema works (the migration is complete, not just reversible).
    async with sessions() as s, s.begin():
        await write_scenario(s, load_scenario("apt4b"), PEPPER)


async def test_every_table_index_and_version_column_exists(engine: AsyncEngine) -> None:
    def check(sync_conn: Any) -> None:
        insp = inspect(sync_conn)
        tables = set(insp.get_table_names()) - {"alembic_version"}
        assert tables == EXPECTED_TABLES
        indexes = {ix["name"] for t in tables for ix in insp.get_indexes(t)}
        missing = EXPECTED_INDEXES - indexes
        assert not missing, missing
        for table in VERSIONED:
            assert "version" in {c["name"] for c in insp.get_columns(table)}, table
        for table in ("allocations", "inventory_owners"):
            cols = {c["name"] for c in insp.get_columns(table)}
            assert {"updated_by_member_id", "updated_at"} <= cols, table
        assert "event_seq" in {c["name"] for c in insp.get_columns("houses")}

    async with engine.connect() as conn:
        await conn.run_sync(check)
        exts = set((await conn.execute(text("SELECT extname FROM pg_extension"))).scalars())
    assert {"citext", "pg_trgm"} <= exts


# ---- constraints that must reject bad rows -------------------------------------------------------


@pytest.fixture
async def seeded(sessions: async_sessionmaker[AsyncSession]) -> dict[str, uuid.UUID]:
    async with sessions() as s, s.begin():
        return await write_scenario(s, load_scenario("apt4b"), PEPPER)


async def _rejects(sessions: async_sessionmaker[AsyncSession], sql: str, params: dict[str, Any]) -> str:
    async with sessions() as s:
        with pytest.raises(IntegrityError) as err:
            async with s.begin():
                await s.execute(text(sql), params)
    return str(err.value.orig)


ConstraintCase = tuple[str, str, Callable[[dict[str, uuid.UUID]], dict[str, Any]], str]

CASES: list[ConstraintCase] = [
    (
        "second active admin",
        "UPDATE members SET role = 'admin' WHERE id = :m",
        lambda ids: {"m": ids["member:arjun"]},
        "uq_members_one_admin_per_house",
    ),
    (
        "duplicate member name, different case",
        "INSERT INTO members (id, house_id, display_name, color, sort_order, role, status, created_at) "
        "VALUES (gen_random_uuid(), :h, 'MEERA', '#000000', 9, 'member', 'placeholder', now())",
        lambda ids: {"h": ids["house:apt4b"]},
        "uq_members_house_id_lower_display_name",
    ),
    (
        "allocation pointing at both a member and a guest",
        "UPDATE allocations SET guest_id = member_id WHERE item_id = :i",
        lambda ids: {"i": ids["item:shoprite.yogurt"]},
        "exactly_one_participant",
    ),
    (
        "duplicate allocation for the same member",
        "INSERT INTO allocations (id, item_id, member_id, weight, created_at, updated_at) "
        "VALUES (gen_random_uuid(), :i, :m, 1, now(), now())",
        lambda ids: {"i": ids["item:shoprite.yogurt"], "m": ids["member:meera"]},
        "uq_allocations_item_id_member_id",
    ),
    (
        "weight must be positive",
        "UPDATE allocations SET weight = 0 WHERE item_id = :i",
        lambda ids: {"i": ids["item:shoprite.yogurt"]},
        "weight_positive",
    ),
    (
        "negative bill total",
        "UPDATE bills SET total_cents = -1 WHERE id = :b",
        lambda ids: {"b": ids["bill:shoprite"]},
        "total_cents_range",
    ),
    (
        "bill state outside the enum",
        "UPDATE bills SET state = 'archived' WHERE id = :b",
        lambda ids: {"b": ids["bill:shoprite"]},
        "state_enum",
    ),
    (
        "void without a reason",
        "UPDATE bills SET state = 'voided' WHERE id = :b",
        lambda ids: {"b": ids["bill:shoprite"]},
        "void_has_reason",
    ),
    (
        "printed total without an override reason",
        "UPDATE bills SET printed_total_cents = total_cents WHERE id = :b",
        lambda ids: {"b": ids["bill:shoprite"]},
        "override_has_reason",
    ),
    (
        "two adjustments on one bill",
        "INSERT INTO bill_fees (id, bill_id, kind, label, amount_cents, created_at) VALUES "
        "(gen_random_uuid(), :b, 'adjustment', 'a', 5, now()), "
        "(gen_random_uuid(), :b, 'adjustment', 'b', -5, now())",
        lambda ids: {"b": ids["bill:shoprite"]},
        "uq_bill_fees_one_adjustment_per_bill",
    ),
    (
        "negative fee (only adjustments are signed)",
        "INSERT INTO bill_fees (id, bill_id, kind, label, amount_cents, created_at) "
        "VALUES (gen_random_uuid(), :b, 'fee', 'Bag', -10, now())",
        lambda ids: {"b": ids["bill:shoprite"]},
        "amount_sign",
    ),
    (
        "second pending ask for the same item",
        "INSERT INTO share_requests (id, house_id, inventory_item_id, requester_member_id, amount, status, "
        "expires_at, created_at) SELECT gen_random_uuid(), house_id, inventory_item_id, requester_member_id, "
        "'half', 'pending', expires_at, now() FROM share_requests WHERE id = :r",
        lambda ids: {"r": ids["request:r1"]},
        "uq_share_requests_one_pending",
    ),
    (
        "chip-in amount without a chip-in resolution",
        "UPDATE share_requests SET chip_in_cents = 100 WHERE id = :r",
        lambda ids: {"r": ids["request:r1"]},
        "chip_in_amount",
    ),
    (
        "ask note over 140 characters",
        "UPDATE share_requests SET note = repeat('x', 141) WHERE id = :r",
        lambda ids: {"r": ids["request:r1"]},
        "note_length",
    ),
    (
        "hard-deleting a bill that has money rows",
        "DELETE FROM bills WHERE id = :b",
        lambda ids: {"b": ids["bill:shoprite"]},
        "fk_",
    ),
    (
        "placeholder member with a user",
        "UPDATE members SET status = 'placeholder' WHERE id = :m",
        lambda ids: {"m": ids["member:arjun"]},
        "status_matches_user",
    ),
    (
        "ledger entry to yourself",
        "INSERT INTO ledger_entries "
        "(id, house_id, from_member_id, to_member_id, amount_cents, source, created_at) "
        "VALUES (gen_random_uuid(), :h, :m, :m, 100, 'carryover', now())",
        lambda ids: {"h": ids["house:apt4b"], "m": ids["member:arjun"]},
        "distinct_parties",
    ),
    (
        "short name longer than 14",
        "UPDATE inventory_items SET short_name = repeat('x', 15) WHERE id = :i",
        lambda ids: {"i": ids["inventory:shoprite.milk"]},
        "short_name_length",
    ),
]


@pytest.mark.parametrize(("label", "sql", "params", "constraint"), CASES, ids=[c[0] for c in CASES])
async def test_constraint_rejects(
    seeded: dict[str, uuid.UUID],
    sessions: async_sessionmaker[AsyncSession],
    label: str,
    sql: str,
    params: Callable[[dict[str, uuid.UUID]], dict[str, Any]],
    constraint: str,
) -> None:
    message = await _rejects(sessions, sql, params(seeded))
    assert constraint in message, f"{label}: {message}"


async def test_anonymized_members_may_share_a_name(
    seeded: dict[str, uuid.UUID], sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Two deleted accounts in one house both become "Former roommate" (PRD §10 members note)."""
    async with sessions() as s, s.begin():
        await s.execute(
            text(
                "UPDATE members SET display_name = 'Former roommate', anonymized_at = now(), status = 'left' "
                "WHERE id IN (:a, :b)"
            ),
            {"a": seeded["member:arjun"], "b": seeded["member:kabir"]},
        )


async def test_adjustment_may_be_negative(
    seeded: dict[str, uuid.UUID], sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO bill_fees (id, bill_id, kind, label, amount_cents, created_at) "
                "VALUES (gen_random_uuid(), :b, 'adjustment', 'Receipt adjustment', -7, now())"
            ),
            {"b": seeded["bill:hmart"]},
        )
