# KitchenTab · Kitchen gestures handoff

New features for the Fridge, Pantry and Review & send screens:

1. Hold and drag to reorder (personal order)
2. Hold for quick actions in one gesture: move to Freezer / Fridge / Pantry, or Ask the owner
3. Move several at once: Select mode, or drag to collect a stack
4. Haptics (Android vibration; best-effort ticks on iPhone)
5. Stocking animation when you tap Send (~500 ms)

## What's inside

| Path | What it is |
| --- | --- |
| `PROMPT.md` | The prompt to paste into Claude Code, plus 3 steps before pasting |
| `docs/features/kitchen-gestures/SPEC.md` | The build contract: behaviour, thresholds, state machine, API, sync, haptics, animation, accessibility, tests |
| `docs/design/gestures/*.dc.html` | Design sources (same format as `docs/design/`). `Gestures.dc.html` renders every state via its `scene` prop; `SortLive.dc.html` is the working animation |
| `docs/design/renders/gestures/*.png` | 20 PNG renders of every state, the haptics spec and the animation storyboard |

## Use it

1. Unzip into the repo root.
2. Follow `PROMPT.md`.
