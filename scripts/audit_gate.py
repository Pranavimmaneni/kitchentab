#!/usr/bin/env python3
"""SEC-09 gate: dependency audits with no unwaived high or critical advisory.

Reads the JSON reports of `pip-audit -f json` (the locked API environment) and `pnpm audit --json`
(the web app) and fails when

* pnpm reports a `high` or `critical` advisory, or
* pip-audit reports any vulnerability (PyPI advisories carry no severity, so every one counts),

unless its id (GHSA-..., PYSEC-..., CVE-...) is listed under `## Waivers` in docs/PROGRESS.md,
where each waiver states why it is real but unfixable. Nothing is ignored silently.

    python scripts/audit_gate.py --pip PIP.json --pnpm PNPM.json [--waivers docs/PROGRESS.md]

Exit codes: 0 clean, 1 unwaived advisories, 2 a report is missing or unreadable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ID_RE = re.compile(
    r"\b(GHSA(?:-[0-9a-z]{4}){3}|PYSEC-\d{4}-\d+|CVE-\d{4}-\d+)\b", re.IGNORECASE
)
BLOCKING = {"high", "critical"}


@dataclass(frozen=True)
class Advisory:
    source: str
    package: str
    version: str
    ids: tuple[str, ...]
    severity: str
    title: str

    def __str__(self) -> str:
        return (
            f"{self.source}: {self.package}@{self.version} {self.severity} "
            f"{', '.join(self.ids)} {self.title}".rstrip()
        )


def waived_ids(progress: Path) -> set[str]:
    """IDs mentioned in the `## Waivers` section (up to the next `## ` heading)."""
    if not progress.is_file():
        return set()
    text = progress.read_text(encoding="utf-8")
    m = re.search(r"^## Waivers\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return {i.upper() for i in ID_RE.findall(m.group(1))} if m else set()


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"audit_gate: can't read {path}: {exc}") from exc


def pnpm_advisories(report: dict[str, Any]) -> list[Advisory]:
    if "error" in report:
        raise SystemExit(f"audit_gate: pnpm audit failed: {report['error']}")
    out = []
    for adv in (report.get("advisories") or {}).values():
        ids = tuple(
            i
            for i in (adv.get("github_advisory_id"), *(adv.get("cves") or []))
            if isinstance(i, str) and i
        )
        versions = sorted({f.get("version", "?") for f in adv.get("findings") or []})
        out.append(
            Advisory(
                "pnpm",
                str(adv.get("module_name")),
                ",".join(versions) or "?",
                ids or (str(adv.get("id")),),
                str(adv.get("severity", "unknown")).lower(),
                str(adv.get("title", "")),
            )
        )
    return out


def pip_advisories(report: dict[str, Any] | list[Any]) -> list[Advisory]:
    deps = report.get("dependencies", []) if isinstance(report, dict) else report
    out = []
    for dep in deps:
        for vuln in dep.get("vulns") or []:
            ids = (vuln.get("id"), *(vuln.get("aliases") or []))
            out.append(
                Advisory(
                    "pip",
                    str(dep.get("name")),
                    str(dep.get("version")),
                    tuple(i for i in ids if i),
                    "unrated",
                    (vuln.get("description") or "")[:80],
                )
            )
    return out


def blocking(
    advisories: list[Advisory], waived: set[str]
) -> tuple[list[Advisory], list[Advisory]]:
    """(blocking, waived) - pnpm below high never blocks; every pip-audit finding does."""
    stop, ok = [], []
    for adv in advisories:
        if adv.source == "pnpm" and adv.severity not in BLOCKING:
            continue
        (ok if {i.upper() for i in adv.ids} & waived else stop).append(adv)
    return stop, ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--pip", type=Path, required=True, help="pip-audit -f json output"
    )
    parser.add_argument(
        "--pnpm", type=Path, required=True, help="pnpm audit --json output"
    )
    parser.add_argument("--waivers", type=Path, default=ROOT / "docs" / "PROGRESS.md")
    args = parser.parse_args(argv)
    for path in (args.pip, args.pnpm):
        if not path.is_file():
            print(f"audit_gate: missing report {path}", file=sys.stderr)
            return 2
    try:
        advisories = pip_advisories(_load(args.pip)) + pnpm_advisories(_load(args.pnpm))
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    stop, waived = blocking(advisories, waived_ids(args.waivers))
    for adv in waived:
        print(f"WAIVED   {adv}")
    for adv in stop:
        print(f"BLOCKING {adv}")
    print(
        f"audit_gate: {len(advisories)} advisories, {len(waived)} waived, "
        f"{len(stop)} blocking: {'FAIL' if stop else 'ok'}"
    )
    return 1 if stop else 0


if __name__ == "__main__":
    sys.exit(main())
