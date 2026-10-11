# ADR-0033: Kitchen gestures: hit zones, select-mode cap and corner fans

Status: accepted · 2026-10-11 · docs/features/kitchen-gestures/SPEC.md §2, §4 · TEST_PLAN GEST-03, GEST-04

## Context

The gestures spec has reorder (neighbours slide aside, the gap follows the finger) and collect
(rest 250 ms on another item) running in the same drag. If the gap simply followed the cell under
the finger, the finger would always sit over the gap, and no item would ever stay under it long
enough to collect. The spec also asks for the fan to stay fully on screen at 320 px, and leaves the
select-mode limit open.

## Decision

- **Hit zones.** While dragging, each visible item's cell has a leading edge, a centre and a
  trailing edge (the pointer hook measures them from the cell rects cached at lift, mapped through
  the current order). An edge places the gap before or after that item; the centre does nothing to
  the order and starts the collect dwell. The centre is also the hysteresis band that stops the gap
  from flickering when the finger sits on a boundary.
- **Collecting freezes the layout.** The first collected item sends the gap home: the original
  order shows again, with dashed ghosts in the cells of the items in hand, and a release in the list
  sends the stack home without saving an order (as the spec's state table says).
- **A stack lands only in a bin.** A single dragged item may also land on the other section of the
  fridge (Freezer or Refrigerated), which is a move, as the spec says; a collected stack follows the
  COLLECTING row of the state table and only moves when dropped in a bin.
- **Select mode picks at most `STACK_MAX` (20) items.** A 21st tap shows a toast. One move is then
  always one request and one event (GEST-10), and the stack you lift is everything you picked.
- **Corner fans.** The fan rotates inward near the sides as the spec says. If that still doesn't
  fit (an item within ~40 px of a screen corner, which a tile can't reach given the 16 px gutter and
  the bottom bar), the fan points where it overflows least, tries a 12 px and a 24 px longer reach,
  then clamps the bubbles on screen. GEST-03 checks on-screen placement over the whole screen, and
  that bubbles never overlap over every position a tile can occupy.
- **Personal order with a filter on.** Only the visible items can be reordered; the hidden ones keep
  their positions in the saved list (`mergeOrder`).

## Consequences

- The reducer stays pure: the hook does the measuring, and `reduce()` gets one hit per pointer
  event. Every rule above is a unit test in `apps/web/tests/unit/gestures.test.ts`.
- Reorder needs the finger past an item's middle third to move the gap, which feels a touch firmer
  than iOS home-screen reordering. The edge/centre split can be tuned in the hook alone.
