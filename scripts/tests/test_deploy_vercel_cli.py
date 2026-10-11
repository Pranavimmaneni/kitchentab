"""VERCEL-10: the deploy scripts resolve the npm Vercel CLI, not the Python `vercel` package's
console script that `uv run` puts first on PATH (apps/api/.venv/Scripts/vercel.exe)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from deploy_harness import DECOY_TEXT, Harness, _system_dirs, make_venv_decoy
from deploylib import DeployError, find_vercel_cli


def _cli(harness: Harness) -> str:
    found = shutil.which("vercel", path=str(harness.bin))
    assert found is not None
    return found


@pytest.mark.id("VERCEL-10")
def test_vercel10_skips_a_vercel_inside_a_virtualenv(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VERCEL_CLI", raising=False)
    decoy = make_venv_decoy(harness.tmp / "venv")
    path = os.pathsep.join([str(decoy), str(harness.bin)])
    plain = shutil.which("vercel", path=path)  # what the scripts used to run
    assert plain is not None and Path(plain).parent == decoy
    assert find_vercel_cli(path) == _cli(harness)


@pytest.mark.id("VERCEL-10")
def test_vercel10_skips_the_running_interpreters_scripts_folder(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even without a pyvenv.cfg (a conda env, a base install), sys.prefix's own folder is skipped."""
    monkeypatch.delenv("VERCEL_CLI", raising=False)
    decoy = make_venv_decoy(harness.tmp / "prefix")
    (harness.tmp / "prefix" / "pyvenv.cfg").unlink()
    monkeypatch.setattr(sys, "prefix", str(harness.tmp / "prefix"))
    path = os.pathsep.join([str(decoy), str(harness.bin)])
    assert find_vercel_cli(path) == _cli(harness)


@pytest.mark.id("VERCEL-10")
def test_vercel10_none_when_only_the_venv_has_vercel(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VERCEL_CLI", raising=False)
    decoy = make_venv_decoy(harness.tmp / "venv")
    assert find_vercel_cli(str(decoy)) is None


@pytest.mark.id("VERCEL-10")
def test_vercel10_scripts_run_the_cli_when_a_venv_is_first_on_path(
    harness: Harness,
) -> None:
    harness.provision()
    decoy = make_venv_decoy(harness.tmp / "venv")
    path = os.pathsep.join([str(decoy), str(harness.bin), *_system_dirs()])
    run = harness.run("redeploy.py", extra_env={"PATH": path})
    assert run.code == 0, run.text
    assert f"vercel: {_cli(harness)}" in run.out
    assert str(decoy) not in run.text and DECOY_TEXT not in run.text
    calls = harness.calls("vercel")
    assert calls and all(c["exe"].endswith("fake_vercel.py") for c in calls)


@pytest.mark.id("VERCEL-10")
def test_vercel10_vercel_cli_env_overrides_path(harness: Harness) -> None:
    harness.provision()
    decoy = make_venv_decoy(harness.tmp / "venv")
    path = os.pathsep.join([str(decoy), *_system_dirs()])  # no CLI on PATH at all
    run = harness.run(
        "redeploy.py", extra_env={"PATH": path, "VERCEL_CLI": _cli(harness)}
    )
    assert run.code == 0, run.text
    assert f"vercel: {_cli(harness)}" in run.out
    assert harness.calls("vercel")


@pytest.mark.id("VERCEL-10")
def test_vercel10_vercel_cli_env_pointing_nowhere_is_a_clear_error(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = str(harness.tmp / "no-such-vercel")
    monkeypatch.setenv("VERCEL_CLI", missing)
    with pytest.raises(DeployError, match="VERCEL_CLI is set to"):
        find_vercel_cli()
    run = harness.run("redeploy.py", extra_env={"VERCEL_CLI": missing})
    assert run.code == 1
    assert "VERCEL_CLI is set to" in run.err
    assert harness.calls() == []
