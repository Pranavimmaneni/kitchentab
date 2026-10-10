"""`make deploy`: ship the current checkout to production (BUILD_ALL §6 step 3, ADR-0022).

1. lint + unit tests (`make lint test`);
2. pull the production env into a temp file outside the repo (deleted right away) to get Neon's
   direct URL and the current origin settings;
3. Alembic migrations against the direct URL (never from a request, ADR-0011);
4. `vercel deploy --prod`, print the URL;
5. set WEB_ORIGIN, PUBLIC_LINK_BASE, GOOGLE_REDIRECT_URI (and VAPID_SUBJECT if unset) from the
   production domain and redeploy if any of them changed.

    uv run --project apps/api python scripts/deploy/deploy.py [--dry-run] [--domain https://...]

`--skip-checks` exists only for the dry-run tests and is refused without `--dry-run`.
"""

from __future__ import annotations

import argparse
import sys

from deploylib import (
    EXIT_OK,
    EXIT_USAGE,
    GOOGLE_CALLBACK_PATH,
    Console,
    DeployError,
    Runner,
    Vercel,
    asyncpg_sqlalchemy_url,
    choose_production_origin,
    direct_database_url,
    main_guard,
    repo_root,
    write_state,
)

TARGET = "deploy"
REQUIRED = (
    "APP_ENV",
    "PROFILE",
    "SESSION_SECRET",
    "TOKEN_HASH_PEPPER",
    "CRON_SECRET",
    "QUEUE_PROVIDER",
    "STORAGE_PROVIDER",
    "PUSH_PROVIDER",
    "SMS_PROVIDER",
    "EXTRACTOR_PROVIDER",
    "EMAIL_PROVIDER",
    "VAPID_PUBLIC_KEY",
    "VAPID_PRIVATE_KEY",
    "DATABASE_URL",
    "BLOB_READ_WRITE_TOKEN",
)


def missing_settings(env: dict[str, str]) -> list[str]:
    # A sensitive secret is listed by `vercel env pull` with an empty value: present means listed.
    missing = [k for k in REQUIRED if k not in env]
    provider = env.get("EMAIL_PROVIDER")
    if provider == "testinbox" and "TEST_INBOX_SECRET" not in env:
        missing.append("TEST_INBOX_SECRET")
    if provider == "smtp":
        missing += [
            k for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD") if k not in env
        ]
    return missing


def origin_settings(origin: str, current: dict[str, str]) -> dict[str, str]:
    wanted = {
        "WEB_ORIGIN": origin,
        "PUBLIC_LINK_BASE": origin,
        "GOOGLE_REDIRECT_URI": origin + GOOGLE_CALLBACK_PATH,
    }
    if not current.get("VAPID_SUBJECT"):
        wanted["VAPID_SUBJECT"] = (
            origin  # push services want a real contact URL or mailto:
        )
    return wanted


def deploy(
    console: Console, runner: Runner, *, skip_checks: bool, domain: str | None
) -> int:
    vercel = Vercel(runner)
    dry = runner.dry_run
    console.say(
        f"KitchenTab deploy{'  [dry run: printing commands, running none]' if dry else ''}"
    )
    runner.announce_vercel()
    vercel.require_linked()
    if not dry and not (runner.root / "vercel.json").exists():
        raise DeployError(
            "vercel.json is missing at the repo root, so Vercel wouldn't know about the two services.",
            hint="See ADR-0011; restore vercel.json, then re-run `make deploy`.",
        )

    console.step("1. Vercel login")
    vercel.whoami()

    console.step("2. Lint and unit tests")
    if skip_checks:
        console.say("Skipped (--skip-checks, dry run only).")
    else:
        try:
            runner.run(
                "make",
                ["lint", "test"],
                what="pass lint and unit tests",
                interactive=True,
            )
        except DeployError as err:
            raise DeployError(
                "Lint or unit tests failed, so nothing was deployed.",
                hint="Fix the failures above (Docker must be running for the API tests), then re-run `make deploy`.",
            ) from err

    console.step(
        "3. Production settings (pulled to a temp file outside the repo, deleted right after reading)"
    )
    env = vercel.env_pull("production")
    if not dry:
        missing = missing_settings(env)
        if missing:
            raise DeployError(
                f"Production is missing: {', '.join(missing)}.",
                hint="Run `make deploy-setup` (it adds what's missing and keeps the rest), then `make deploy`.",
            )
        console.say(f"Read {len(env)} production variables (values hidden).")

    console.step("4. Database migrations (Alembic, Neon direct URL)")
    direct = "postgresql://<neon-direct-url>" if dry else direct_database_url(env)
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
            "Database migrations failed, so nothing was deployed (production keeps running the old version).",
            "Fix the migration, then re-run `make deploy`. If a migration half-applied, Neon can restore "
            "the database to an earlier point (Neon console -> Restore).",
        ) from err

    console.step("5. Deploy to production")
    try:
        url = vercel.deploy_prod()
    except DeployError as err:
        raise err.wrap(
            "The Vercel deploy failed. The previous production deployment is still live.",
            "Read the build log above (or `vercel inspect --logs <url>`), fix it, then re-run `make deploy`. "
            "Migrations already applied are kept and are safe to re-run.",
        ) from err
    console.say(f"Deployed: {url}")

    console.step("6. Production URL settings")
    if domain:
        origin = domain.rstrip("/")
    elif dry:
        origin = "https://kitchentab.vercel.app"
        vercel.inspect(url)
    else:
        _, aliases = vercel.inspect(url)
        found = choose_production_origin(aliases)
        if not found:
            raise DeployError(
                f"Deployed to {url}, but I couldn't find its production domain in `vercel inspect`.",
                hint="Re-run with the domain, e.g. `make deploy ARGS=--domain=https://kitchentab.vercel.app`.",
            )
        origin = found
    if not origin.startswith("https://") or "localhost" in origin:
        raise DeployError(
            f"The production URL must be https and not localhost (got {origin})."
        )
    wanted = origin_settings(origin, env)
    changed = [name for name, value in wanted.items() if dry or env.get(name) != value]
    for name in changed:
        if name in env:
            vercel.env_rm(name, "production")
        vercel.env_add(name, "production", wanted[name], note=wanted[name])
    if not changed:
        console.say(
            "WEB_ORIGIN, PUBLIC_LINK_BASE and GOOGLE_REDIRECT_URI are already right."
        )
    else:
        console.step("7. Redeploy so the new settings take effect")
        url = vercel.redeploy(url)
        console.say(f"Redeployed: {url}")

    if not dry:
        write_state(runner.root, production_url=origin, last_deployment=url)
    console.step("Done")
    console.say(f"Production: {origin}")
    console.say("Next: `make launch-check TARGET=prod`.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deploy KitchenTab to production.")
    parser.add_argument(
        "--dry-run", action="store_true", help="print every command, run none"
    )
    parser.add_argument(
        "--skip-checks",
        action="store_true",
        help="dry-run tests only: leave out lint and tests",
    )
    parser.add_argument(
        "--domain",
        help="production origin if it can't be detected, e.g. https://kitchentab.vercel.app",
    )
    args = parser.parse_args(argv)
    console = Console(args.dry_run)
    if args.skip_checks and not args.dry_run:
        console.error(
            DeployError(
                "--skip-checks is only allowed with --dry-run.", code=EXIT_USAGE
            )
        )
        return EXIT_USAGE
    runner = Runner(console, repo_root(), TARGET)
    return main_guard(
        console,
        lambda: deploy(
            console, runner, skip_checks=args.skip_checks, domain=args.domain
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
