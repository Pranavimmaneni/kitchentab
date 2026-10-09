# ADR-0004: Milestone-aware traceability

`scripts/check_traceability.py` parses FR IDs from the PRD and test IDs from TEST_PLAN, then scans
pytest (`@tid`, `@fr`), Vitest and Playwright (IDs at the start of test titles, FRs as `[FR-xx]`).
It enforces coverage only for milestones whose heading in `docs/PROGRESS.md` ends with `[done]`,
and reports overall coverage. `--all` (used in P10) enforces everything. PRD §15 and §19.9 rows
have no IDs, so `docs/traceability/edge_cases.yaml` assigns `EDGE-15-nn` and `EDGE-19-nn` and the
script requires a test for each. §21.7 rows are SYNC-01..24.
