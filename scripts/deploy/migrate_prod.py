"""`make migrate-prod`: apply Alembic migrations to the production (Neon) database.

Git pushes to `main` deploy through Vercel's Git integration, which builds and ships the code but
never touches the database. When a change adds a migration, run this BEFORE merging it to `main`
(migrations here are additive: new columns/tables first, code that uses them second). Needs the
Vercel CLI logged in to the account that owns the project; the database URL is read with
`vercel env pull` into a temp file outside the repo and deleted right after.

    uv run --project apps/api python scripts/deploy/migrate_prod.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys

from deploylib import (
    EXIT_OK,
    Console,
    DeployError,
    Runner,
    Vercel,
    asyncpg_sqlalchemy_url,
    direct_database_url,
    main_guard,
    repo_root,
)

TARGET = "migrate-prod"


def migrate(console: Console, runner: Runner) -> int:
    vercel = Vercel(runner)
    runner.announce_vercel()
    vercel.require_linked()
    vercel.whoami()
    console.step(
        "1. Production database URL (pulled to a temp file, deleted after reading)"
    )
    env = vercel.env_pull("production")
    direct = (
        "postgresql://<neon-direct-url>" if runner.dry_run else direct_database_url(env)
    )
    console.step("2. Alembic upgrade head (Neon direct URL)")
    try:
        runner.run(
            "uv",
            ["run", "alembic", "upgrade", "head"],
            what="run the database migrations",
            cwd=runner.root / "apps" / "api",
            env={"DATABASE_DIRECT_URL": asyncpg_sqlalchemy_url(direct)},
            env_note="DATABASE_DIRECT_URL=<Neon direct URL>",
        )
    except DeployError as err:
        raise err.wrap(
            "The migration failed; production keeps its current schema.",
            "Fix the migration and run `make migrate-prod` again. Neon can restore the database to an "
            "earlier point if a migration half-applied (Neon console -> Restore).",
        ) from err
    console.step("Done")
    console.say(
        "Production schema is at head. Merge the change to main to deploy the code."
    )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply migrations to the production database."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print every command, run none"
    )
    args = parser.parse_args(argv)
    console = Console(args.dry_run)
    runner = Runner(console, repo_root(), TARGET)
    return main_guard(console, lambda: migrate(console, runner))


if __name__ == "__main__":
    sys.exit(main())
