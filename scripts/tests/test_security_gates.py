"""Self-tests of scripts/audit_gate.py (SEC-09) and scripts/zap_gate.py (SEC-08) on fake reports."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ag = _load("audit_gate")
zg = _load("zap_gate")

PROGRESS = """# progress

## Assumptions

1. GHSA-aaaa-bbbb-cccc is mentioned here, which is not a waiver.

## Waivers

- GHSA-vfj7-8cjw-p6xm (braces): no patched release exists; dev-only lint tooling.
- PYSEC-2024-99: example.

## Next
"""


def pnpm_report(*advisories: tuple[str, str, str]) -> dict[str, object]:
    return {
        "advisories": {
            str(n): {
                "id": n,
                "module_name": module,
                "severity": severity,
                "github_advisory_id": ghsa,
                "cves": [],
                "title": f"{module} issue",
                "findings": [{"version": "1.0.0", "paths": ["apps__web>x"]}],
            }
            for n, (module, severity, ghsa) in enumerate(advisories, 1)
        },
        "metadata": {},
    }


def pip_report(*vulns: tuple[str, str, list[str]]) -> dict[str, object]:
    return {
        "dependencies": [
            {"name": "fastapi", "version": "1.0", "vulns": []},
            *(
                {
                    "name": name,
                    "version": "2.0",
                    "vulns": [{"id": vid, "aliases": aliases, "description": "x"}],
                }
                for name, vid, aliases in vulns
            ),
        ]
    }


def write(tmp: Path, name: str, data: object) -> Path:
    path = tmp / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def run_audit(tmp: Path, pip: object, pnpm: object) -> int:
    progress = tmp / "PROGRESS.md"
    progress.write_text(PROGRESS, encoding="utf-8")
    code: int = ag.main(
        [
            "--pip",
            str(write(tmp, "pip.json", pip)),
            "--pnpm",
            str(write(tmp, "pnpm.json", pnpm)),
            "--waivers",
            str(progress),
        ]
    )
    return code


@pytest.mark.id("SEC-09")
def test_waivers_come_only_from_the_waivers_section(tmp_path: Path) -> None:
    progress = tmp_path / "PROGRESS.md"
    progress.write_text(PROGRESS, encoding="utf-8")
    assert ag.waived_ids(progress) == {"GHSA-VFJ7-8CJW-P6XM", "PYSEC-2024-99"}


@pytest.mark.id("SEC-09")
def test_clean_and_moderate_pass(tmp_path: Path) -> None:
    assert (
        run_audit(
            tmp_path,
            pip_report(),
            pnpm_report(("postcss", "moderate", "GHSA-qx2v-qp2m-jg93")),
        )
        == 0
    )


@pytest.mark.id("SEC-09")
def test_high_or_critical_pnpm_advisory_fails_unless_waived(tmp_path: Path) -> None:
    assert (
        run_audit(
            tmp_path,
            pip_report(),
            pnpm_report(("postcss", "high", "GHSA-6g55-p6wh-862q")),
        )
        == 1
    )
    assert (
        run_audit(
            tmp_path,
            pip_report(),
            pnpm_report(("x", "critical", "GHSA-aaaa-bbbb-cccc")),
        )
        == 1
    )
    assert (
        run_audit(
            tmp_path,
            pip_report(),
            pnpm_report(("braces", "high", "GHSA-vfj7-8cjw-p6xm")),
        )
        == 0
    )


@pytest.mark.id("SEC-09")
def test_any_pip_audit_finding_fails_unless_an_alias_is_waived(tmp_path: Path) -> None:
    assert (
        run_audit(
            tmp_path,
            pip_report(("jinja2", "GHSA-1111-2222-3333", ["CVE-2024-1"])),
            pnpm_report(),
        )
        == 1
    )
    assert (
        run_audit(
            tmp_path,
            pip_report(("jinja2", "GHSA-1111-2222-3333", ["PYSEC-2024-99"])),
            pnpm_report(),
        )
        == 0
    )


@pytest.mark.id("SEC-09")
def test_missing_or_failed_reports_are_errors(tmp_path: Path) -> None:
    assert (
        run_audit(
            tmp_path, pip_report(), {"error": {"code": "ERR_PNPM_AUDIT_BAD_RESPONSE"}}
        )
        == 2
    )
    assert (
        ag.main(
            [
                "--pip",
                str(tmp_path / "nope.json"),
                "--pnpm",
                str(tmp_path / "nope.json"),
            ]
        )
        == 2
    )


def zap_report(*alerts: tuple[str, int]) -> dict[str, object]:
    return {
        "site": [
            {
                "@name": "http://host.docker.internal:3000",
                "alerts": [
                    {
                        "pluginid": pid,
                        "riskcode": str(risk),
                        "name": f"alert {pid}",
                        "count": "2",
                    }
                    for pid, risk in alerts
                ],
            }
        ]
    }


RULES = "# comment\n10202\tIGNORE\t(Absence of Anti-CSRF Tokens: header-based double submit)\n10096\tWARN\t(x)\n"


def run_zap(tmp: Path, report: object, rules: str = RULES) -> int:
    rules_path = tmp / "rules.tsv"
    rules_path.write_text(rules, encoding="utf-8")
    code: int = zg.main(
        [str(write(tmp, "zap.json", report)), "--rules", str(rules_path)]
    )
    return code


@pytest.mark.id("SEC-08")
def test_zap_low_and_informational_pass(tmp_path: Path) -> None:
    assert run_zap(tmp_path, zap_report(("10096", 0), ("10010", 1))) == 0


@pytest.mark.id("SEC-08")
def test_zap_medium_or_high_fails_unless_ignored_with_a_reason(tmp_path: Path) -> None:
    assert run_zap(tmp_path, zap_report(("10038", 2))) == 1
    assert run_zap(tmp_path, zap_report(("40012", 3))) == 1
    assert run_zap(tmp_path, zap_report(("10202", 2))) == 0
    assert run_zap(tmp_path, zap_report(("10096", 2))) == 1, "WARN is not a suppression"


@pytest.mark.id("SEC-08")
def test_zap_rule_without_a_reason_is_an_error(tmp_path: Path) -> None:
    assert run_zap(tmp_path, zap_report(), "10202\tIGNORE\n") == 2
    assert run_zap(tmp_path, zap_report(), "not-a-rule\n") == 2


@pytest.mark.id("SEC-08")
def test_repo_rules_file_is_valid() -> None:
    ignored = zg.load_rules(ROOT / "infra" / "zap" / "baseline-rules.tsv")
    assert set(ignored) == {"10202", "10010"}
