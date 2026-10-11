# Paste this into Claude Code (from the repo root, auto mode, Opus at high effort)

```
/goal The kitchen gestures feature in docs/features/kitchen-gestures/SPEC.md is fully built and verified, shown with command output in this transcript: every FR-G01..FR-G11 is implemented in the Fridge, Pantry and Review & send screens in both themes; every test ID in SPEC.md §9 (GEST-01..15, SYNC-G1..G6 at 200 iterations in both PROFILEs, HAPT-01..03, ANIM-01..03, A11Y-G1, VIS-G1) exists in docs/TEST_PLAN.md and in the codebase and passes; make lint test test-sync e2e e2e-multi exits 0 twice in a row with no regressions in existing tests; scripts/check_traceability.py reports 100%; Playwright screenshots of states r2, m2, s2, c3 and sort-after at 390 px in light and dark have been compared with docs/design/renders/gestures/ and every difference fixed except token-level colors; docs/PROGRESS.md has a new "Milestone 11 · Kitchen gestures" section with all items checked; the PRD (§19, §20, §21) and docs/design/README.md are updated to reference the new spec; everything is committed with FR-G and test IDs in messages and git status is clean. How to work: read CLAUDE.md, docs/features/kitchen-gestures/SPEC.md, docs/design/gestures/ and the renders first. Build the gesture reducer test-first, then the move and item-order endpoints (four write kinds, policy.py, house events), then the UI, then haptics and the stocking animation. Do not add new dependencies. Never stop to ask: decide, record ADRs, keep going. Fix failures at the root; never weaken, skip or delete tests. After the build, spawn a fresh-eyes reviewer subagent with SPEC.md and the diff to hunt for gesture edge cases, sync races and accessibility gaps, then fix every confirmed finding. Do not deploy. Or stop after 300 turns, leaving docs/PROGRESS.md accurate for a fresh session to resume.
```

## Before pasting

1. Unzip this kit into the repo root (it only adds files; nothing is overwritten).
2. In Claude Code, run `/goal` with no arguments. If the main build goal is still active, let it
   finish first: a new `/goal` replaces the old one.
3. Make sure the mode shows **auto** (Shift+Tab).

## After it finishes

- Pull it up on your phone (or run `make redeploy` if it's already on Vercel) and try the five
  gestures on a real iPhone and Android. Long-press feel and haptics can't be judged in emulation.
