"""PERF-01 and PERF-02 (TEST_PLAN 3.11): `make test-load` runs `scripts/load_test.py` through here.

The driver starts its own API instances on a fresh database, builds 200 houses, runs k6 (Docker) for
PERF-01 and the 1,000-stream fan-out for PERF-02, then the invariant checker; its report is checked
here so the numbers sit next to the test IDs. Opt-in (`-m load`): it takes minutes and wants a
quiet machine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.harness.tags import tid

REPO = Path(__file__).resolve().parents[3]
REPORT = REPO / ".cache" / "load" / "report.json"


@pytest.mark.load
@tid("PERF-01", "PERF-02")
def test_PERF_01_02_two_hundred_people_and_a_thousand_streams() -> None:
    REPORT.unlink(missing_ok=True)
    minutes = os.environ.get("LOAD_MINUTES", "2")
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(REPO / "scripts" / "load_test.py"), "--minutes", minutes],
        cwd=REPO,
        check=False,
        timeout=60 * 60,
    )
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in report["results"]}
    perf01, perf02 = by_id["PERF-01"], by_id["PERF-02"]
    assert perf01["error_rate"] == 0, perf01
    assert perf01["p95_ms"] < 300, f"PERF-01 p95 {perf01['p95_ms']} ms (target < 300)"
    assert perf02["streams_open"] == perf02["streams"] == 1000, perf02
    assert perf02["missed_deliveries"] == 0 and perf02["error_count"] == 0, perf02
    assert perf02["p95_s"] < 2.0, f"PERF-02 delivery p95 {perf02['p95_s']} s (target < 2 s)"
    assert by_id["invariants"]["violations"] == []
    assert proc.returncode == 0
