"""Fake `uv` and `make` for the deploy-script tests: record the call, succeed or fail on request.

Usage (from a shim): fake_tool.py <tool-name> [args...]
Appends {"tool", "exe", "argv", "cwd", "exit", "db"} to $FAKE_VERCEL_STATE/calls.json, where "db"
describes DATABASE_DIRECT_URL without its password. $FAKE_UV_FAIL=1 makes `uv` fail like a broken
Alembic migration; $FAKE_MAKE_FAIL=1 makes `make` fail like a failing test.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

STATE_DIR = Path(os.environ["FAKE_VERCEL_STATE"])
CALLS = STATE_DIR / "calls.json"


def describe_db() -> dict[str, object] | None:
    url = os.environ.get("DATABASE_DIRECT_URL")
    if not url:
        return None
    parts = urlsplit(url)
    return {
        "scheme": parts.scheme,
        "host": parts.hostname,
        "has_password": bool(parts.password),
        "query": dict(parse_qsl(parts.query)),
    }


def main() -> int:
    tool, argv = sys.argv[1], sys.argv[2:]
    code = 0
    if tool == "uv" and os.environ.get("FAKE_UV_FAIL") == "1":
        print(
            "INFO  [alembic.runtime.migration] Running upgrade 0005 -> 0006, add widgets",
            file=sys.stderr,
        )
        print(
            'sqlalchemy.exc.ProgrammingError: relation "widgets" already exists',
            file=sys.stderr,
        )
        code = 1
    elif tool == "make" and os.environ.get("FAKE_MAKE_FAIL") == "1":
        print(
            "FAILED apps/api/tests/test_money.py::test_split - assert 101 == 100",
            file=sys.stderr,
        )
        print("make: *** [Makefile:70: test-api] Error 1", file=sys.stderr)
        code = 2
    elif tool == "uv":
        print(
            "INFO  [alembic.runtime.migration] Running upgrade  -> 0005, ok",
            file=sys.stderr,
        )
    calls = json.loads(CALLS.read_text(encoding="utf-8")) if CALLS.exists() else []
    calls.append(
        {
            "tool": tool,
            "exe": str(Path(__file__).resolve()),
            "argv": argv,
            "cwd": os.getcwd(),
            "exit": code,
            "db": describe_db(),
        }
    )
    CALLS.write_text(json.dumps(calls, indent=2), encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
