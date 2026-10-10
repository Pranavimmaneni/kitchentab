"""`make redeploy`: rebuild the live production deployment with the current environment variables
(after `vercel env add/rm`, or when a deploy needs a fresh build). No code changes, no tests, no
migrations: code changes go through `make deploy`. ADR-0022.

    uv run --project apps/api python scripts/deploy/redeploy.py [--dry-run] [--url https://...]
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
    main_guard,
    read_state,
    repo_root,
    write_state,
)

TARGET = "redeploy"


def redeploy_live(console: Console, vercel: Vercel, origin: str | None) -> str:
    """Rebuild whatever the production domain serves now; returns the new deployment URL."""
    current = vercel.current_production(origin)
    console.say(f"Live production deployment: {current}")
    try:
        new = vercel.redeploy(current)
    except DeployError as err:
        raise err.wrap(
            "The redeploy failed. The current production deployment is still live.",
            f"Read the build log above, fix the cause, then re-run `make {vercel.runner.target}`.",
        ) from err
    console.say(f"Redeployed: {new}")
    if not vercel.runner.dry_run:
        write_state(vercel.runner.root, last_deployment=new)
    return new


def redeploy(console: Console, runner: Runner, url: str | None) -> int:
    vercel = Vercel(runner)
    dry = runner.dry_run
    console.say(
        f"KitchenTab redeploy{'  [dry run: printing commands, running none]' if dry else ''}"
    )
    runner.announce_vercel()
    vercel.require_linked()
    vercel.whoami()
    origin = url or read_state(runner.root).get("production_url")
    redeploy_live(console, vercel, origin)
    if origin:
        console.say(f"Production: {origin}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild production with the current env vars."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print every command, run none"
    )
    parser.add_argument(
        "--url", help="production origin (default: the one `make deploy` recorded)"
    )
    args = parser.parse_args(argv)
    console = Console(args.dry_run)
    runner = Runner(console, repo_root(), TARGET)
    return main_guard(console, lambda: redeploy(console, runner, args.url))


if __name__ == "__main__":
    sys.exit(main())
