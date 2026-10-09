# ADR-0019: Web token adjustments found while building the components

- Light `--warn` is #BE381D, not #C23A1E: the PRD value is 4.45:1 on `--bg` (#ECEAE2), below the
  4.5:1 that A11Y-05 requires for "you owe" text. The new value is 4.62:1 and looks the same.
- Extra tokens the PRD table did not name: `--on-accent` (#121210 on lime), `--till` / `--on-till`
  (the split totals bar is #121210 in both themes, as in Split.dc.html) and `--tag-*` for the
  FRIDGE / FREEZER / PANTRY / NOT FOOD tags (sky, lilac, tan, outline per §19.8).
- SegmentedControl options and ItemRow buttons are 44 px tall, not 40 px, to meet the 44 px
  target rule (CLAUDE.md rule 9, A11Y-06).
- Playwright Firefox does not start from `%LOCALAPPDATA%\ms-playwright` on Pranav's Windows machine
  (side-by-side configuration error in mozglue); it runs from another folder. Local runs set
  `PLAYWRIGHT_BROWSERS_PATH` to a repo-ignored `.cache/ms-playwright`; CI (Linux) is unaffected.
