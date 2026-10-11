"""`make prod-clean-test-data`: delete the data that launch-check and e2e runs left in production,
and nothing else (BUILD_ALL §6 step 3, ADR-0022).

What counts as test data (the tag):
* test users: `users.email` ends with `@launch-check.kitchentab.test` (a reserved `.test` domain no
  real person can own; launch-check and `e2e-multi TARGET=prod` sign up only with it);
* test houses: created by a test user, and no other user is referenced anywhere in them (member,
  guest, bill author, uploader, allocation author, payment recorder or confirmer, activity actor,
  seen marker, notification);
* everything inside a test house, the test users' own rows (sessions, identities, prefs, push
  subscriptions...), and test-inbox emails addressed to the test domain.
A test user who is still referenced by a real house is kept (and reported).

Without `--yes` it only previews: the deletes run inside a transaction that is rolled back, so the
counts are exactly what `--yes` would delete. With `--yes` the same transaction commits only if
every table shrank by exactly the planned amount and no untagged user was touched.

    uv run --project apps/api python scripts/deploy/prod_clean_test_data.py [--dry-run] [--yes]
    ... --local   use DATABASE_URL from the environment instead of production

Needs `asyncpg` (from the API environment, which `make` uses); everything else is stdlib.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from deploylib import (
    EXIT_OK,
    TEST_EMAIL_DOMAIN,
    Console,
    DeployError,
    Runner,
    Vercel,
    direct_database_url,
    libpq_url,
    main_guard,
    repo_root,
)

TARGET = "prod-clean-test-data"
SUFFIX = "@" + TEST_EMAIL_DOMAIN

# Every foreign key to users.id, as (table, column). The DB test compares this with the live
# schema so a new table can't slip past the guards below.
USER_FKS = {
    ("activity_events", "actor_user_id"),
    ("allocations", "created_by"),
    ("auth_identities", "user_id"),
    ("bills", "created_by"),
    ("guests", "user_id"),
    ("houses", "created_by"),
    ("idempotency_keys", "user_id"),
    ("members", "user_id"),
    ("notification_prefs", "user_id"),
    ("notifications", "user_id"),
    ("payment_handles", "user_id"),
    ("payments", "confirmed_by"),
    ("payments", "recorded_by"),
    ("push_subscriptions", "user_id"),
    ("receipts", "uploaded_by"),
    ("seen_markers", "user_id"),
    ("sessions", "user_id"),
}
# Rows removed by ON DELETE CASCADE when a test user is deleted.
USER_CASCADES = (
    "auth_identities",
    "idempotency_keys",
    "notification_prefs",
    "notifications",
    "payment_handles",
    "push_subscriptions",
    "seen_markers",
    "sessions",
)

TU = "SELECT id FROM kt_test_users"
TH = "SELECT id FROM kt_test_houses"
TB = f"SELECT id FROM bills WHERE house_id IN ({TH})"
TI = f"SELECT id FROM inventory_items WHERE house_id IN ({TH})"

SETUP = [
    "CREATE TEMP TABLE kt_test_users (id uuid PRIMARY KEY) ON COMMIT DROP",
    (
        "INSERT INTO kt_test_users SELECT id FROM users "
        "WHERE email IS NOT NULL AND right(lower(email::text), length($1::text)) = $1::text"
    ),
    "CREATE TEMP TABLE kt_test_houses (id uuid PRIMARY KEY) ON COMMIT DROP",
    f"""INSERT INTO kt_test_houses SELECT h.id FROM houses h
    WHERE h.created_by IN ({TU})
      AND NOT EXISTS (SELECT 1 FROM members x WHERE x.house_id = h.id
                      AND x.user_id IS NOT NULL AND x.user_id NOT IN ({TU}))
      AND NOT EXISTS (SELECT 1 FROM bills b WHERE b.house_id = h.id AND (
            b.created_by NOT IN ({TU})
         OR EXISTS (SELECT 1 FROM guests x WHERE x.bill_id = b.id AND x.user_id IS NOT NULL AND x.user_id NOT IN ({TU}))
         OR EXISTS (SELECT 1 FROM receipts x WHERE x.bill_id = b.id AND x.uploaded_by NOT IN ({TU}))
         OR EXISTS (SELECT 1 FROM payments x WHERE x.bill_id = b.id AND (x.recorded_by NOT IN ({TU})
                    OR (x.confirmed_by IS NOT NULL AND x.confirmed_by NOT IN ({TU}))))
         OR EXISTS (SELECT 1 FROM seen_markers x WHERE x.bill_id = b.id AND x.user_id NOT IN ({TU}))
         OR EXISTS (SELECT 1 FROM items i JOIN allocations x ON x.item_id = i.id
                    WHERE i.bill_id = b.id AND x.created_by IS NOT NULL AND x.created_by NOT IN ({TU}))))
      AND NOT EXISTS (SELECT 1 FROM activity_events x WHERE x.house_id = h.id
                      AND x.actor_user_id IS NOT NULL AND x.actor_user_id NOT IN ({TU}))
      AND NOT EXISTS (SELECT 1 FROM notifications x WHERE x.house_id = h.id AND x.user_id NOT IN ({TU}))""",
]

# (table, DELETE) in foreign-key order: children before parents. All RESTRICT keys.
HOUSE_DELETES = [
    (
        "inventory_grants",
        f"DELETE FROM inventory_grants WHERE inventory_item_id IN ({TI})",
    ),
    (
        "inventory_owners",
        f"DELETE FROM inventory_owners WHERE inventory_item_id IN ({TI})",
    ),
    ("member_item_order", f"DELETE FROM member_item_order WHERE house_id IN ({TH})"),
    ("ledger_entries", f"DELETE FROM ledger_entries WHERE house_id IN ({TH})"),
    ("share_requests", f"DELETE FROM share_requests WHERE house_id IN ({TH})"),
    ("inventory_items", f"DELETE FROM inventory_items WHERE house_id IN ({TH})"),
    (
        "allocations",
        f"DELETE FROM allocations WHERE item_id IN (SELECT id FROM items WHERE bill_id IN ({TB}))",
    ),
    ("share_snapshots", f"DELETE FROM share_snapshots WHERE bill_id IN ({TB})"),
    ("payments", f"DELETE FROM payments WHERE bill_id IN ({TB})"),
    ("bill_exclusions", f"DELETE FROM bill_exclusions WHERE bill_id IN ({TB})"),
    ("bill_fees", f"DELETE FROM bill_fees WHERE bill_id IN ({TB})"),
    ("receipt_extractions", f"DELETE FROM receipt_extractions WHERE bill_id IN ({TB})"),
    ("receipts", f"DELETE FROM receipts WHERE bill_id IN ({TB})"),
    ("seen_markers", f"DELETE FROM seen_markers WHERE bill_id IN ({TB})"),
    ("items", f"DELETE FROM items WHERE bill_id IN ({TB})"),
    ("guests", f"DELETE FROM guests WHERE bill_id IN ({TB})"),
    ("activity_events", f"DELETE FROM activity_events WHERE house_id IN ({TH})"),
    ("notifications", f"DELETE FROM notifications WHERE house_id IN ({TH})"),
    ("bills", f"DELETE FROM bills WHERE house_id IN ({TH})"),
    (
        "house_events",
        f"DELETE FROM house_events WHERE house_id IN ({TH})",
    ),  # outbox cascades
    ("members", f"DELETE FROM members WHERE house_id IN ({TH})"),
    ("houses", f"DELETE FROM houses WHERE id IN ({TH})"),
]
# Test users nobody outside the deleted houses still points at (RESTRICT keys only).
DELETABLE_USERS = """SELECT u.id FROM kt_test_users u WHERE
    NOT EXISTS (SELECT 1 FROM members x WHERE x.user_id = u.id)
    AND NOT EXISTS (SELECT 1 FROM houses x WHERE x.created_by = u.id)
    AND NOT EXISTS (SELECT 1 FROM bills x WHERE x.created_by = u.id)
    AND NOT EXISTS (SELECT 1 FROM guests x WHERE x.user_id = u.id)
    AND NOT EXISTS (SELECT 1 FROM receipts x WHERE x.uploaded_by = u.id)
    AND NOT EXISTS (SELECT 1 FROM allocations x WHERE x.created_by = u.id)
    AND NOT EXISTS (SELECT 1 FROM payments x WHERE x.recorded_by = u.id OR x.confirmed_by = u.id)
    AND NOT EXISTS (SELECT 1 FROM activity_events x WHERE x.actor_user_id = u.id)"""
USERS_DELETE = f"DELETE FROM users WHERE id IN ({DELETABLE_USERS})"
INBOX_DELETE = "DELETE FROM test_inbox WHERE right(lower(to_address::text), length($1::text)) = $1::text"


def _count(status: str) -> int:
    parts = status.split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0


async def clean(dsn: str, *, commit: bool) -> dict[str, int]:
    """Runs the whole plan in one transaction; commits only if `commit` and every check holds.
    Returns rows deleted per table (including cascades)."""
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=30, command_timeout=120)
    try:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SET LOCAL lock_timeout = '10s'")
            tables = [
                r["tablename"]
                for r in await conn.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
            ]
            before = {
                t: await conn.fetchval(f'SELECT count(*) FROM "{t}"') for t in tables
            }
            untagged_users = await conn.fetchval(
                "SELECT count(*) FROM users WHERE email IS NULL OR right(lower(email::text), length($1::text)) <> $1::text",
                SUFFIX,
            )
            await conn.execute(SETUP[0])
            await conn.execute(SETUP[1], SUFFIX)
            await conn.execute(SETUP[2])
            await conn.execute(SETUP[3])
            deleted: dict[str, int] = {t: 0 for t in tables}
            deleted["outbox"] = await conn.fetchval(
                f"SELECT count(*) FROM outbox WHERE house_id IN ({TH})"
            )
            for table, sql in HOUSE_DELETES:
                deleted[table] += _count(await conn.execute(sql))
            for table in USER_CASCADES:
                deleted[table] += await conn.fetchval(
                    f"SELECT count(*) FROM {table} WHERE user_id IN ({DELETABLE_USERS})"
                )
            deleted["users"] = _count(await conn.execute(USERS_DELETE))
            deleted["test_inbox"] = _count(await conn.execute(INBOX_DELETE, SUFFIX))
            summary = {
                "test_users": await conn.fetchval("SELECT count(*) FROM kt_test_users"),
                "kept_test_users": await conn.fetchval(
                    f"SELECT count(*) FROM users WHERE id IN ({TU})"
                ),
            }

            after = {
                t: await conn.fetchval(f'SELECT count(*) FROM "{t}"') for t in tables
            }
            wrong = [
                f"{t}: expected -{deleted.get(t, 0)}, got -{before[t] - after[t]}"
                for t in tables
                if before[t] - after[t] != deleted.get(t, 0)
            ]
            still_untagged = await conn.fetchval(
                "SELECT count(*) FROM users WHERE email IS NULL OR right(lower(email::text), length($1::text)) <> $1::text",
                SUFFIX,
            )
            if still_untagged != untagged_users:
                wrong.append(
                    f"untagged users: {untagged_users} before, {still_untagged} after"
                )
            if wrong:
                raise DeployError(
                    "Safety check failed; nothing was deleted (rolled back).",
                    hint="Unexpected row changes:\n" + "\n".join(wrong),
                )
        except BaseException:
            await tx.rollback()
            raise
        if commit:
            await tx.commit()
        else:
            await tx.rollback()
        return {**{k: v for k, v in deleted.items() if v}, **summary}
    finally:
        await conn.close()


def _print_plan(console: Console) -> None:
    console.say(
        f"Tag: users whose email ends with {SUFFIX}; houses only they ever touched."
    )
    console.say("[dry-run] BEGIN")
    for sql in SETUP:
        console.say(
            "[dry-run] "
            + " ".join(sql.split())[:160]
            + (" ..." if len(" ".join(sql.split())) > 160 else "")
        )
    for table, sql in HOUSE_DELETES:
        console.say(
            f"[dry-run] {sql}"
            if len(sql) < 160
            else f"[dry-run] DELETE FROM {table} ... (test houses)"
        )
    console.say(
        "[dry-run] DELETE FROM users WHERE id IN (test users no real row points at)"
    )
    console.say(f"[dry-run] DELETE FROM test_inbox WHERE to_address ends with {SUFFIX}")
    console.say(
        "[dry-run] check every table shrank by exactly the planned rows; ROLLBACK (preview) or COMMIT (--yes)"
    )


def run(console: Console, runner: Runner, args: argparse.Namespace) -> int:
    dry = runner.dry_run
    console.say(
        f"KitchenTab prod-clean-test-data{'  [dry run: printing commands, running none]' if dry else ''}"
    )
    if args.local:
        url = os.environ.get("DATABASE_URL", "")
        if not url and not dry:
            raise DeployError("--local needs DATABASE_URL in the environment.")
        console.say("Database: DATABASE_URL from the environment (--local).")
    else:
        vercel = Vercel(runner)
        runner.announce_vercel()
        vercel.require_linked()
        vercel.whoami()
        env = vercel.env_pull("production")
        url = "" if dry else direct_database_url(env)
        console.say(
            "Database: production (Neon direct URL from `vercel env pull`, temp file already deleted)."
        )
    if dry:
        _print_plan(console)
        return EXIT_OK
    console.register(url)
    try:
        counts = asyncio.run(clean(libpq_url(url), commit=args.yes))
    except ImportError as err:
        raise DeployError(
            "asyncpg isn't installed here.",
            hint="Run it with `make prod-clean-test-data`.",
        ) from err
    except DeployError:
        raise
    except Exception as err:
        raise DeployError(
            f"Database error: {type(err).__name__}: {console.redact(str(err))}"
        ) from err
    kept = counts.pop("kept_test_users", 0)
    tagged = counts.pop("test_users", 0)
    rows = dict(counts)
    console.say(
        f"\nTest users found: {tagged} ({kept} kept because a real house still references them)."
    )
    if not rows:
        console.say("Nothing to delete.")
        return EXIT_OK
    width = max(len(k) for k in rows)
    verb = "Deleted" if args.yes else "Would delete"
    console.say(f"{verb}:")
    for table in sorted(rows):
        console.say(f"  {table:<{width}}  {rows[table]}")
    if args.yes:
        console.say("Committed.")
    else:
        console.say(
            "\nPreview only (rolled back). Run `make prod-clean-test-data ARGS=--yes` to delete these rows."
        )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Delete production rows tagged as test data."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the commands and SQL, run none"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="really delete (default: preview counts only)",
    )
    parser.add_argument(
        "--local", action="store_true", help="use DATABASE_URL from the environment"
    )
    args = parser.parse_args(argv)
    console = Console(args.dry_run)
    runner = Runner(console, repo_root(), TARGET)
    return main_guard(console, lambda: run(console, runner, args))


if __name__ == "__main__":
    sys.exit(main())
