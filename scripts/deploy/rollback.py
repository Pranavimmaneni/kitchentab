"""`make rollback`: point production back at an earlier deployment (ADR-0022).

Lists recent ready production deployments, asks which one (default: the newest one that isn't
live), promotes it with `vercel promote`, and checks the domain now serves it. Database migrations
are not rolled back: Alembic migrations are additive, so the previous code keeps working.

    uv run --project apps/api python scripts/deploy/rollback.py [--dry-run] [--to N|URL] [--yes]
"""

from __future__ import annotations

import argparse
import sys

from deploylib import (
    EXIT_OK,
    Console,
    DeployError,
    DeploymentRow,
    Runner,
    Vercel,
    main_guard,
    read_state,
    repo_root,
    write_state,
)

TARGET = "rollback"


def pick(
    candidates: list[DeploymentRow], current: str, choice: str | None, *, ask: bool
) -> DeploymentRow:
    default = next(
        (i for i, row in enumerate(candidates, 1) if row.url != current), None
    )
    if default is None:
        raise DeployError(
            "There is no earlier ready production deployment to roll back to.",
            hint="`vercel ls kitchentab` shows every deployment.",
        )
    if choice is None and ask:
        try:
            answer = input(f"Roll back to which deployment? [{default}] ").strip()
        except EOFError:
            answer = ""
        choice = answer or None
    if choice is None:
        return candidates[default - 1]
    if choice.isdigit() and 1 <= int(choice) <= len(candidates):
        found: DeploymentRow | None = candidates[int(choice) - 1]
    else:
        found = next((r for r in candidates if r.url == choice.rstrip("/")), None)
    if found is None:
        raise DeployError(f"'{choice}' isn't one of the listed deployments.")
    row = found
    if row.url == current:
        raise DeployError("That deployment is already live; pick another one.")
    return row


def rollback(
    console: Console, runner: Runner, *, choice: str | None, yes: bool, url: str | None
) -> int:
    vercel = Vercel(runner)
    dry = runner.dry_run
    console.say(
        f"KitchenTab rollback{'  [dry run: printing commands, running none]' if dry else ''}"
    )
    runner.announce_vercel()
    vercel.require_linked()
    vercel.whoami()
    origin = url or read_state(runner.root).get("production_url")

    rows = vercel.deployments()
    if dry:
        if origin:
            vercel.inspect(origin)
        target = (
            choice if choice and not choice.isdigit() else "<chosen-deployment-url>"
        )
        vercel.run(["promote", target, "--yes"], what="promote the chosen deployment")
        if origin:
            vercel.inspect(origin)
        return EXIT_OK

    current = vercel.current_production(origin)
    candidates = [
        r
        for r in rows
        if r.environment.lower() == "production" and r.status.lower() == "ready"
    ]
    console.say("\nRecent production deployments (newest first):")
    for i, row in enumerate(candidates, 1):
        live = "  <- live now" if row.url == current else ""
        console.say(f"  {i}. {row.url}  ({row.age} ago){live}")
    chosen = pick(candidates, current, choice, ask=not yes)

    console.say(f"\nPromoting {chosen.url} to production.")
    try:
        vercel.run(["promote", chosen.url, "--yes"], what=f"promote {chosen.url}")
    except DeployError as err:
        raise err.wrap(
            "The rollback failed; production is unchanged.",
            "Try again with `make rollback`, or use the dashboard: Deployments -> the deployment -> "
            "... -> Promote to Production (Instant Rollback).",
        ) from err
    if origin:
        live, _ = vercel.inspect(origin, check=False)
        if live and live != chosen.url:
            raise DeployError(
                f"Promoted {chosen.url}, but {origin} still serves {live}.",
                hint="Wait a minute and check `vercel inspect "
                + origin
                + "`; aliases can lag briefly.",
            )
    write_state(runner.root, last_deployment=chosen.url)
    console.say(f"Rolled back. Production now serves {chosen.url}.")
    console.say(
        "Note: database migrations were not rolled back (they are additive). Ship the fix with `make deploy`."
    )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Roll production back to an earlier deployment."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print every command, run none"
    )
    parser.add_argument(
        "--to", dest="choice", help="number from the list, or a deployment URL"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="don't ask; take --to or the newest earlier one",
    )
    parser.add_argument(
        "--url", help="production origin (default: the one `make deploy` recorded)"
    )
    args = parser.parse_args(argv)
    console = Console(args.dry_run)
    runner = Runner(console, repo_root(), TARGET)
    return main_guard(
        console,
        lambda: rollback(
            console, runner, choice=args.choice, yes=args.yes, url=args.url
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
