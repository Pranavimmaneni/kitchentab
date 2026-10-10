"""VERCEL-10: `make deploy-setup` against the stubbed Vercel CLI (ADR-0022)."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys

import pytest
from deploy_harness import ROOT, Harness

SCRIPT = "deploy_setup.py"
SECRET_VARS = (
    "SESSION_SECRET",
    "TOKEN_HASH_PEPPER",
    "CRON_SECRET",
    "TEST_INBOX_SECRET",
    "VAPID_PRIVATE_KEY",
)
CONFIG = {
    "PROFILE": "vercel",
    "QUEUE_PROVIDER": "vercel",
    "STORAGE_PROVIDER": "vercel_blob",
    "PUSH_PROVIDER": "webpush",
    "SMS_PROVIDER": "none",
    "EXTRACTOR_PROVIDER": "local",
    "EMAIL_PROVIDER": "testinbox",
}


def _env_adds(h: Harness) -> list[tuple[str, str]]:
    return [(a[2], a[3]) for a in h.vercel_argvs() if a[:2] == ["env", "add"]]


@pytest.mark.id("VERCEL-10")
def test_vercel10_dry_run_prints_every_command_and_runs_none(harness: Harness) -> None:
    run = harness.run(SCRIPT, "--dry-run")
    assert run.code == 0, run.text
    assert harness.calls() == []  # nothing ran, not even `vercel whoami`
    for line in (
        "[dry-run] $ vercel whoami",
        "[dry-run] $ vercel link --yes --project kitchentab",
        "[dry-run] $ vercel integration add neon",
        "[dry-run] $ vercel blob store add kitchentab-receipts",
        "[dry-run] $ vercel env add SESSION_SECRET production < <generated secret: 86 chars>",
        "[dry-run] $ vercel env add TEST_INBOX_SECRET preview < <generated secret: 86 chars>",
        "[dry-run] $ vercel env add VAPID_PRIVATE_KEY production < <generated P-256 private key: 43 chars>",
        "[dry-run] $ vercel env add APP_ENV production < prod",
        "[dry-run] $ vercel env add APP_ENV preview < staging",
        "[dry-run] $ vercel env add EMAIL_PROVIDER production < testinbox",
        "Framework Preset -> Services",
        "Queues",
    ):
        assert line in run.out, line
    assert not (harness.root / ".vercel").exists()


@pytest.mark.id("VERCEL-10")
def test_vercel10_happy_path_sets_every_variable_for_production_and_preview(
    harness: Harness,
) -> None:
    run = harness.run(SCRIPT)
    assert run.code == 0, run.text
    assert f"vercel: {harness.stub_path()}" in run.out
    argvs = harness.vercel_argvs()
    assert argvs[0] == ["whoami"]
    assert ["link", "--yes", "--project", "kitchentab"] in argvs
    assert ["integration", "add", "neon"] in argvs
    assert ["blob", "store", "add", "kitchentab-receipts"] in argvs
    assert (
        json.loads((harness.root / ".vercel" / "project.json").read_text())[
            "projectName"
        ]
        == "kitchentab"
    )
    for env, app_env in (("production", "prod"), ("preview", "staging")):
        values = harness.env_vars(env)
        assert values["APP_ENV"] == app_env
        for name, value in CONFIG.items():
            assert values[name] == value, name
        assert len(values["SESSION_SECRET"]) >= 64
        assert (
            len(values["TOKEN_HASH_PEPPER"]) >= 32 and len(values["CRON_SECRET"]) >= 32
        )
        assert len(values["TEST_INBOX_SECRET"]) >= 64
        _assert_vapid_pair(values["VAPID_PUBLIC_KEY"], values["VAPID_PRIVATE_KEY"])
        for name in ("WEB_ORIGIN", "PUBLIC_LINK_BASE", "GOOGLE_REDIRECT_URI"):
            assert name not in values  # set by `make deploy` once the URL is known
    prod, preview = harness.env_vars("production"), harness.env_vars("preview")
    assert all(
        prod[n] != preview[n] for n in SECRET_VARS
    )  # separate secrets per environment
    # Each variable was added exactly once.
    adds = _env_adds(harness)
    assert len(adds) == len(set(adds))
    assert "Done. Next: `make deploy`." in run.out


@pytest.mark.id("VERCEL-10")
def test_vercel10_setup_satisfies_the_api_deployed_mode_validation(
    harness: Harness,
) -> None:
    """Every variable apps/api/app/settings.py requires in production is set (plus the URL ones
    that `make deploy` adds after the first deploy)."""
    assert harness.run(SCRIPT).code == 0
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in ("SYSTEMROOT", "PATH", "TEMP", "TMP", "HOME")
    }
    env.update(harness.env_vars("production"))
    origin = "https://kitchentab.vercel.app"
    env.update(
        WEB_ORIGIN=origin,
        PUBLIC_LINK_BASE=origin,
        GOOGLE_REDIRECT_URI=origin + "/api/v1/auth/google/callback",
    )
    check = (
        "from app.settings import Settings\n"
        "s = Settings(_env_file=None)\n"
        "assert s.is_deployed and s.app_env == 'prod', s.app_env\n"
        "print('valid')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", check],
        cwd=ROOT / "apps" / "api",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "valid" in proc.stdout


@pytest.mark.id("VERCEL-10")
def test_vercel10_rerun_is_idempotent_and_never_regenerates_secrets(
    harness: Harness,
) -> None:
    assert harness.run(SCRIPT).code == 0
    before = {env: harness.env_vars(env) for env in ("production", "preview")}
    harness.reset_calls()
    again = harness.run(SCRIPT)
    assert again.code == 0, again.text
    argvs = harness.vercel_argvs()
    assert not [a for a in argvs if a[:2] in (["env", "add"], ["env", "rm"])]
    assert ["link", "--yes", "--project", "kitchentab"] not in argvs
    assert ["integration", "add", "neon"] not in argvs
    assert not [a for a in argvs if a[:1] == ["blob"]]
    assert {env: harness.env_vars(env) for env in ("production", "preview")} == before
    assert (
        "kept (already set, never regenerated): SESSION_SECRET (production)"
        in again.out
    )
    assert "Already linked to kitchentab" in again.out


@pytest.mark.id("VERCEL-10")
def test_vercel10_network_error_midway_then_rerun_resumes(harness: Harness) -> None:
    first = harness.run(SCRIPT, fail="network@14")
    assert first.code == 1
    assert "Network problem" in first.err and "re-run `make deploy-setup`" in first.err
    partial = {env: harness.env_vars(env) for env in ("production", "preview")}
    assert partial["production"], "some variables were set before the failure"
    harness.reset_calls()
    second = harness.run(SCRIPT)
    assert second.code == 0, second.text
    for env, values in partial.items():
        current = harness.env_vars(env)
        for name, value in values.items():
            assert current[name] == value, f"{name} ({env}) was regenerated"
    adds = _env_adds(harness)
    assert not [
        a for a in adds if a[1] == "production" and a[0] in partial["production"]
    ]
    harness.assert_no_secrets(first, second)


@pytest.mark.id("VERCEL-10")
def test_vercel10_not_logged_in_says_run_vercel_login(harness: Harness) -> None:
    run = harness.run(SCRIPT, fail="whoami")
    assert run.code == 1
    assert "You're not logged in to Vercel." in run.err
    assert "Run `vercel login`" in run.err
    assert harness.vercel_argvs() == [["whoami"]]


@pytest.mark.id("VERCEL-10")
def test_vercel10_link_failure_is_reported_and_stops(harness: Harness) -> None:
    run = harness.run(SCRIPT, fail="link")
    assert run.code == 1
    assert "Could not link this folder to the Vercel project 'kitchentab'" in run.err
    assert "limit of 200 projects" in run.err
    assert not [a for a in harness.vercel_argvs() if a[:1] == ["env"]]


@pytest.mark.id("VERCEL-10")
def test_vercel10_env_var_that_exists_but_is_not_listed_stops_with_a_clear_message(
    harness: Harness,
) -> None:
    assert harness.run(SCRIPT).code == 0
    kept = harness.env_vars("production")["SESSION_SECRET"]
    harness.reset_calls()
    run = harness.run(SCRIPT, fail="env-exists")
    assert run.code == 1
    assert (
        "already exists in production, but `vercel env ls production` didn't list it"
        in run.err
    )
    assert harness.env_vars("production")["SESSION_SECRET"] == kept
    harness.assert_no_secrets(run)


@pytest.mark.id("VERCEL-10")
def test_vercel10_unconnected_stores_print_the_dashboard_steps(
    harness: Harness,
) -> None:
    run = harness.run(SCRIPT, fail="neon-unlinked,blob-unlinked")
    assert run.code == 3
    assert "ACTION NEEDED" in run.out
    assert "Storage -> the Neon database -> Connect Project -> kitchentab" in run.out
    assert "Storage -> kitchentab-receipts -> Connect Project -> kitchentab" in run.out


@pytest.mark.id("VERCEL-10")
def test_vercel10_integration_failure_is_reported(harness: Harness) -> None:
    run = harness.run(SCRIPT, fail="integration")
    assert run.code == 1
    assert "Could not add the Neon integration" in run.err


@pytest.mark.id("VERCEL-10")
def test_vercel10_postal_address_flag_is_stored_and_kept(harness: Harness) -> None:
    run = harness.run(
        SCRIPT, "--postal-address", "KitchenTab, 1 Main St, Newark, NJ 07102"
    )
    assert run.code == 0, run.text
    assert harness.env_vars("production")["EMAIL_POSTAL_ADDRESS"].startswith(
        "KitchenTab, 1 Main St"
    )
    harness.reset_calls()
    assert harness.run(SCRIPT, "--postal-address", "something else").code == 0
    assert harness.env_vars("production")["EMAIL_POSTAL_ADDRESS"].startswith(
        "KitchenTab, 1 Main St"
    )


@pytest.mark.id("VERCEL-10")
def test_vercel10_secrets_never_appear_in_output_or_logs(harness: Harness) -> None:
    run = harness.run(SCRIPT)
    assert run.code == 0
    assert harness.secret_values(), "the fake account holds secrets to look for"
    harness.assert_no_secrets(run)
    for call in harness.calls("vercel"):
        assert len(call["argv"]) < 6  # values go on stdin, never argv
    assert "<secret: 86 chars>" in run.out


@pytest.mark.id("VERCEL-10")
def test_vercel10_only_the_stub_cli_is_ever_resolved(harness: Harness) -> None:
    stub = harness.stub_path()
    assert stub is not None and stub.startswith(str(harness.bin))
    real = shutil.which("vercel")  # whatever the developer has installed
    run = harness.run(SCRIPT)
    assert run.code == 0
    assert f"vercel: {stub}" in run.out
    if real:
        assert real not in run.text
    calls = harness.calls("vercel")
    assert calls and all(c["exe"].endswith("fake_vercel.py") for c in calls)


@pytest.mark.id("VERCEL-10")
def test_vercel10_vapid_keys_are_valid_p256_pairs() -> None:
    from deploylib import new_vapid_keys, vapid_public_from_private

    public, private = new_vapid_keys()
    _assert_vapid_pair(public, private.reveal())
    assert vapid_public_from_private(private.reveal()) == public
    assert str(private) == "<secret: 43 chars>"


def _assert_vapid_pair(public: str, private: str) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from py_vapid import Vapid

    def unb64(s: str) -> bytes:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

    raw_public = unb64(public)
    assert len(raw_public) == 65 and raw_public[0] == 4
    key = ec.derive_private_key(int.from_bytes(unb64(private), "big"), ec.SECP256R1())
    assert (
        key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        == raw_public
    )
    vapid = Vapid.from_string(private)  # how pywebpush loads VAPID_PRIVATE_KEY
    assert (
        vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        == raw_public
    )
