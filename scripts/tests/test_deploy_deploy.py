"""VERCEL-10 / VERCEL-06: `make deploy` against the stubbed Vercel CLI, uv and make (ADR-0022)."""

from __future__ import annotations

from pathlib import Path

import pytest
from deploy_harness import ORIGIN, Harness

SCRIPT = "deploy.py"
URL_VARS = ("WEB_ORIGIN", "PUBLIC_LINK_BASE", "GOOGLE_REDIRECT_URI", "VAPID_SUBJECT")


def first_deploy_state(h: Harness) -> None:
    """As left by `make deploy-setup`: everything except the URL settings, no deployment yet."""
    h.provision(deployments=0)
    state = h.state()
    for env in ("production", "preview"):
        for name in URL_VARS:
            state["env"][env].pop(name)
    state["aliases"] = []
    h.save_state(state)
    (h.root / ".vercel" / "kitchentab-deploy.json").unlink()


@pytest.mark.id("VERCEL-10", "VERCEL-06")
def test_vercel10_dry_run_prints_the_whole_pipeline_and_runs_nothing(
    harness: Harness,
) -> None:
    run = harness.run(SCRIPT, "--dry-run")
    assert run.code == 0, run.text
    assert harness.calls() == []
    for line in (
        "[dry-run] $ vercel whoami",
        "[dry-run] $ make lint test",
        "--environment=production --yes",
        "[dry-run] $ (cd apps/api && DATABASE_DIRECT_URL=<Neon direct URL> uv run alembic upgrade head)",
        "[dry-run] $ vercel deploy --prod",
        "[dry-run] $ vercel inspect https://<new-deployment>.vercel.app",
        "[dry-run] $ vercel env add WEB_ORIGIN production < https://kitchentab.vercel.app",
        "[dry-run] $ vercel env add PUBLIC_LINK_BASE production < https://kitchentab.vercel.app",
        (
            "[dry-run] $ vercel env add GOOGLE_REDIRECT_URI production < "
            "https://kitchentab.vercel.app/api/v1/auth/google/callback"
        ),
        "[dry-run] $ vercel redeploy https://<new-deployment>.vercel.app",
    ):
        assert line in run.out, line
    skipped = harness.run(SCRIPT, "--dry-run", "--skip-checks")
    assert skipped.code == 0 and "make lint test" not in skipped.out
    assert harness.calls() == []


@pytest.mark.id("VERCEL-10")
def test_vercel10_skip_checks_is_refused_outside_dry_run(harness: Harness) -> None:
    harness.provision()
    run = harness.run(SCRIPT, "--skip-checks")
    assert run.code == 2
    assert "--skip-checks is only allowed with --dry-run" in run.err
    assert harness.calls() == []


@pytest.mark.id("VERCEL-10", "VERCEL-06")
def test_vercel10_first_deploy_migrates_deploys_sets_origins_and_redeploys(
    harness: Harness,
) -> None:
    first_deploy_state(harness)
    neon_password = (
        harness.state()["env"]["production"]["DATABASE_URL"].split(":")[2].split("@")[0]
    )
    run = harness.run(SCRIPT)
    assert run.code == 0, run.text
    order = [(c["tool"], c["argv"][:2]) for c in harness.calls()]
    assert order[:5] == [
        ("vercel", ["whoami"]),
        ("make", ["lint", "test"]),
        ("vercel", ["env", "pull"]),
        ("uv", ["run", "alembic"]),
        ("vercel", ["deploy", "--prod"]),
    ]
    migrate = harness.calls("uv")[0]
    assert migrate["argv"] == ["run", "alembic", "upgrade", "head"]
    assert Path(migrate["cwd"]) == harness.root / "apps" / "api"
    # Neon's direct (unpooled) host, rewritten for SQLAlchemy+asyncpg.
    assert migrate["db"]["scheme"] == "postgresql+asyncpg"
    assert migrate["db"]["host"] == "ep-fancy-snow-a5k2j3l4.us-east-2.aws.neon.tech"
    assert migrate["db"]["query"] == {"ssl": "require"}
    assert migrate["db"]["has_password"]
    # The pulled env file lived outside the repo and is gone.
    pulled = harness.state()["pulled_files"]
    assert pulled and not any(Path(p).exists() for p in pulled)
    assert not any(str(harness.root) in p for p in pulled)
    prod = harness.env_vars("production")
    assert prod["WEB_ORIGIN"] == ORIGIN and prod["PUBLIC_LINK_BASE"] == ORIGIN
    assert prod["GOOGLE_REDIRECT_URI"] == ORIGIN + "/api/v1/auth/google/callback"
    assert prod["VAPID_SUBJECT"] == ORIGIN
    deployments = harness.state()["deployments"]
    assert len(deployments) == 2  # deploy + redeploy with the new settings
    assert (
        deployments[0]["web_origin"] is None and deployments[1]["web_origin"] == ORIGIN
    )
    assert harness.state()["current"] == deployments[1]["url"]
    assert f"Production: {ORIGIN}" in run.out
    assert f"Deployed: {deployments[0]['url']}" in run.out
    harness.assert_no_secrets(run, extra=(neon_password,))


@pytest.mark.id("VERCEL-10")
def test_vercel10_second_deploy_does_not_touch_settings_or_redeploy(
    harness: Harness,
) -> None:
    harness.provision()
    run = harness.run(SCRIPT)
    assert run.code == 0, run.text
    argvs = harness.vercel_argvs()
    assert not [a for a in argvs if a[:2] in (["env", "add"], ["env", "rm"])]
    assert not [a for a in argvs if a[:1] == ["redeploy"]]
    assert "already right" in run.out


@pytest.mark.id("VERCEL-10")
def test_vercel10_custom_domain_wins_and_changed_origin_is_replaced(
    harness: Harness,
) -> None:
    harness.provision()
    run = harness.run(SCRIPT, "--domain", "https://kitchen.example.com")
    assert run.code == 0, run.text
    argvs = harness.vercel_argvs()
    assert ["env", "rm", "WEB_ORIGIN", "production", "--yes"] in argvs
    assert harness.env_vars("production")["WEB_ORIGIN"] == "https://kitchen.example.com"
    assert (
        harness.env_vars("production")["VAPID_SUBJECT"] == ORIGIN
    )  # already set: left alone
    assert [a for a in argvs if a[:1] == ["redeploy"]]


@pytest.mark.id("VERCEL-10", "VERCEL-06")
def test_vercel10_migration_failure_stops_before_deploying(harness: Harness) -> None:
    harness.provision()
    run = harness.run(SCRIPT, extra_env={"FAKE_UV_FAIL": "1"})
    assert run.code == 1
    assert "Database migrations failed, so nothing was deployed" in run.err
    assert 'relation "widgets" already exists' in run.err
    assert not [
        a for a in harness.vercel_argvs() if a[:1] in (["deploy"], ["redeploy"])
    ]
    assert not any(Path(p).exists() for p in harness.state()["pulled_files"])
    harness.assert_no_secrets(
        run
    )  # includes the Neon password inside DATABASE_URL_UNPOOLED


@pytest.mark.id("VERCEL-10")
def test_vercel10_deploy_failure_keeps_production_and_says_so(harness: Harness) -> None:
    harness.provision()
    live = harness.state()["current"]
    run = harness.run(SCRIPT, fail="deploy")
    assert run.code == 1
    assert (
        "The Vercel deploy failed. The previous production deployment is still live."
        in run.err
    )
    assert harness.state()["current"] == live
    assert not [a for a in harness.vercel_argvs() if a[:2] == ["env", "add"]]


@pytest.mark.id("VERCEL-10")
def test_vercel10_failing_checks_stop_everything(harness: Harness) -> None:
    harness.provision()
    run = harness.run(SCRIPT, extra_env={"FAKE_MAKE_FAIL": "1"})
    assert run.code == 1
    assert "Lint or unit tests failed, so nothing was deployed." in run.err
    assert [c["tool"] for c in harness.calls()] == ["vercel", "make"]


@pytest.mark.id("VERCEL-10")
def test_vercel10_not_linked_or_setup_incomplete_points_to_deploy_setup(
    harness: Harness,
) -> None:
    run = harness.run(SCRIPT)
    assert run.code == 1 and "Run `make deploy-setup` first." in run.err
    harness.provision()
    state = harness.state()
    del (
        state["env"]["production"]["VAPID_PRIVATE_KEY"],
        state["env"]["production"]["BLOB_READ_WRITE_TOKEN"],
    )
    harness.save_state(state)
    run = harness.run(SCRIPT)
    assert run.code == 1
    assert "Production is missing: VAPID_PRIVATE_KEY, BLOB_READ_WRITE_TOKEN." in run.err
    assert not harness.calls("uv")


@pytest.mark.id("VERCEL-10")
def test_vercel10_not_logged_in_and_network_errors_are_clear(harness: Harness) -> None:
    harness.provision()
    run = harness.run(SCRIPT, fail="whoami")
    assert run.code == 1 and "Run `vercel login`" in run.err
    harness.reset_calls()
    state = harness.state()
    state["calls"] = 0
    harness.save_state(state)
    run = harness.run(SCRIPT, fail="network@2")  # whoami ok, env pull fails
    assert run.code == 1
    assert (
        "Network problem while trying to read the production environment variables"
        in run.err
    )
    assert not harness.calls("uv")


@pytest.mark.id("VERCEL-10")
def test_vercel10_database_urls_are_rewritten_for_each_driver() -> None:
    from deploylib import asyncpg_sqlalchemy_url, libpq_url

    neon = "postgresql://u:p@ep-x.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    assert (
        asyncpg_sqlalchemy_url(neon)
        == "postgresql+asyncpg://u:p@ep-x.aws.neon.tech/neondb?ssl=require"
    )
    assert (
        libpq_url(neon) == "postgresql://u:p@ep-x.aws.neon.tech/neondb?sslmode=require"
    )
    assert (
        libpq_url("postgresql+asyncpg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    )
