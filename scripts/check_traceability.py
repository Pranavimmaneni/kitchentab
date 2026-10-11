#!/usr/bin/env python3
"""Traceability check (TEST_PLAN intro, ADR-0004).

Every FR in docs/PRD.md, every test ID in docs/TEST_PLAN.md (plus the §21.7 SYNC rows) and every
edge-case ID in docs/traceability/edge_cases.yaml must be carried by at least one test:

* pytest: `@tid("SYNC-03")`, `@fr("FR-20")` (or `pytest.mark.id` / `pytest.mark.fr`)
* Vitest / Playwright: the test title starts with the ID(s) (`test("SYNC-03 last two untick", ...)`)
  and FRs appear as `[FR-20]` or `@FR-20` in the title.

Enforcement is milestone-aware: IDs listed under a `## Milestone N: ... [done]` heading in
docs/PROGRESS.md (and edge cases whose `milestone` is done) must be covered; everything else is
reported. `--all` enforces every ID (release gate). IDs listed under "## Waivers" in PROGRESS.md
count as satisfied but are reported. Unknown IDs on tests are always an error (typos).

    python scripts/check_traceability.py [--all] [--json] [--root PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Feature specs number their own rows with a G (kitchen gestures: GEST-01, SYNC-G1, FR-G01).
ID_RE = r"(?:EDGE-(?:15|19)-\d{2}|[A-Z][A-Z0-9]*-(?:\d{2}|G\d(?!\d)))"
FR_RE = r"FR-G?\d{2}"
SKIP_FILES = {
    "test_check_traceability.py"
}  # its synthetic fixtures contain bogus IDs on purpose
SKIP_DIRS = {
    ".venv",
    "node_modules",
    ".next",
    "test-results",
    "playwright-report",
    "__pycache__",
    ".git",
}


@dataclass
class Catalog:
    fr: dict[str, str] = field(default_factory=dict)  # id -> priority (Must/Should)
    tests: dict[str, str] = field(default_factory=dict)  # id -> priority (M/S)
    edges: dict[str, int] = field(default_factory=dict)  # id -> milestone

    def all_ids(self) -> set[str]:
        return set(self.fr) | set(self.tests) | set(self.edges)


@dataclass
class Milestone:
    number: int
    title: str
    done: bool
    ids: set[str]


def parse_catalog(root: Path) -> Catalog:
    cat = Catalog()
    prd = (root / "docs" / "PRD.md").read_text(encoding="utf-8")
    for m in re.finditer(rf"^\| ({FR_RE}) \| (Must|Should) \|", prd, re.MULTILINE):
        cat.fr[m.group(1)] = m.group(2)
    # §21.7 race matrix rows are SYNC-01..24 (Must)
    for m in re.finditer(r"^\| (SYNC-\d{2}) \| [^|]+\| [^|]+\|\s*$", prd, re.MULTILINE):
        cat.tests[m.group(1)] = "M"
    plan = (root / "docs" / "TEST_PLAN.md").read_text(encoding="utf-8")
    for m in re.finditer(rf"^\| ({ID_RE}) \| (M|S) \|", plan, re.MULTILINE):
        cat.tests[m.group(1)] = m.group(2)
    for m in re.finditer(r"^\| (E2E-\d{2}) \| ", plan, re.MULTILINE):
        cat.tests[m.group(1)] = "M"
    import yaml

    edges = yaml.safe_load(
        (root / "docs" / "traceability" / "edge_cases.yaml").read_text(encoding="utf-8")
    )
    for section in edges.values():
        for edge_id, spec in section.items():
            cat.edges[edge_id] = int(spec["milestone"])
    return cat


def expand(token: str) -> list[str]:
    """`FR-01..FR-09`, `AUTH-01..16`, `SYNC-01..34`, `FR-G01..G11`, `SYNC-G1..G6` or a single ID."""
    token = token.strip().rstrip(",")
    m = re.fullmatch(r"([A-Z][A-Z0-9]*)-(G?)(\d{1,2})\.\.(?:\1-)?\2(\d{1,2})", token)
    if m:
        prefix, g, lo, hi = m.group(1), m.group(2), m.group(3), int(m.group(4))
        out = [f"{prefix}-{g}{n:0{len(lo)}d}" for n in range(int(lo), hi + 1)]
        return [i for i in out if re.fullmatch(ID_RE, i) or re.fullmatch(FR_RE, i)]
    if re.fullmatch(ID_RE, token) or re.fullmatch(FR_RE, token):
        return [token]
    return []


def parse_progress(root: Path) -> tuple[list[Milestone], set[str]]:
    text = (root / "docs" / "PROGRESS.md").read_text(encoding="utf-8")
    milestones: list[Milestone] = []
    for block in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        head, _, body = block.partition("\n")
        m = re.match(r"Milestone (\d+): (.*?)\s*(\[done\])?\s*$", head)
        if not m:
            continue
        ids: set[str] = set()
        ids_line = re.search(r"^IDs: (.*)$", body, re.MULTILINE)
        if ids_line:
            for part in re.split(r"[,·]", ids_line.group(1)):
                for token in part.split():
                    ids.update(expand(token))
        milestones.append(Milestone(int(m.group(1)), m.group(2), bool(m.group(3)), ids))
    waivers: set[str] = set()
    w = re.search(r"^## Waivers\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if w:
        waivers = set(
            re.findall(rf"^\s*[-*] ({ID_RE}|{FR_RE})\b", w.group(1), re.MULTILINE)
        )
    return milestones, waivers


def _walk(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    out = []
    for path in root.rglob("*"):
        if (
            path.suffix in suffixes
            and path.name not in SKIP_FILES
            and not (SKIP_DIRS & set(path.parts))
            and path.is_file()
        ):
            out.append(path)
    return out


PY_MARK = re.compile(r"(?:\btid|\bfr|pytest\.mark\.id|pytest\.mark\.fr)\(([^)]*)\)")
# A test title is the first string argument of a call (`test("SYNC-03 ...")`,
# `it.each(rows)("MONEY-02 ...")`) that starts with a TEST_PLAN ID. Skipped or expected-to-fail
# tests (.skip, .fixme, .fail, .todo) never count as coverage.
TS_TITLE = re.compile(r"(\.?[A-Za-z_$][\w$]*)?\(\s*(['\"`])(.+?)\2", re.DOTALL)
SKIPPED = {".skip", ".fixme", ".fail", ".todo"}


def scan_tests(root: Path) -> dict[str, set[str]]:
    """Returns ID -> set of 'file:line' that carry it."""
    found: dict[str, set[str]] = defaultdict(set)
    for path in _walk(root, (".py",)):
        rel = path.relative_to(root).as_posix()
        if "/tests/" not in f"/{rel}" and not path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in PY_MARK.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            for ident in re.findall(rf"['\"]({ID_RE}|{FR_RE})['\"]", m.group(1)):
                found[ident].add(f"{rel}:{line}")
    for path in _walk(root, (".ts", ".tsx")):
        rel = path.relative_to(root).as_posix()
        if not re.search(r"(^|/)tests?/|\.(test|spec)\.tsx?$", rel):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in TS_TITLE.finditer(text):
            title = m.group(3)
            lead = re.match(rf"^((?:{ID_RE})(?:[ ,/+]+(?:{ID_RE}))*)\b", title)
            if not lead or (m.group(1) or "") in SKIPPED:
                continue
            line = text.count("\n", 0, m.start()) + 1
            ids = re.findall(ID_RE, lead.group(1))
            ids += re.findall(rf"[\[@]({FR_RE})\]?", title)
            for ident in ids:
                found[ident].add(f"{rel}:{line}")
    return found


def run(root: Path, enforce_all: bool) -> tuple[int, dict[str, object]]:
    cat = parse_catalog(root)
    milestones, waivers = parse_progress(root)
    found = scan_tests(root)
    known = cat.all_ids()
    unknown = sorted(i for i in found if i not in known)

    done_numbers = {m.number for m in milestones if m.done}
    required: set[str] = set()
    for m in milestones:
        if m.done or enforce_all:
            required |= m.ids & known
    required |= {e for e, n in cat.edges.items() if n in done_numbers or enforce_all}
    if enforce_all:
        required |= known

    covered = {i for i in known if i in found}
    missing = sorted(required - covered - waivers)
    unassigned = (
        sorted(known - set().union(*(m.ids for m in milestones)) - set(cat.edges))
        if milestones
        else []
    )
    report: dict[str, object] = {
        "total_ids": len(known),
        "covered": len(covered),
        "coverage_pct": round(
            100 * len(covered | (waivers & known)) / max(len(known), 1), 1
        ),
        "waived": sorted(waivers & known),
        "required_now": len(required),
        "missing_required": missing,
        "unknown_ids_in_tests": unknown,
        "ids_not_assigned_to_a_milestone": unassigned,
        "milestones": [
            {
                "milestone": m.number,
                "title": m.title,
                "done": m.done,
                "ids": len(m.ids & known),
                "covered": len((m.ids & known) & covered),
            }
            for m in milestones
        ],
    }
    failed = bool(missing or unknown or (enforce_all and unassigned))
    return (1 if failed else 0), report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--all", action="store_true", help="enforce every ID (release gate)"
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args(argv)
    code, report = run(args.root, args.all)
    if args.json:
        print(json.dumps(report, indent=2))
        return code
    print(
        f"traceability: {report['covered']}/{report['total_ids']} IDs carried by tests "
        f"({report['coverage_pct']}% incl. waivers); enforcing {report['required_now']} now"
    )
    for m in report["milestones"]:  # type: ignore[union-attr]
        flag = "done" if m["done"] else "    "
        print(
            f"  M{m['milestone']:<2} [{flag}] {m['covered']:>3}/{m['ids']:<3} {m['title']}"
        )
    for key in (
        "missing_required",
        "unknown_ids_in_tests",
        "ids_not_assigned_to_a_milestone",
    ):
        items = report[key]
        if items:
            print(f"{key} ({len(items)}): {', '.join(items)}")  # type: ignore[arg-type]
    print("OK" if code == 0 else "FAIL")
    return code


if __name__ == "__main__":
    sys.exit(main())
