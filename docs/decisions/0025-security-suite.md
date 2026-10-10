# ADR-0025: The security suite (`make test-security`) and pip-audit

**Context.** TEST_PLAN 3.10 asks for an authorization matrix, IDOR, injection and secret checks, a
ZAP baseline and dependency audits (SEC-01..09), run nightly and before release.

**Decision.**

- `pip-audit` is added to the API `dev` dependency group (pre-approved for SEC-09; not named in
  PRD §12). It audits the *locked* environment: `uv export --frozen --all-groups` to a hashed
  requirements file, then `pip-audit --disable-pip --require-hashes`, so the result matches
  `uv.lock` exactly and nothing is resolved or installed.
- `pnpm audit` runs in `apps/web`. Both JSON reports go through `scripts/audit_gate.py`: any
  pip-audit finding (PyPI advisories carry no severity) or any pnpm `high`/`critical` advisory
  fails, unless its GHSA/PYSEC/CVE id is listed under "Waivers" in `docs/PROGRESS.md` with the
  reason it is real but unfixable. There is no ignore list anywhere else.
- SEC-01 is a table in `tests/test_security_matrix.py`: every `/api/v1` route x six roles (admin,
  payer, member, bill-link holder, member of another house, signed out). A route missing from
  the table fails the suite, so new endpoints force an authorization decision. Read rows share
  one world; each write row gets a fresh one (an allowed write may delete or transfer things).
  Cron routes are tested separately (bearer secret); `/__test` and `/api/test` are SEC-11's.
- SEC-07 has two halves: a DEBUG-level flow whose JSON log output and `app.*` log calls must not
  contain any secret, and `scripts/scan_bundle_secrets.py`, which greps the built web bundle for
  server-only env names and values (run only when a build exists; the release gate builds first).
- SEC-08 (`make zap-baseline`) runs `ghcr.io/zaproxy/zaproxy:stable` against `ZAP_TARGET` (default
  `http://host.docker.internal:3000`; it never starts the web app). `scripts/zap_gate.py` fails
  on High/Medium alerts not suppressed in `infra/zap/baseline-rules.tsv`, where every IGNORE
  carries its reason. `make test-security` runs it only when `ZAP_TARGET` is set.

**Consequences.** The suite runs under both profiles. It is slow (one world per write row, about
5 minutes per profile on a laptop) because it goes through real HTTP and Postgres on purpose.
