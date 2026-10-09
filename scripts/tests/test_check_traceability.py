"""Self-test of scripts/check_traceability.py on a tiny synthetic repo."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "check_traceability", ROOT / "scripts" / "check_traceability.py"
)
assert spec and spec.loader
ct = importlib.util.module_from_spec(spec)
sys.modules["check_traceability"] = ct
spec.loader.exec_module(ct)


def make_repo(
    tmp: Path, *, done: bool, tests_py: str = "", tests_ts: str = "", waivers: str = ""
) -> Path:
    (tmp / "docs" / "traceability").mkdir(parents=True)
    (tmp / "docs" / "PRD.md").write_text(
        "| FR-01 | Must | Sign in | ok |\n| FR-02 | Should | House | ok |\n"
        "| SYNC-01 | Two members tick | Both end up on it |\n",
        encoding="utf-8",
    )
    (tmp / "docs" / "TEST_PLAN.md").write_text(
        "| AUTH-01 | M | Email code | Signed in |\n| E2E-01 | New payer journey |\n",
        encoding="utf-8",
    )
    (tmp / "docs" / "traceability" / "edge_cases.yaml").write_text(
        "prd_15:\n  EDGE-15-01: {milestone: 1, situation: x}\n", encoding="utf-8"
    )
    flag = " [done]" if done else ""
    (tmp / "docs" / "PROGRESS.md").write_text(
        f"## Milestone 1: Foundation{flag}\nIDs: FR-01..FR-02, AUTH-01, SYNC-01, E2E-01\n\n## Waivers\n{waivers}\n",
        encoding="utf-8",
    )
    (tmp / "apps" / "api" / "tests").mkdir(parents=True)
    (tmp / "apps" / "api" / "tests" / "test_x.py").write_text(
        tests_py, encoding="utf-8"
    )
    (tmp / "apps" / "web" / "tests").mkdir(parents=True)
    (tmp / "apps" / "web" / "tests" / "x.spec.ts").write_text(
        tests_ts, encoding="utf-8"
    )
    return tmp


FULL_PY = '@tid("AUTH-01", "SYNC-01")\n@fr("FR-01")\ndef test_a(): ...\n@tid("EDGE-15-01")\ndef test_b(): ...\n'
FULL_TS = "test('E2E-01 new payer [FR-02]', async () => {});\n"


def test_not_done_milestone_is_reported_not_enforced(tmp_path: Path) -> None:
    code, report = ct.run(make_repo(tmp_path, done=False), enforce_all=False)
    assert code == 0
    assert report["covered"] == 0


def test_done_milestone_requires_every_id(tmp_path: Path) -> None:
    code, report = ct.run(
        make_repo(tmp_path, done=True, tests_py=FULL_PY), enforce_all=False
    )
    assert code == 1
    assert report["missing_required"] == ["E2E-01", "FR-02"]


def test_python_and_typescript_markers_count(tmp_path: Path) -> None:
    code, report = ct.run(
        make_repo(tmp_path, done=True, tests_py=FULL_PY, tests_ts=FULL_TS),
        enforce_all=True,
    )
    assert code == 0, report
    assert report["coverage_pct"] == 100.0


def test_waivers_satisfy_but_are_reported(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        done=True,
        tests_py=FULL_PY,
        waivers="- E2E-01 needs real phones\n- FR-02 later\n",
    )
    code, report = ct.run(repo, enforce_all=False)
    assert code == 0
    assert report["waived"] == ["E2E-01", "FR-02"]


def test_unknown_ids_on_tests_fail(tmp_path: Path) -> None:
    code, report = ct.run(
        make_repo(tmp_path, done=False, tests_py='@tid("AUTH-99")\ndef t(): ...\n'),
        False,
    )
    assert code == 1
    assert report["unknown_ids_in_tests"] == ["AUTH-99"]


def test_range_expansion() -> None:
    assert ct.expand("FR-01..FR-03") == ["FR-01", "FR-02", "FR-03"]
    assert ct.expand("AUTH-14..16") == ["AUTH-14", "AUTH-15", "AUTH-16"]
    assert ct.expand("Screens") == []


def test_each_titles_count_and_skipped_tests_do_not(tmp_path: Path) -> None:
    ts = (
        "it.each(rows.map((r) => [r.name]))('E2E-01 fixture %s [FR-02]', () => {});\n"
        "test.skip('AUTH-01 not yet', () => {});\n"
    )
    code, report = ct.run(make_repo(tmp_path, done=False, tests_ts=ts), enforce_all=False)
    found = ct.scan_tests(tmp_path)
    assert "E2E-01" in found and "FR-02" in found
    assert "AUTH-01" not in found
    assert code == 0
