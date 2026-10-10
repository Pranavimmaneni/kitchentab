"""A fake `vercel` CLI for the deploy-script tests (ADR-0022). Never talks to the network.

State lives in $FAKE_VERCEL_STATE/state.json (the fake "Vercel account": env var values,
deployments, aliases) and every call is appended to $FAKE_VERCEL_STATE/calls.json as
{"tool", "exe", "argv", "cwd", "stdin_chars", "exit"}. Stdin values are never logged.

Failure switches, comma-separated in $FAKE_VERCEL_FAIL:
  whoami         not logged in
  link           `vercel link` fails
  env-exists     `env ls` hides existing variables (so `env add` hits "already been added")
  deploy         `deploy` and `redeploy` fail with a build error
  network@N      call number N and every later call fail with a network error
  integration    `integration add` fails
  promote        `promote` fails
  neon-unlinked  `integration add neon` succeeds but connects nothing
  blob-unlinked  `blob create-store` succeeds but connects nothing
Output mimics Vercel CLI 48: data on stdout, chatter on stderr.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

VERSION = "Vercel CLI 48.2.1"
TEAM = "pranavs-projects"
ENVS = ("production", "preview", "development")

for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

STATE_DIR = Path(os.environ["FAKE_VERCEL_STATE"])
STATE = STATE_DIR / "state.json"
CALLS = STATE_DIR / "calls.json"
FAIL = {
    f.strip() for f in os.environ.get("FAKE_VERCEL_FAIL", "").split(",") if f.strip()
}


def load() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {
        "logged_in": True,
        "user": "pranav",
        "env": {e: {} for e in ENVS},
        "integrations": [],
        "blob_stores": [],
        "deployments": [],
        "aliases": [],
        "current": None,
        "calls": 0,
    }


def save(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def log(argv: list[str], stdin_chars: int, code: int) -> None:
    calls = json.loads(CALLS.read_text(encoding="utf-8")) if CALLS.exists() else []
    calls.append(
        {
            "tool": "vercel",
            "exe": str(Path(__file__).resolve()),
            "argv": argv,
            "cwd": os.getcwd(),
            "stdin_chars": stdin_chars,
            "exit": code,
        }
    )
    CALLS.write_text(json.dumps(calls, indent=2), encoding="utf-8")


def err(*lines: str) -> None:
    for line in lines:
        print(line, file=sys.stderr)


def fail(message: str, code: int = 1) -> int:
    err(f"Error: {message}")
    return code


def new_url() -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    slug = "".join(secrets.choice(alphabet) for _ in range(9))
    return f"https://kitchentab-{slug}-{TEAM}.vercel.app"


def linked(state: dict) -> bool:
    return (Path.cwd() / ".vercel" / "project.json").exists() and state.get(
        "project"
    ) == "kitchentab"


def make_deployment(state: dict, target: str) -> dict:
    deployment = {
        "url": new_url(),
        "target": target,
        "status": "Ready",
        "env_names": sorted(state["env"]["production"]),
        "web_origin": state["env"]["production"].get("WEB_ORIGIN"),
        "email_provider": state["env"]["production"].get("EMAIL_PROVIDER"),
    }
    state["deployments"].append(deployment)
    if target == "production":
        state["current"] = deployment["url"]
        if not state["aliases"]:
            state["aliases"] = [
                "https://kitchentab.vercel.app",
                f"https://kitchentab-{TEAM}.vercel.app",
                f"https://kitchentab-git-main-{TEAM}.vercel.app",
            ]
    return deployment


def cmd_env(state: dict, args: list[str], stdin: str) -> int:
    sub, rest = (args[0], args[1:]) if args else ("", [])
    if sub == "ls":
        environment = rest[0] if rest else "production"
        rows = {} if "env-exists" in FAIL else state["env"].get(environment, {})
        err(f"> Environment Variables found for {TEAM}/kitchentab [182ms]")
        if not rows:
            err(f"> No Environment Variables found for {TEAM}/kitchentab [182ms]")
            return 0
        print(
            " name                          value               environments        created"
        )
        for name in sorted(rows):
            print(f" {name:<29} Encrypted           {environment.title():<19} 1m ago")
        return 0
    if sub == "add":
        if len(rest) < 2:
            return fail("Missing required arguments")
        name, environment = rest[0], rest[1]
        if name in state["env"][environment]:
            return fail(
                f'The variable "{name}" has already been added to {environment.title()}. '
                f"To remove, run `vercel env rm {name}`."
            )
        if not stdin:
            return fail("No value given (stdin was empty)")
        state["env"][environment][name] = stdin
        err(f"Added Environment Variable {name} to Project kitchentab [94ms]")
        return 0
    if sub == "rm":
        name, environment = rest[0], rest[1]
        if "--yes" not in rest:
            return fail("Removing needs confirmation; pass --yes")
        if name not in state["env"][environment]:
            return fail("Environment Variable was not found.")
        del state["env"][environment][name]
        err(f"Removed Environment Variable {name} [77ms]")
        return 0
    if sub == "pull":
        target = Path(rest[0])
        environment = next(
            (a.split("=", 1)[1] for a in rest if a.startswith("--environment=")),
            "development",
        )
        lines = ["# Created by Vercel CLI"]
        for name, value in sorted(state["env"][environment].items()):
            escaped = (
                value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            )
            lines.append(f'{name}="{escaped}"')
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        state.setdefault("pulled_files", []).append(str(target))
        err(
            f"> Downloading `{environment}` Environment Variables for {TEAM}/kitchentab"
        )
        err(f"Created {target.name} file [312ms]")
        return 0
    return fail(f"Unknown env subcommand {sub}")


def cmd_integration(state: dict, args: list[str]) -> int:
    if args[:2] != ["add", "neon"]:
        return fail("Unknown integration command")
    if "integration" in FAIL:
        return fail("Failed to install integration neon (402: plan limit)")
    state["integrations"].append("neon")
    if "neon-unlinked" not in FAIL:
        password = "npg_" + secrets.token_hex(8)
        host = "ep-fancy-snow-a5k2j3l4"
        for environment in ENVS:
            env = state["env"][environment]
            env["DATABASE_URL"] = (
                f"postgresql://neondb_owner:{password}@{host}-pooler.us-east-2.aws.neon.tech/neondb"
                "?sslmode=require&channel_binding=require"
            )
            env["DATABASE_URL_UNPOOLED"] = (
                f"postgresql://neondb_owner:{password}@{host}.us-east-2.aws.neon.tech/neondb"
                "?sslmode=require&channel_binding=require"
            )
            env["PGPASSWORD"] = password
    print("? Choose a billing plan: Free")
    err("Neon installed and connected to kitchentab (Production, Preview, Development)")
    return 0


def cmd_blob(state: dict, args: list[str]) -> int:
    # CLI 54: `vercel blob create-store <name> --access private --yes`
    if len(args) < 2 or args[0] != "create-store" or "--access" not in args:
        return fail("Usage: vercel blob create-store [name] --access <public|private>")
    name = args[1]
    if name in state["blob_stores"]:
        return fail(f'A Blob store named "{name}" already exists')
    state["blob_stores"].append(name)
    if "blob-unlinked" not in FAIL:
        token = "vercel_blob_rw_" + secrets.token_urlsafe(24)
        for environment in ENVS:
            state["env"][environment]["BLOB_READ_WRITE_TOKEN"] = token
    err(f'Success! Blob store "{name}" created and linked to kitchentab')
    return 0


def cmd_inspect(state: dict, args: list[str]) -> int:
    target = args[0].rstrip("/") if args else ""
    if target in state["aliases"] and state["current"]:
        target = state["current"]
    deployment = next((d for d in state["deployments"] if d["url"] == target), None)
    if deployment is None:
        return fail(f'Can\'t find the deployment "{target}" under the context "{TEAM}"')
    host = deployment["url"].removeprefix("https://")
    err(f'> Fetched deployment "{host}" in {TEAM} [432ms]', "", "  General", "")
    err(
        "    id\t\tdpl_" + host[11:20],
        "    name\tkitchentab",
        f"    target\t{deployment['target']}",
        f"    status\t\u25cf {deployment['status']}",
        f"    url\t\t{deployment['url']}",
        "    created\tFri Oct 09 2026 07:58:12 GMT-0400 [2m ago]",
        "",
    )
    if deployment["url"] == state["current"]:
        err("  Aliases", "")
        for alias in state["aliases"]:
            err(f"    \u2576 {alias}")
        err("")
    err(
        "  Builds",
        "",
        "    \u250c .        [0ms]",
        "    \u2514\u2500\u2500 \u03bb api/index (12.4MB) [iad1]",
    )
    return 0


def cmd_ls(state: dict) -> int:
    err(f"> Deployments for {TEAM}/kitchentab [580ms]", "")
    print(
        "  Age     Deployment                                                   Status      Environment     Duration     Username"
    )
    for i, d in enumerate(reversed(state["deployments"])):
        env = "Production" if d["target"] == "production" else "Preview"
        print(
            f"  {i + 1}h      {d['url']:<60} \u25cf {d['status']:<9} {env:<15} 48s          pranav"
        )
    return 0


def main() -> int:
    argv = sys.argv[1:]
    # Only `env add` reads stdin (like the real CLI); anything else leaves the caller's stdin alone.
    reads_stdin = (
        argv[:2] == ["env", "add"] and sys.stdin is not None and not sys.stdin.isatty()
    )
    stdin = sys.stdin.read() if reads_stdin else ""
    state = load()
    state["calls"] += 1
    code = run(state, argv, stdin)
    save(state)
    log(argv, len(stdin), code)
    return code


def run(state: dict, argv: list[str], stdin: str) -> int:
    for flag in FAIL:
        if flag.startswith("network@") and state["calls"] >= int(flag.split("@")[1]):
            err(
                "Error: request to https://api.vercel.com/v9/projects failed, reason: getaddrinfo ENOTFOUND api.vercel.com"
            )
            return 1
    err(VERSION)
    if not argv:
        return fail("No command")
    cmd, args = argv[0], argv[1:]
    if cmd in ("--version", "-v"):
        print(VERSION.split()[-1])
        return 0
    if not state["logged_in"] or "whoami" in FAIL:
        return fail(
            'No existing credentials found. Please run `vercel login` or pass "--token"'
        )
    if cmd == "whoami":
        print(state["user"])
        return 0
    if cmd == "link":
        if "link" in FAIL:
            return fail(
                "Could not create the project: You have reached the limit of 200 projects"
            )
        if "--project" not in args or args[args.index("--project") + 1] != "kitchentab":
            return fail("Expected --project kitchentab")
        vercel_dir = Path.cwd() / ".vercel"
        vercel_dir.mkdir(exist_ok=True)
        (vercel_dir / "project.json").write_text(
            json.dumps(
                {
                    "projectId": "prj_fake123",
                    "orgId": "team_fake456",
                    "projectName": "kitchentab",
                }
            ),
            encoding="utf-8",
        )
        state["project"] = "kitchentab"
        err(
            f"\u2705  Linked to {TEAM}/kitchentab (created .vercel and added it to .gitignore)"
        )
        return 0
    if not linked(state):
        return fail(
            "Your codebase isn't linked to a project on Vercel. Run `vercel link` to begin."
        )
    if cmd == "env":
        return cmd_env(state, args, stdin)
    if cmd == "integration":
        return cmd_integration(state, args)
    if cmd == "blob":
        return cmd_blob(state, args)
    if cmd in ("deploy", "redeploy"):
        if "deploy" in FAIL:
            err('Error: Command "pnpm run build" exited with 1')
            return 1
        if cmd == "deploy":
            target = "production" if "--prod" in args else "preview"
        else:
            source = next(
                (d for d in state["deployments"] if d["url"] == args[0].rstrip("/")),
                None,
            )
            if source is None:
                return fail(f'Can\'t find the deployment "{args[0]}"')
            target = source["target"]
        deployment = make_deployment(state, target)
        err(f"\U0001f50d  Inspect: https://vercel.com/{TEAM}/kitchentab/abc123 [2s]")
        err(f"\u2705  {target.title()}: {deployment['url']} [41s]")
        print(deployment["url"])
        return 0
    if cmd == "inspect":
        return cmd_inspect(state, args)
    if cmd in ("ls", "list"):
        return cmd_ls(state)
    if cmd == "promote":
        if "promote" in FAIL:
            return fail("Failed to promote: deployment is not eligible for promotion")
        url = args[0].rstrip("/")
        if not any(d["url"] == url for d in state["deployments"]):
            return fail(f'Can\'t find the deployment "{url}"')
        state["current"] = url
        err(f"Success! {url} is now promoted to production [3s]")
        return 0
    return fail(f"Unknown command {cmd}")


if __name__ == "__main__":
    sys.exit(main())
