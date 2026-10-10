"""`make deploy-setup`: one-time Vercel setup for KitchenTab (BUILD_ALL §6 step 3, ADR-0022).

Checks the login, links this folder to the `kitchentab` project, adds Neon (free plan) and a Blob
store, prints the dashboard steps the CLI can't do (Services framework, Queues beta), then
generates and sets every environment variable the API's deployed-mode validation needs, for
production and preview. Safe to re-run: anything that already exists is skipped, and an existing
secret is never regenerated. WEB_ORIGIN, PUBLIC_LINK_BASE and GOOGLE_REDIRECT_URI are set by
`make deploy` once the production URL is known.

    uv run --project apps/api python scripts/deploy/deploy_setup.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from deploylib import (
    BLOB_STORE,
    ENVIRONMENTS,
    EXIT_ACTION_NEEDED,
    EXIT_OK,
    PROJECT,
    Console,
    DeployError,
    Runner,
    Secret,
    Vercel,
    main_guard,
    new_secret,
    new_vapid_keys,
    repo_root,
)

# Generated per environment (production and preview get different values). Order matters for
# resumability: TEST_INBOX_SECRET goes in before EMAIL_PROVIDER (see `_wanted`).
SECRETS: dict[
    str, int
] = {  # name -> random bytes (token_urlsafe: ~1.33 chars per byte)
    "SESSION_SECRET": 64,
    "TOKEN_HASH_PEPPER": 48,
    "CRON_SECRET": 48,
    "TEST_INBOX_SECRET": 64,  # 86 chars; settings require >= 64 (ADR-0016)
}
# Fixed values the deployed-mode validation in apps/api/app/settings.py requires. APP_ENV is per
# environment (APP_ENVS); both values count as deployed (`Settings.is_deployed`). Rate limits are
# left at their defaults (60 writes / 300 reads per minute; production refuses 0).
APP_ENVS = {"production": "prod", "preview": "staging"}
CONFIG: dict[str, str] = {
    "APP_ENV": "prod",  # replaced per environment from APP_ENVS
    "PROFILE": "vercel",
    "QUEUE_PROVIDER": "vercel",
    "STORAGE_PROVIDER": "vercel_blob",
    "PUSH_PROVIDER": "webpush",
    "SMS_PROVIDER": "none",
    "EXTRACTOR_PROVIDER": "local",
    "EMAIL_PROVIDER": "testinbox",  # `make set-email` switches this to smtp
}
VAPID = ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY")
POSTAL = "EMAIL_POSTAL_ADDRESS"  # optional; never overwritten once set
# Provided by the Marketplace integrations, not by us.
NEON_VAR = "DATABASE_URL"
BLOB_VAR = "BLOB_READ_WRITE_TOKEN"
TARGET = "deploy-setup"


@dataclass
class Report:
    added: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)


def _wanted(existing: set[str]) -> list[str]:
    """Variables to add, in order. TEST_INBOX_SECRET is only added while EMAIL_PROVIDER is not
    set yet: once it is (to testinbox here, or to smtp by `make set-email`, which also removes
    TEST_INBOX_SECRET), a missing test inbox secret is deliberate."""
    names = [n for n in SECRETS if n not in existing]
    if "EMAIL_PROVIDER" in existing and "TEST_INBOX_SECRET" in names:
        names.remove("TEST_INBOX_SECRET")
    names += [n for n in CONFIG if n not in existing]
    return names


def _postal_address(
    console: Console, given: str | None, existing: dict[str, set[str]], dry: bool
) -> str:
    """Optional: from --postal-address, else asked for at a terminal (Enter skips)."""
    if all(POSTAL in names for names in existing.values()) and not dry:
        return ""
    if given is not None:
        return given.strip()
    if dry or not sys.stdin.isatty():
        return ""
    try:
        return input(
            f"{POSTAL} for email footers (e.g. 'KitchenTab, 1 Main St, City, ST 00000'; Enter to skip): "
        ).strip()
    except EOFError:
        return ""


def setup(console: Console, runner: Runner, postal_arg: str | None = None) -> int:
    vercel = Vercel(runner)
    report = Report()
    dry = runner.dry_run
    console.say(
        f"KitchenTab deploy-setup{'  [dry run: printing commands, running none]' if dry else ''}"
    )
    runner.announce_vercel()
    if dry:
        console.say(
            "(A dry run assumes a brand-new project; a real run skips whatever already exists.)"
        )

    console.step("1. Vercel login")
    vercel.whoami()

    console.step(f"2. Link this folder to the Vercel project '{PROJECT}'")
    linked = None if dry else vercel.linked_project()
    if linked == PROJECT:
        console.say(f"Already linked to {PROJECT} (.vercel/project.json). Skipping.")
    else:
        if linked:
            console.warn(
                f"This folder is linked to '{linked}'; relinking it to '{PROJECT}'."
            )
        vercel.run(
            ["link", "--yes", "--project", PROJECT],
            what=f"link this folder to the Vercel project '{PROJECT}'",
        )

    existing = {env: (set() if dry else vercel.env_names(env)) for env in ENVIRONMENTS}

    console.step("3. Postgres: Neon from the Vercel Marketplace (free plan)")
    if all(NEON_VAR in names for names in existing.values()):
        console.say(f"Neon is connected ({NEON_VAR} exists). Skipping.")
    else:
        console.say(
            "The Neon installer asks a few questions. Choose: plan Free; region Washington, D.C. "
            "(iad1) or the one nearest you;\nconnect it to the project 'kitchentab' for Production, "
            "Preview and Development; leave the environment variable prefix empty."
        )
        vercel.run(
            ["integration", "add", "neon"],
            what="add the Neon integration",
            interactive=True,
        )
        if not dry:
            existing = {env: vercel.env_names(env) for env in ENVIRONMENTS}
            if not all(NEON_VAR in names for names in existing.values()):
                report.actions.append(
                    "Connect Neon to the project: vercel.com -> your team -> Storage -> the Neon "
                    "database -> Connect Project -> kitchentab, environments Production and "
                    f"Preview (it adds {NEON_VAR} and DATABASE_URL_UNPOOLED)."
                )

    console.step("4. Receipt storage: Vercel Blob store")
    if all(BLOB_VAR in names for names in existing.values()):
        console.say(f"A Blob store is connected ({BLOB_VAR} exists). Skipping.")
    else:
        console.say(
            f"Creating the Blob store '{BLOB_STORE}'. If asked, link it to 'kitchentab'."
        )
        res = vercel.run(
            # CLI 54: `blob create-store`; --yes connects it to every environment of the linked project.
            ["blob", "create-store", BLOB_STORE, "--access", "private", "--yes"],
            what=f"create the Blob store '{BLOB_STORE}'",
            check=False,
        )
        if res.code != 0 and "already" not in res.text.lower():
            raise runner.failure(
                "vercel",
                ["blob", "create-store"],
                f"create the Blob store '{BLOB_STORE}'",
                res,
            )
        if not dry:
            existing = {env: vercel.env_names(env) for env in ENVIRONMENTS}
            if not all(BLOB_VAR in names for names in existing.values()):
                report.actions.append(
                    f"Connect the Blob store: vercel.com -> your team -> Storage -> {BLOB_STORE} -> "
                    "Connect Project -> kitchentab, environments Production and Preview (it adds "
                    f"{BLOB_VAR})."
                )

    console.step("5. Dashboard-only settings (the CLI has no command for these)")
    console.say(
        "  a. Services framework (ADR-0011): vercel.com -> kitchentab -> Settings -> Build and "
        "Deployment ->\n     Framework Preset -> Services -> Save.\n"
        "  b. Vercel Queues (beta, outbox delivery, ADR-0013): vercel.com -> kitchentab -> Queues "
        "tab -> Enable,\n     if the tab offers it. The consumer is declared in apps/api/pyproject.toml "
        "([[tool.vercel.subscribers]])\n     and registers itself on each deploy; nothing else to "
        "configure."
    )

    console.step("6. Environment variables (production and preview)")
    postal = _postal_address(console, postal_arg, existing, dry)
    for env in ENVIRONMENTS:
        names = existing[env]
        for name in [*SECRETS, *CONFIG, *VAPID]:
            if name in names:
                report.kept.append(f"{name} ({env})")
        _set_vapid(console, vercel, env, names, report)
        for name in _wanted(names):
            if name in SECRETS:
                value: str | Secret = new_secret(SECRETS[name])
                console.register(value)
                note = (
                    str(value) if not dry else f"<generated secret: {len(value)} chars>"
                )
            else:
                value = APP_ENVS[env] if name == "APP_ENV" else CONFIG[name]
                note = value
            vercel.env_add(name, env, value, note=note)
            report.added.append(f"{name} ({env})")
        if POSTAL in names:
            report.kept.append(f"{POSTAL} ({env})")
        elif postal:
            vercel.env_add(POSTAL, env, postal, note="<postal address>")
            report.added.append(f"{POSTAL} ({env})")
    for item in report.kept:
        console.say(f"  kept (already set, never regenerated): {item}")

    console.step("Summary")
    console.say(
        f"{'Would add' if dry else 'Added'} {len(report.added)} variables; kept {len(report.kept)}."
    )
    console.say(
        "WEB_ORIGIN, PUBLIC_LINK_BASE and GOOGLE_REDIRECT_URI are set by `make deploy` after the first deploy."
    )
    if not all(POSTAL in names for names in existing.values()) and not postal:
        console.say(
            f"Note: {POSTAL} (the postal address in notification email footers, required by "
            "anti-spam law before\nreal launch) is empty. Set it later with `make deploy-setup "
            "ARGS='--postal-address \"...\"'`, then `make redeploy`."
        )
    if report.actions:
        console.say("\nACTION NEEDED before `make deploy`:")
        for i, action in enumerate(report.actions, 1):
            console.say(f"  {i}. {action}")
        console.say("Then re-run `make deploy-setup` to check (it skips what's done).")
        return EXIT_ACTION_NEEDED
    console.say("Done. Next: `make deploy`.")
    return EXIT_OK


def _set_vapid(
    console: Console, vercel: Vercel, env: str, names: set[str], report: Report
) -> None:
    """The VAPID pair is generated together. Private goes first: a run that dies between the two
    leaves only the private key, which this function replaces with a fresh pair (nothing can be
    subscribed with a pair that was never complete)."""
    public, private = VAPID
    have_public, have_private = public in names, private in names
    if have_public and have_private:
        return
    if have_public and not have_private:
        raise DeployError(
            f"{public} is set for {env} but {private} isn't, so the pair can't be completed.",
            hint=f"Remove the orphan with `vercel env rm {public} {env} --yes`, then re-run `make {TARGET}`.",
        )
    if have_private:
        console.say(
            f"{private} ({env}) exists without {public}: replacing it with a new pair."
        )
        vercel.env_rm(private, env)
    pub, priv = new_vapid_keys()
    console.register(priv)
    note = (
        str(priv)
        if not vercel.runner.dry_run
        else f"<generated P-256 private key: {len(priv)} chars>"
    )
    vercel.env_add(private, env, priv, note=note)
    vercel.env_add(public, env, pub, note=f"<public key: {len(pub)} chars>")
    report.added += [f"{private} ({env})", f"{public} ({env})"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-time Vercel setup for KitchenTab."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print every command, run none"
    )
    parser.add_argument(
        "--postal-address", help="EMAIL_POSTAL_ADDRESS for email footers (optional)"
    )
    args = parser.parse_args(argv)
    console = Console(args.dry_run)
    runner = Runner(console, repo_root(), TARGET)
    return main_guard(console, lambda: setup(console, runner, args.postal_address))


if __name__ == "__main__":
    sys.exit(main())
