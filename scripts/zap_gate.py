#!/usr/bin/env python3
"""SEC-08 gate: the ZAP baseline report has no high or medium alert.

`make zap-baseline` runs `zap-baseline.py` (ghcr.io/zaproxy/zaproxy:stable) against ZAP_TARGET
and writes `.cache/zap/zap-report.json`. This script fails when any alert with risk High (3) or
Medium (2) remains after the rules file (`infra/zap/baseline-rules.tsv`) is applied. The rules
file is ZAP's own `-c` format (`<plugin id>\\t<IGNORE|INFO|WARN|FAIL>\\t(<reason>)`); only IGNORE
lines with a reason count here, so every suppressed alert is a documented false positive.

    python scripts/zap_gate.py REPORT.json [--rules infra/zap/baseline-rules.tsv]

Exit codes: 0 clean, 1 high/medium alerts, 2 unreadable report or a rule without a reason.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES = ROOT / "infra" / "zap" / "baseline-rules.tsv"
RISK = {3: "High", 2: "Medium", 1: "Low", 0: "Informational"}


@dataclass(frozen=True)
class Alert:
    plugin: str
    risk: int
    name: str
    site: str
    count: int

    def __str__(self) -> str:
        return f"[{RISK.get(self.risk, self.risk)}] {self.plugin} {self.name} ({self.count}x on {self.site})"


class RulesError(ValueError):
    pass


def load_rules(path: Path) -> dict[str, str]:
    """plugin id -> reason, for IGNORE lines. A missing file means no suppressions."""
    if not path.is_file():
        return {}
    ignored: dict[str, str] = {}
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in raw.split("\t") if p.strip()]
        if len(parts) < 2 or not parts[0].isdigit():
            raise RulesError(f"{path}:{n}: expected '<id>\\t<ACTION>\\t(<reason>)'")
        if parts[1].upper() == "IGNORE":
            reason = parts[2].strip("() ") if len(parts) > 2 else ""
            if len(reason) < 10:
                raise RulesError(f"{path}:{n}: IGNORE {parts[0]} needs a reason")
            ignored[parts[0]] = reason
    return ignored


def alerts(report: dict[str, Any]) -> list[Alert]:
    out = []
    for site in report.get("site") or []:
        for a in site.get("alerts") or []:
            out.append(
                Alert(
                    str(a.get("pluginid")),
                    int(a.get("riskcode", 0)),
                    str(a.get("name") or a.get("alert")),
                    str(site.get("@name", "?")),
                    int(a.get("count") or len(a.get("instances") or []) or 1),
                )
            )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("report", type=Path)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        ignored = load_rules(args.rules)
    except (OSError, ValueError) as exc:
        print(f"zap_gate: {exc}", file=sys.stderr)
        return 2
    found = alerts(report)
    blocking = [a for a in found if a.risk >= 2 and a.plugin not in ignored]
    for a in found:
        if a.plugin in ignored and a.risk >= 2:
            print(f"IGNORED  {a}: {ignored[a.plugin]}")
    for a in blocking:
        print(f"BLOCKING {a}")
    print(
        f"zap_gate: {len(found)} alerts, {len(blocking)} high/medium: {'FAIL' if blocking else 'ok'}"
    )
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
