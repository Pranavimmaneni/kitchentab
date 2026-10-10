#!/usr/bin/env python3
"""SEC-07, client half: no server-only configuration in the built web bundle.

Reads the variable names (and values) from `.env.example` and `.env`, then searches every file
under the built bundle (default `apps/web/.next/static`) for

* the name of any server-only variable (`SESSION_SECRET`, `DATABASE_URL`, ...): client code that
  mentions one is reading server config, and a bundler may inline it;
* the value of any server-only variable that is long enough to be meaningful (8+ characters):
  that is the leak itself.

Public variables are the ones the browser is meant to see: `NEXT_PUBLIC_*` and the PUBLIC set
below (origins, the VAPID *public* key, the Turnstile *site* key, the Google client id, which is
public in every OAuth redirect). Findings name the variable and the file, never the value.

    python scripts/scan_bundle_secrets.py [--bundle DIR] [--env FILE ...] [--allow-missing]

Exit codes: 0 clean, 1 findings, 2 no bundle (unless --allow-missing).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = ROOT / "apps" / "web" / ".next" / "static"
DEFAULT_ENVS = (ROOT / ".env.example", ROOT / ".env")
PUBLIC = frozenset(
    {
        "WEB_ORIGIN",
        "PUBLIC_LINK_BASE",
        "VAPID_PUBLIC_KEY",
        "TURNSTILE_SITE_KEY",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_ISSUER",
        "GOOGLE_REDIRECT_URI",
    }
)
MIN_VALUE_LEN = 8
TEXT_SUFFIXES = {
    ".js",
    ".mjs",
    ".cjs",
    ".css",
    ".json",
    ".map",
    ".html",
    ".txt",
    ".svg",
    ".webmanifest",
}
LINE = re.compile(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$")


@dataclass(frozen=True)
class Finding:
    path: Path
    name: str
    kind: str  # "name" | "value"

    def __str__(self) -> str:
        what = (
            f"the name {self.name}"
            if self.kind == "name"
            else f"the value of {self.name}"
        )
        return f"{self.path}: contains {what}"


def parse_env(text: str) -> dict[str, str]:
    """KEY=VALUE lines; quotes stripped; ` # comment` after an unquoted value dropped."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        m = LINE.match(raw)
        if not m:
            continue
        name, value = m.group(1), m.group(2).strip()
        if value[:1] in ("'", '"'):
            end = value.find(value[0], 1)
            value = value[1:end] if end > 0 else value[1:]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        out[name] = value
    return out


def is_public(name: str) -> bool:
    return name.startswith("NEXT_PUBLIC_") or name in PUBLIC


def load_env(paths: list[Path]) -> tuple[set[str], dict[str, set[str]]]:
    """(server-only names, server-only name -> values worth searching for)."""
    names: set[str] = set()
    values: dict[str, set[str]] = {}
    public_values: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        for name, value in parse_env(
            path.read_text(encoding="utf-8", errors="replace")
        ).items():
            if is_public(name):
                public_values.add(value)
                continue
            names.add(name)
            if len(value) >= MIN_VALUE_LEN:
                values.setdefault(name, set()).add(value)
    for name in list(values):
        values[name] -= (
            public_values  # e.g. an origin that is also a public variable's value
        )
    return names, {k: v for k, v in values.items() if v}


def bundle_files(bundle: Path) -> list[Path]:
    return sorted(
        p
        for p in bundle.rglob("*")
        if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES
    )


def scan(bundle: Path, names: set[str], values: dict[str, set[str]]) -> list[Finding]:
    name_re = (
        re.compile(
            r"(?<![A-Za-z0-9_])("
            + "|".join(re.escape(n) for n in sorted(names))
            + r")(?![A-Za-z0-9_])"
        )
        if names
        else None
    )
    findings: list[Finding] = []
    for path in bundle_files(bundle):
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(bundle)
        if name_re is not None:
            for name in sorted({m.group(1) for m in name_re.finditer(text)}):
                findings.append(Finding(rel, name, "name"))
        for name, secrets in sorted(values.items()):
            if any(secret in text for secret in secrets):
                findings.append(Finding(rel, name, "value"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument(
        "--env",
        type=Path,
        action="append",
        help="env file(s); default .env.example and .env",
    )
    parser.add_argument(
        "--allow-missing", action="store_true", help="exit 0 when there is no build"
    )
    args = parser.parse_args(argv)
    if not args.bundle.is_dir():
        print(
            f"scan_bundle_secrets: no build at {args.bundle} (run `next build` first)"
        )
        return 0 if args.allow_missing else 2
    names, values = load_env(args.env or list(DEFAULT_ENVS))
    if not names:
        print("scan_bundle_secrets: no env files with variables found", file=sys.stderr)
        return 2
    files = bundle_files(args.bundle)
    findings = scan(args.bundle, names, values)
    for f in findings:
        print(f"SECRET IN BUNDLE  {f}")
    print(
        f"scan_bundle_secrets: {len(files)} files, {len(names)} server-only names, "
        f"{sum(len(v) for v in values.values())} values: {'FAIL' if findings else 'clean'}"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
