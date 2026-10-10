"""Write docs/TEST_REPORT.md (TEST_PLAN §8) from what the last `make test-all` actually ran.

Inputs: the JUnit XML every suite leaves in `.cache/results/` (pytest records each test's TEST_PLAN
and FR ids as properties; Playwright and Vitest titles start with their ids), the TEST_PLAN catalog
and the waivers in docs/PROGRESS.md (via check_traceability), and the JSON the simulation, load,
extraction and launch checks leave in `.cache/`.

An id passes when at least one test carrying it ran and none of them failed. A Must id that failed
or never ran is a Must failure unless it is waived; `--check` exits 1 when there is any.

    uv run --project apps/api python scripts/test_report.py [--check] [--out docs/TEST_REPORT.md]
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_traceability import ID_RE, parse_catalog, parse_progress

RESULTS = ROOT / ".cache" / "results"
LEAD_IDS = re.compile(rf"^((?:{ID_RE})(?:[ ,/+]+(?:{ID_RE}))*)\b")


def case_ids(case: ET.Element) -> list[str]:
    ids: list[str] = []
    for prop in case.iter("property"):
        if prop.get("name") == "test_ids":
            ids += (prop.get("value") or "").split()
    if (
        not ids
    ):  # Playwright / Vitest: the title starts with its ids ("SYNC-03 two phones …")
        title = case.get("name") or ""
        title = title.split(" › ")[-1]
        lead = LEAD_IDS.match(title)
        if lead:
            ids = re.findall(ID_RE, lead.group(1))
    return ids


def outcome(case: ET.Element) -> str:
    if case.find("failure") is not None or case.find("error") is not None:
        return "fail"
    if case.find("skipped") is not None:
        return "skip"
    return "pass"


def read_results(
    paths: list[Path],
) -> tuple[dict[str, Counter[str]], Counter[str], list[dict[str, str]]]:
    by_id: dict[str, Counter[str]] = defaultdict(Counter)
    totals: Counter[str] = Counter()
    failures: list[dict[str, str]] = []
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            result = outcome(case)
            totals[result] += 1
            ids = case_ids(case)
            for i in ids:
                by_id[i][result] += 1
            if result == "fail":
                node = (
                    case.find("failure")
                    if case.find("failure") is not None
                    else case.find("error")
                )
                message = (node.get("message") if node is not None else "") or ""
                failures.append(
                    {
                        "ids": " ".join(ids) or "(no id)",
                        "test": f"{case.get('classname', '')}::{case.get('name', '')}",
                        "message": message.splitlines()[0][:160] if message else "",
                        "suite": path.stem,
                    }
                )
    return by_id, totals, failures


def area(test_id: str) -> str:
    return "EDGE" if test_id.startswith("EDGE-") else test_id.split("-")[0]


def load_json(pattern: str) -> list[tuple[str, Any]]:
    out = []
    for name in sorted(glob.glob(str(ROOT / pattern))):
        try:
            out.append(
                (Path(name).name, json.loads(Path(name).read_text(encoding="utf-8")))
            )
        except (OSError, ValueError):
            continue
    return out


def git_version() -> str:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"
    return sha.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")


def build(results: Path) -> tuple[str, list[str]]:
    paths = sorted(results.glob("*.xml"))
    by_id, totals, failures = read_results(paths)
    cat = parse_catalog(ROOT)
    _, waivers = parse_progress(ROOT)
    ids = sorted(cat.tests) + sorted(cat.edges)
    status: dict[str, str] = {}
    for i in ids:
        got = by_id.get(i, Counter())
        if i in waivers:
            status[i] = "waived"
        elif got["fail"]:
            status[i] = "fail"
        elif got["pass"]:
            status[i] = "pass"
        else:
            status[i] = "not run"
    must = [i for i in cat.tests if cat.tests[i] == "M"] + list(cat.edges)
    must_failures = sorted(i for i in must if status[i] in ("fail", "not run"))

    lines = [
        f"# KitchenTab test report — {git_version()} — {datetime.now(UTC).date().isoformat()}",
        "",
        (
            "Generated by `scripts/test_report.py` from the JUnit XML of the last `make test-all` "
            f"({len(paths)} result files in `.cache/results/`). Do not edit by hand."
        ),
        "",
        "## Summary",
        (
            f"Test cases — Pass: {totals['pass']} · Fail: {totals['fail']} · Skipped: {totals['skip']} · "
            "Flaky (quarantined): 0"
        ),
        "",
        (
            f"TEST_PLAN ids — {sum(s == 'pass' for s in status.values())} pass · "
            f"{sum(s == 'fail' for s in status.values())} fail · {sum(s == 'not run' for s in status.values())} "
            f"not run · {sum(s == 'waived' for s in status.values())} waived · "
            f"**Must failures: {len(must_failures)}**"
        ),
        "",
        "## By area",
        "| Area | Total | Pass | Fail | Not run | Waived | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    areas: dict[str, list[str]] = defaultdict(list)
    for i in ids:
        areas[area(i)].append(i)
    for name in sorted(areas):
        group = areas[name]
        c = Counter(status[i] for i in group)
        bad = [i for i in group if status[i] in ("fail", "not run")]
        note = (
            ("open: " + ", ".join(bad[:8]) + (" …" if len(bad) > 8 else ""))
            if bad
            else ""
        )
        lines.append(
            f"| {name} | {len(group)} | {c['pass']} | {c['fail']} | {c['not run']} | {c['waived']} | {note} |"
        )
    lines += ["", "## Failures and waivers", "| ID | What failed | Cause | Fix / waiver reason | Owner |",
              "| --- | --- | --- | --- | --- |"]  # fmt: skip
    for f in failures[:80]:
        lines.append(
            f"| {f['ids']} | `{f['test'][:90]}` | {f['message']} | open | build |"
        )
    for i in sorted(i for i in ids if status[i] == "not run" and i in must_failures):
        lines.append(
            f"| {i} | not run in this test-all | no test result | open | build |"
        )
    for i in sorted(waivers):
        lines.append(
            f"| {i} | waived | see docs/PROGRESS.md § Waivers | waiver | Pranav |"
        )

    lines += ["", "## Simulation, load and launch checks"]
    for name, data in load_json(".cache/simulate/summary-*.json"):
        lines.append(
            f"- `{name}`: {data.get('result')} — {data.get('actions')} actions, {data.get('checkpoints')} "
            f"checkpoints, {data.get('restarts')} restarts, {len(data.get('violations', []))} violations"
        )
    for name, data in load_json(".cache/load/report.json"):
        for r in data.get("results", []):
            keep = {k: v for k, v in r.items() if k not in ("errors",)}
            lines.append(f"- `{name}` {r.get('id')}: {json.dumps(keep)}")
    for name, data in load_json(".cache/launch-check/*.json"):
        lines.append(f"- `{name}`: {json.dumps(data)[:300]}")
    for name, data in load_json(".cache/extraction/*.json"):
        lines.append(f"- `{name}`: {json.dumps(data)[:300]}")

    lines += [
        "",
        "## Coverage",
        (
            "money.py / money.ts / policy.py branches · backend lines · frontend lib lines: see `make test` "
            "output (`--cov` gates) and `apps/web/coverage/`."
        ),
        "",
        "## Device QA",
        "| Device | OS | Browser | Install | Push | Sync | Notes |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        "| Real phones | — | — | pending | pending | pending | Pranav runs docs/DEVICE_QA.md against production |",
        "",
        "## Known issues shipped",
    ]
    known = [i for i in sorted(waivers)]
    lines += [f"- {i} (waived; see docs/PROGRESS.md)" for i in known] or ["- None."]
    return "\n".join(lines) + "\n", must_failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "TEST_REPORT.md")
    parser.add_argument(
        "--check", action="store_true", help="exit 1 on any Must failure"
    )
    args = parser.parse_args()
    text, must_failures = build(args.results)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.out}: {len(must_failures)} Must failures")
    if must_failures:
        print(
            "  "
            + ", ".join(must_failures[:40])
            + (" …" if len(must_failures) > 40 else "")
        )
    return 1 if args.check and must_failures else 0


if __name__ == "__main__":
    sys.exit(main())
