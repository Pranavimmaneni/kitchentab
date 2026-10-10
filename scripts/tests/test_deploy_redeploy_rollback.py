"""VERCEL-10: `make redeploy` and `make rollback` against the stubbed Vercel CLI (ADR-0022)."""

from __future__ import annotations

import pytest
from deploy_harness import ORIGIN, Harness


@pytest.mark.id("VERCEL-10")
def test_vercel10_redeploy_dry_run_runs_nothing(harness: Harness) -> None:
    run = harness.run("redeploy.py", "--dry-run")
    assert run.code == 0, run.text
    assert (
        "[dry-run] $ vercel redeploy https://<current-production-deployment>.vercel.app"
        in run.out
    )
    assert harness.calls() == []


@pytest.mark.id("VERCEL-10")
def test_vercel10_redeploy_rebuilds_what_the_domain_serves(harness: Harness) -> None:
    harness.provision(deployments=3)
    state = harness.state()
    live = state["deployments"][1][
        "url"
    ]  # rolled back earlier: the live one isn't the newest
    state["current"] = live
    harness.save_state(state)
    run = harness.run("redeploy.py")
    assert run.code == 0, run.text
    assert ["inspect", ORIGIN] in harness.vercel_argvs()
    assert ["redeploy", live] in harness.vercel_argvs()
    new = harness.state()["deployments"][-1]
    assert harness.state()["current"] == new["url"] and new["target"] == "production"
    assert f"Redeployed: {new['url']}" in run.out
    harness.assert_no_secrets(run)


@pytest.mark.id("VERCEL-10")
def test_vercel10_redeploy_failure_keeps_production(harness: Harness) -> None:
    harness.provision()
    live = harness.state()["current"]
    run = harness.run("redeploy.py", fail="deploy")
    assert run.code == 1
    assert (
        "The redeploy failed. The current production deployment is still live."
        in run.err
    )
    assert harness.state()["current"] == live


@pytest.mark.id("VERCEL-10")
def test_vercel10_rollback_dry_run_runs_nothing(harness: Harness) -> None:
    run = harness.run("rollback.py", "--dry-run")
    assert run.code == 0, run.text
    assert "[dry-run] $ vercel ls kitchentab" in run.out
    assert "[dry-run] $ vercel promote <chosen-deployment-url> --yes" in run.out
    assert harness.calls() == []


@pytest.mark.id("VERCEL-10")
def test_vercel10_rollback_lists_and_promotes_the_chosen_deployment(
    harness: Harness,
) -> None:
    harness.provision(deployments=3)
    urls = [
        d["url"] for d in harness.state()["deployments"]
    ]  # oldest first; the last is live
    run = harness.run(
        "rollback.py", stdin="3\n"
    )  # list is newest first: 3 = the oldest
    assert run.code == 0, run.text
    assert f"1. {urls[2]}" in run.out and "<- live now" in run.out
    assert f"3. {urls[0]}" in run.out
    assert ["promote", urls[0], "--yes"] in harness.vercel_argvs()
    assert harness.state()["current"] == urls[0]
    assert f"Rolled back. Production now serves {urls[0]}." in run.out
    assert "database migrations were not rolled back" in run.out


@pytest.mark.id("VERCEL-10")
def test_vercel10_rollback_default_is_the_previous_deployment(harness: Harness) -> None:
    harness.provision(deployments=3)
    urls = [d["url"] for d in harness.state()["deployments"]]
    run = harness.run("rollback.py", "--yes")
    assert run.code == 0, run.text
    assert harness.state()["current"] == urls[1]


@pytest.mark.id("VERCEL-10")
def test_vercel10_rollback_failures_are_clear(harness: Harness) -> None:
    harness.provision(deployments=1)
    run = harness.run("rollback.py", "--yes")
    assert run.code == 1 and "no earlier ready production deployment" in run.err
    harness.provision(deployments=2)
    live = harness.state()["current"]
    run = harness.run("rollback.py", "--yes", fail="promote")
    assert run.code == 1
    assert "The rollback failed; production is unchanged." in run.err
    assert harness.state()["current"] == live
    run = harness.run("rollback.py", "--to", "https://not-a-deployment.vercel.app")
    assert run.code == 1 and "isn't one of the listed deployments" in run.err
