"""VERCEL-10: `make prod-clean-test-data` deletes only rows tagged as test data (ADR-0022).

Runs against a real migrated Postgres seeded with the apt4b scenario."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from deploy_harness import ROOT, Harness

SCRIPT = "prod_clean_test_data.py"
TAG = "@launch-check.kitchentab.test"


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def sql(url: str, *statements: str) -> list[list[tuple]]:
    async def go() -> list[list[tuple]]:
        import asyncpg

        conn = await asyncpg.connect(_plain(url))
        try:
            out = []
            for statement in statements:
                out.append([tuple(r) for r in await conn.fetch(statement)])
            return out
        finally:
            await conn.close()

    return asyncio.run(go())


def counts(url: str) -> dict[str, int]:
    tables = [
        r[0]
        for r in sql(
            url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )[0]
    ]
    rows = sql(url, *[f'SELECT count(*) FROM "{t}"' for t in tables])
    return {t: r[0][0] for t, r in zip(tables, rows, strict=True)}


def seed(url: str) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "APP_ENV": "local",
        "PROFILE": "vercel",
        "QUEUE_PROVIDER": "inline",
        "SESSION_SECRET": "seed-session-secret-0123456789abcdef",
        "TOKEN_HASH_PEPPER": "seed-pepper-0123456789abcdef0123456",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "app.seed", "apt4b"],
        cwd=ROOT / "apps" / "api",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]


def tag_users(url: str, *, except_email: str | None = None) -> None:
    keep = f" WHERE email IS NULL OR email <> '{except_email}'" if except_email else ""
    local = "coalesce(split_part(email::text, '@', 1), 'user-' || replace(id::text, '-', ''))"
    sql(url, f"UPDATE users SET email = {local} || '{TAG}'{keep} RETURNING id")


def add_lone_user(url: str, email: str) -> str:
    uid = str(uuid.uuid4())
    now = datetime.now(UTC)
    sql(
        url,
        f"INSERT INTO users (id, display_name, email, created_at) VALUES ('{uid}', 'Lone', '{email}', '{now.isoformat()}') RETURNING id",
        f"INSERT INTO sessions (id, user_id, token_hash, created_at, last_seen_at, expires_at) VALUES "
        f"('{uuid.uuid4()}', '{uid}', '{uuid.uuid4().hex}', '{now.isoformat()}', '{now.isoformat()}', "
        f"'{(now + timedelta(days=30)).isoformat()}') RETURNING id",
        f"INSERT INTO test_inbox (id, to_address, subject, text_body, created_at) VALUES "
        f"('{uuid.uuid4()}', '{email}', '123456 is your KitchenTab code', 'x', '{now.isoformat()}') RETURNING id",
    )
    return uid


def run_local(h: Harness, url: str, *args: str):
    return h.run(SCRIPT, "--local", *args, extra_env={"DATABASE_URL": url})


@pytest.mark.id("VERCEL-10")
def test_vercel10_clean_dry_run_prints_the_plan_and_connects_to_nothing(
    harness: Harness,
) -> None:
    run = harness.run(SCRIPT, "--dry-run")
    assert run.code == 0, run.text
    assert harness.calls() == []
    assert "[dry-run] $ vercel env pull" in run.out
    assert f"Tag: users whose email ends with {TAG}" in run.out
    assert (
        "[dry-run] DELETE FROM houses WHERE id IN (SELECT id FROM kt_test_houses)"
        in run.out
    )
    local = harness.run(
        SCRIPT,
        "--dry-run",
        "--local",
        extra_env={"DATABASE_URL": "postgresql://x:y@127.0.0.1:1/none"},
    )
    assert local.code == 0, local.text


@pytest.mark.id("VERCEL-10")
def test_vercel10_clean_preview_then_yes_deletes_only_test_houses_and_users(
    harness: Harness, migrated_db: str
) -> None:
    seed(migrated_db)
    tag_users(migrated_db)  # the whole apt4b house becomes launch-check data
    real = add_lone_user(migrated_db, "real.person@gmail.com")
    lone_test = add_lone_user(migrated_db, "lone" + TAG)
    before = counts(migrated_db)
    assert (
        before["houses"] == 1 and before["bills"] > 0 and before["inventory_items"] > 0
    )

    preview = run_local(harness, migrated_db)
    assert preview.code == 0, preview.text
    assert (
        "Would delete:" in preview.out and "Preview only (rolled back)" in preview.out
    )
    assert counts(migrated_db) == before  # nothing changed

    run = run_local(harness, migrated_db, "--yes")
    assert run.code == 0, run.text
    assert "Committed." in run.out
    after = counts(migrated_db)
    assert after["houses"] == 0 and after["bills"] == 0 and after["members"] == 0
    assert (
        after["house_events"] == 0
        and after["outbox"] == 0
        and after["inventory_items"] == 0
    )
    remaining = sql(migrated_db, "SELECT id::text, email::text FROM users")[0]
    assert remaining == [(real, "real.person@gmail.com")]
    assert (
        sql(migrated_db, f"SELECT count(*) FROM sessions WHERE user_id = '{real}'")[0][
            0
        ][0]
        == 1
    )
    assert (
        sql(
            migrated_db, f"SELECT count(*) FROM sessions WHERE user_id = '{lone_test}'"
        )[0][0][0]
        == 0
    )
    inbox = sql(migrated_db, "SELECT to_address::text FROM test_inbox")[0]
    assert inbox == [("real.person@gmail.com",)]
    # The preview promised exactly what --yes deleted.
    for line in preview.out.splitlines():
        if line.startswith("  ") and line.split()[-1].isdigit():
            table, n = line.split()[0], int(line.split()[-1])
            assert before[table] - after[table] == n, table


@pytest.mark.id("VERCEL-10")
def test_vercel10_clean_keeps_a_house_any_real_user_is_in(
    harness: Harness, migrated_db: str
) -> None:
    seed(migrated_db)
    email = sql(migrated_db, "SELECT email::text FROM users ORDER BY email LIMIT 1")[0][
        0
    ][0]
    tag_users(migrated_db, except_email=email)
    before = counts(migrated_db)
    run = run_local(harness, migrated_db, "--yes")
    assert run.code == 0, run.text
    after = counts(migrated_db)
    assert (
        after == before
    )  # every test user is still referenced by a real house: all kept
    assert "kept because a real house still references them" in run.out


@pytest.mark.id("VERCEL-10")
def test_vercel10_clean_without_tagged_rows_deletes_nothing(
    harness: Harness, migrated_db: str
) -> None:
    seed(migrated_db)
    before = counts(migrated_db)
    run = run_local(harness, migrated_db, "--yes")
    assert run.code == 0, run.text
    assert "Nothing to delete." in run.out
    assert counts(migrated_db) == before


@pytest.mark.id("VERCEL-10")
def test_vercel10_clean_production_path_pulls_the_direct_url_and_hides_it(
    harness: Harness, migrated_db: str
) -> None:
    seed(migrated_db)
    tag_users(migrated_db)
    harness.provision()
    state = harness.state()
    state["env"]["production"]["DATABASE_URL_UNPOOLED"] = _plain(migrated_db)
    harness.save_state(state)
    before = counts(migrated_db)
    run = harness.run(SCRIPT)
    assert run.code == 0, run.text
    assert counts(migrated_db) == before  # no --yes: preview only
    assert ["whoami"] in harness.vercel_argvs()
    pulled = harness.state()["pulled_files"]
    assert pulled and not any(os.path.exists(p) for p in pulled)
    harness.assert_no_secrets(run)
    from urllib.parse import urlsplit

    password = urlsplit(_plain(migrated_db)).password
    if password and len(password) >= 6:
        assert password not in run.text


@pytest.mark.id("VERCEL-10")
def test_vercel10_clean_knows_every_foreign_key_to_users_and_house_data(
    migrated_db: str,
) -> None:
    """A new table pointing at users or house data must be added to the cleanup plan."""
    sys.path.insert(0, str(ROOT / "scripts" / "deploy"))
    import prod_clean_test_data as plan

    rows = sql(
        migrated_db,
        """SELECT tc.table_name, kcu.column_name, ccu.table_name
           FROM information_schema.table_constraints tc
           JOIN information_schema.key_column_usage kcu
             ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
           JOIN information_schema.constraint_column_usage ccu
             ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
           WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'""",
    )[0]
    assert {(t, c) for t, c, ref in rows if ref == "users"} == plan.USER_FKS
    house_tables = {t for t, _ in plan.HOUSE_DELETES} | {"outbox"}
    for table, _, ref in rows:
        if ref in house_tables and ref != "users":
            assert table in house_tables, (
                f"{table} references {ref} but isn't in the cleanup plan"
            )
    assert {t for t, c in plan.USER_FKS if t not in house_tables} <= set(
        plan.USER_CASCADES
    ) | {"houses"}
