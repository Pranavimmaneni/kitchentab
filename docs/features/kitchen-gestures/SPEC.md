# Kitchen gestures, haptics and the stocking animation — feature spec

Status: approved design, ready to build. Applies to the Fridge (`/kitchen/fridge`), Pantry
(`/kitchen/pantry`) and Review & send screens. Extends PRD §19 (fridge and pantry), §20 (PWA) and
§21 (sync). Where this spec and the PRD disagree, this spec wins for these features only.

Designs: `docs/design/gestures/` (sources) and `docs/design/renders/gestures/` (PNGs, light; two dark).
The mockups use literal hex values; implement with the tokens in `apps/web/styles/tokens.css`.

| Render | What it shows |
| --- | --- |
| `r1` `r2` `r3` | Reorder: hold (ring fills), dragging (neighbours slide aside), dropped |
| `m1` `m2` `m3` `m4` | Quick actions: bubbles fan out, slide onto Pantry, moved + Undo, slide onto Ask |
| `s1` `s2` `s3` | Select mode: picking, 3 picked + tap Pantry, moved together |
| `c1` `c2` `c3` `c4` | Drag to collect: pick up, rest to collect, over Pantry bin, moved |
| `m2-dark` `c3-dark` | Dark theme reference |
| `haptics-spec` | Every haptic moment, feel and pattern |
| `sort-before` `sort-after` `sort-storyboard` | Stocking animation: before, after, keyframes and timing |

`docs/design/gestures/SortLive.dc.html` is a working prototype of the animation (open the canvas
and tap Send). Read `docs/design/README.md` for the `.dc.html` format.

---

## 1. Requirements (new FR IDs)

| ID | Requirement |
| --- | --- |
| FR-G01 | Long-press (350 ms) on an item in Fridge or Pantry lifts it: scale 1.15, rotate −4°, hard offset shadow (tokens: `--shadow-hard`). |
| FR-G02 | Dragging a lifted item within its own section reorders it; neighbours animate aside; the order is saved **per member**. |
| FR-G03 | Lifting also opens the quick-action fan (3 bubbles). One continuous gesture: slide onto a bubble and release to choose it; release elsewhere cancels. |
| FR-G04 | Quick actions are always the two locations the item is not in (Freezer / Fridge / Pantry) plus a third bubble: **Ask [owner]** for items you don't own and that aren't open, **Used up** for items you own, **Help yourself** (toast only) for items open to share. |
| FR-G05 | Select mode: header **Select** button; tap toggles items; bottom bar shows count + move buttons; moving applies to all picked items. |
| FR-G06 | Drag to collect: while dragging, resting 250 ms on another item adds it to a stack under the finger with a count badge; dropping the stack on a bin moves them all. |
| FR-G07 | While any drag is active a drop bar replaces the bottom bar with three bins: Freezer, Fridge, Pantry. The bin for the dragged items' current location shows **HERE** and refuses drops (only when every dragged item is already there). |
| FR-G08 | Every move shows a toast with **Undo** for 5 s. |
| FR-G09 | Haptics per §6, through one `haptic(event)` helper, with a Settings › Kitchen › Haptics switch (default on; hidden when the device can't do it). |
| FR-G10 | Stocking animation on Send per §7: ≤ 520 ms, non-blocking, reduced-motion variant. |
| FR-G11 | Every gesture action has a no-gesture path (§8). |

## 2. Gesture engine

Build one small engine, `apps/web/features/kitchen/gestures/`, as a **pure reducer + a thin pointer
hook**, so the logic is unit-testable without a browser. No new dependency (do not add dnd-kit or a
haptics library).

### Constants (one file, `constants.ts`)

| Name | Value | Meaning |
| --- | --- | --- |
| `LONG_PRESS_MS` | 350 | Hold time before lift |
| `PRE_LIFT_SLOP_PX` | 8 | Movement before lift that cancels the press (it's a scroll) |
| `DECIDE_PX` | 12 | Travel after lift that decides menu vs drag |
| `MENU_RADIUS_PX` | 84 | Bubble distance from the item centre |
| `MENU_FAN_DEG` | 120 | Fan width; bubbles at −150°, −90°, −30° (0° = right) |
| `MENU_FOLD_MS` | 120 | Bubbles fold away when drag wins |
| `COLLECT_DWELL_MS` | 250 | Rest time over an item to collect it |
| `SHIFT_MS` | 180 | Neighbour slide, ease-out `cubic-bezier(.2,.8,.2,1)` |
| `RETURN_MS` | 180 | Items glide home on cancel |
| `AUTOSCROLL_EDGE_PX` | 48 | Auto-scroll zone at top/bottom |
| `STACK_MAX` | 20 | Max items in a stack |
| `UNDO_MS` | 5000 | Undo window |

### States and transitions

```
IDLE
 └─ pointerdown on item ──► PRESSING (start 350 ms timer, show charge ring after 120 ms)
PRESSING
 ├─ move > 8 px ──► IDLE (let the page scroll)
 ├─ pointerup / cancel ──► IDLE (it was a tap: open the item sheet)
 └─ timer fires ──► LIFTED (haptic: lift; open the fan; select mode: no fan)
LIFTED
 ├─ pointerup with < DECIDE_PX travel ──► MENU_STATIC (bubbles stay as normal buttons)
 ├─ travel ≥ DECIDE_PX inside the fan's 120° cone ──► MENU_TRACKING
 └─ travel ≥ DECIDE_PX outside the cone ──► DRAGGING (fold the fan)
MENU_TRACKING
 ├─ finger enters a bubble ──► highlight it (haptic: tick)
 ├─ pointerup on a bubble ──► run its action ──► IDLE
 └─ pointerup elsewhere / cancel ──► IDLE (no action)
MENU_STATIC
 ├─ tap a bubble ──► run its action ──► IDLE
 └─ tap outside / Escape / scroll ──► IDLE
DRAGGING (reorder mode: neighbours shift, gap follows the finger)
 ├─ rest 250 ms on another item ──► COLLECTING (add it; haptic: collect; freeze the layout)
 ├─ over a bin ──► highlight (haptic: tick)
 ├─ pointerup in the list ──► commit new order ──► IDLE
 ├─ pointerup on a bin ──► move ──► IDLE
 └─ cancel (2nd finger, pointercancel, Escape) ──► glide home ──► IDLE
COLLECTING (stack under the finger, list frozen, picked slots show dashed ghosts)
 ├─ rest 250 ms on another item ──► add (until STACK_MAX)
 ├─ pointerup on a bin ──► move all ──► IDLE
 └─ pointerup anywhere else / cancel ──► items fly home ──► IDLE
```

- In **select mode**, a long-press on a picked item goes straight to COLLECTING with every picked
  item in the stack; on an unpicked item it behaves as DRAGGING. The fan never opens in select mode.
- The cone test uses the angle from the lifted item's centre to the finger. Near the top of the
  viewport (item centre < 180 px from the top) the fan flips below the finger (+30°, +90°, +150°);
  within 60 px of a side edge the fan rotates 30° inward. Bubbles must stay fully on screen.
- Dragging an item onto the **Freezer area** of the fridge (or the Refrigerated area from the
  freezer) is a move, not a reorder: that section's label changes to `DROP TO MOVE TO FREEZER`
  while hovered.
- The lifted item and the stack ride **28 px above the finger** so the finger never hides them.

### Browser plumbing (the parts that usually go wrong)

1. Use Pointer Events with `setPointerCapture` on the pressed item.
2. The list keeps native vertical scrolling (`touch-action: pan-y`) until the lift. From LIFTED
   onward, a **non-passive `touchmove` listener** on the screen container calls `preventDefault()`
   so the page doesn't scroll under the drag. Remove it on IDLE.
3. Items: `-webkit-touch-callout: none; user-select: none; -webkit-user-select: none`, and
   `contextmenu` → `preventDefault()` on touch (keep right-click on desktop: it opens MENU_STATIC).
4. Items must be `<button>`s, not links, so iOS doesn't show a link preview on long-press.
5. `pointercancel`, `visibilitychange` (hidden), a second pointer and `Escape` all cancel safely.
6. Animate only `transform` and `opacity`; no layout reads during the drag except a cached grid of
   slot rects taken at lift time (recompute after auto-scroll).

## 3. Quick-action menu details

- Bubbles: 52 px, lime fill and 64 px when hovered, with a lime label chip above the hovered one
  (`MOVE TO PANTRY`, `ASK MEERA FOR SOME`). Each bubble has a small text label under it at all times.
- The background dims (scrim 45% black) while the fan is open; only the lifted item and the bubbles
  sit above it.
- MENU_STATIC renders the bubbles as `role="menu"` with `role="menuitem"` buttons, focus on the
  first, arrow keys move, Escape closes.
- Actions:
  - **Move to X** → the move endpoint (§5) for one item.
  - **Ask [owner]** → the existing ask-to-share create endpoint with amount `a_bit`; the toast says
    `Asked Meera for some yogurt` with Undo (Undo cancels the ask).
  - **Used up** → the existing used-up transition, toast with Undo.
  - **Help yourself** → toast only (`Open to share — help yourself`), no request.

## 4. Select mode details

- Header gets a `SELECT` pill (44 px) left of the theme toggle; in select mode it reads `DONE`
  (ink fill). Back, Done and Escape leave select mode and clear the selection.
- Each item shows a 20 px check circle at its top-left; picked items get a lime check and a 3 px
  violet ring. The legend row becomes the hint *"Tap items to pick them. Hold a picked item to drag
  them all at once."*
- The bottom bar becomes: count (`3` / `SELECTED`) + one button per destination. A destination is
  hidden only when every picked item is already there. In the Fridge with only refrigerated
  items picked, that's `Freezer` and `Pantry` (Pantry is the primary, violet).
- Mixed picks: items already at the destination are skipped; toast `2 moved · 1 already there`.
- Filters still work in select mode; picked items hidden by a filter stay picked (count includes
  them).

## 5. Data, API and sync

Follow CLAUDE.md rules: one transaction per write ending with the house event sequence, the four
write kinds, the outbox. Use the existing inventory storage field (freezer / fridge / pantry) from
PRD §19; if it's named differently in the code, use the existing name.

### 5.1 Personal order (set-state PUT)

- New table `member_item_order (house_id, member_id, location, item_ids uuid[] , updated_at)`,
  PK `(house_id, member_id, location)`; `location` ∈ `freezer | fridge | pantry` (the fridge
  screen has two lists: `freezer` and `fridge`).
- `PUT /houses/{house_id}/me/item-order/{location}` body `{ "item_ids": [...] }`. Set-state: the
  client sends the full list; optimistic UI allowed. It writes a `house_events` row visible only to
  that member's other devices (event `member.item_order.set`, audience = actor).
- Reading: items in the saved order first, then items not in the list (newest first), skipping ids
  that no longer exist. Never error on stale ids.

### 5.2 Moving items (state transition)

- `POST /houses/{house_id}/items/move` with `Idempotency-Key`. Body
  `{ "moves": [{ "item_id": "...", "to": "pantry", "expect_from": "fridge" }] }` (1–20 moves).
- One transaction: lock the item rows (`FOR UPDATE`, ordered by id), then for each move:
  `moved` if found and not already there; `skipped_already_there`; `skipped_gone` (used up or
  deleted). Undo sends the inverse moves with `expect_from` = the destination; if an item's
  location no longer equals `expect_from` (someone moved it since), that one is
  `skipped_changed`. Response lists a result per item.
- Policy (in `policy.py`): any current house member may move any active item (it's physical
  location, not ownership). Members who left can't.
- One `house_events` row `items.moved` with the list; all clients update from it. No push
  notification for moves (they appear in the item's history line, e.g. `Moved to pantry by Arjun`).
- Rate limit: 60 move requests per member per minute.

### 5.3 Concurrency rules

| ID | Case | Expected |
| --- | --- | --- |
| SYNC-G1 | Two members move the same item to different places at the same moment | Both requests succeed in lock order; the final location is the later one; both clients converge from events. |
| SYNC-G2 | Member A is mid-drag with item X in a stack; B uses X up | X leaves A's stack with an inline note `Yogurt was used up`; the drop moves the rest. |
| SYNC-G3 | A drops a stack; the response is lost; A retries | Same Idempotency-Key → same result, one event. |
| SYNC-G4 | A taps Undo after B already moved the item again | That item is `skipped_changed`; toast `1 item changed since — not undone`. |
| SYNC-G5 | A reorders on phone and laptop at once | Last PUT wins per location; both devices converge. |
| SYNC-G6 | Items arrive from a new bill while A is dragging | The list updates around the gap; the dragged item stays in hand; new items appear at the end of A's order. |

## 6. Haptics

One module `apps/web/lib/haptics.ts` exporting `haptic(event)` with the events below.

| Event | When | Android `navigator.vibrate` | iPhone |
| --- | --- | --- | --- |
| `lift` | Hold reaches 350 ms | `18` | 1 tick |
| `tick` | Finger enters a bubble or a bin; select toggle; reorder slot change (max 1 per 80 ms) | `6` (`5` for reorder) | 1 tick |
| `collect` | An item joins the stack | `10` | 1 tick |
| `success` | Move / ask / used-up committed; stocking animation done | `[12, 60, 24]` | 2 ticks 60 ms apart |
| `blocked` | Drop on HERE, or an action that isn't allowed | `[24, 40, 24]` | 2 quick ticks |
| — | Cancel, scroll, other people's live changes | none | none |

- **iPhone:** Safari has no Vibration API. Implement the known workaround inside `haptics.ts`: a
  hidden `<input type="checkbox" switch>` with a `<label>`; calling `label.click()` during a user
  gesture produces one system tick. Patterns are repeated ticks. Treat it as best effort and keep
  the code isolated so it can be removed if Safari changes.
- Feature detection: Android path if `'vibrate' in navigator`; iPhone path if the `switch`
  attribute is supported (`'switch' in HTMLInputElement.prototype` or a UA check for iOS Safari
  17.4+); otherwise no-op and hide the setting.
- Global rules: at least 60 ms between haptics; only inside the user's own gesture; never the only
  feedback (each event has a visual twin, see the haptics render); respect the setting.

## 7. Stocking animation (Review & send → Send)

- Trigger: tapping **Send** on Review & send. It runs **while the send request is in flight**; the
  navigation to the Share screen happens when both the request and the animation are done.
- Layout: three bins (Freezer, Fridge, Pantry) under the item list, as in `sort-before.png`.
- Motion (see `sort-storyboard.png`):
  - 0–60 ms **lift**: each row icon scales to 1.15 and rises 5 px,
    `cubic-bezier(.2,1.4,.4,1)`; rows dim to 40% over 120 ms.
  - From 60 ms **fly**: item *i* starts at `60 + 20·i` ms and takes 220 ms. X uses
    `cubic-bezier(.25,.1,.35,1)`, Y uses `cubic-bezier(.55,0,.85,.4)` (two nested elements, so the
    path arcs). Scale goes to 0.62, small ±12° rotation, landing spread ±6 px per item.
  - **Land**: each bin squashes `scale(1.08, .92)` for 100 ms on its first catch; its count badge
    (`+2`) slides in over 90 ms; icons fade out 60 ms after landing.
  - `success` haptic once, when the last item lands (~420 ms with 8 items).
  - More than 8 items: stagger = `min(20, 140 / (n − 1))` ms so the total stays ≤ ~520 ms.
- Failure: if the send fails, icons fly back in 200 ms, bins reset, error toast; nothing was stocked.
- `prefers-reduced-motion: reduce`: no flight; rows dim, counts fade in over 120 ms, haptic still fires.
- Transform/opacity only; must hold 60 fps on a mid-range Android (check with the Performance panel
  at 4× CPU throttle).

## 8. Accessibility and no-gesture paths

- Tap an item → item sheet (existing) gains: **Move to** (segmented: the two other locations),
  **Ask** / **Used up** (as today), and **Move earlier / Move later** for personal order.
- Select mode (§4) is the full no-gesture path for moving several items.
- Desktop: right-click or `Shift+F10` on a focused item opens MENU_STATIC as a real menu.
- Keyboard in select mode: `Space` toggles the focused item, `Escape` exits.
- Screen readers: items announce name, owners, sharing state, location and (in select mode)
  `selected / not selected`; the select bar is a labelled toolbar announcing `N selected`; moves
  announce via a polite live region (`Yogurt moved to pantry. Undo available.`).
- Targets ≥ 44 px (the bubbles are 52 px); WCAG 2.2 AA contrast in both themes; drag is never the
  only way to do anything (WCAG 2.5.7).

## 9. Tests (add to `docs/TEST_PLAN.md`, keep IDs in test names)

| ID | Test |
| --- | --- |
| GEST-01 | Reducer: press → 350 ms → LIFTED; move 9 px before 350 ms → IDLE; release before 350 ms → tap |
| GEST-02 | Reducer: after lift, 12 px into the cone → MENU_TRACKING; outside → DRAGGING; < 12 px release → MENU_STATIC |
| GEST-03 | Reducer: fan flips below near the top edge and rotates near the sides; bubbles stay on screen at 320 px width |
| GEST-04 | Reducer: dwell 250 ms collects; 200 ms doesn't; STACK_MAX enforced; collect freezes reorder |
| GEST-05 | Reducer: cancel paths (2nd pointer, pointercancel, Escape, visibility hidden) return to IDLE with no action |
| GEST-06 | E2E (touch emulation via CDP, WebKit iPhone + Chromium Pixel): reorder eggs to slot 2; reload; order persists for that member only |
| GEST-07 | E2E: hold yogurt, slide to Pantry, release → item in Pantry, toast, Undo restores |
| GEST-08 | E2E: hold, slide to Ask Meera → ask created with amount a_bit; Meera sees it; Undo cancels it |
| GEST-09 | E2E: own item shows Used up; open item shows Help yourself (no request created) |
| GEST-10 | E2E: select 3, Move to Pantry → one request, three moves, one event; mixed picks skip correctly |
| GEST-11 | E2E: drag-to-collect 3 items onto Pantry; drop on HERE is refused; drop in the list cancels |
| GEST-12 | E2E: page doesn't scroll during a drag; it does scroll when the finger moves before 350 ms |
| GEST-13 | E2E: no text selection, callout or context menu appears on long-press (iPhone profile) |
| GEST-14 | API: move endpoint results `moved / skipped_already_there / skipped_gone / skipped_changed`; idempotent retry; 21 moves rejected; ex-member forbidden |
| GEST-15 | API: item-order PUT is per member and per location; stale ids ignored on read |
| SYNC-G1..G6 | §5.3 cases, 200 iterations each in the race harness, both `PROFILE`s |
| HAPT-01 | `haptic()` maps every event to the §6 pattern on a stubbed `navigator.vibrate`; 60 ms throttle; off when the setting is off |
| HAPT-02 | iPhone path clicks the hidden switch label N times for a pattern; no-op when unsupported; setting hidden when unsupported |
| HAPT-03 | No haptic fires for remote events or on cancel |
| ANIM-01 | Stocking animation finishes ≤ 520 ms for 8 items and ≤ 560 ms for 30 (stagger compresses) |
| ANIM-02 | Navigation waits for both the request and the animation; failure flies icons back and stocks nothing |
| ANIM-03 | Reduced motion: no transforms, counts fade, haptic once |
| A11Y-G1 | axe clean in select mode, MENU_STATIC and the drop bar, both themes; keyboard-only user can move, ask and reorder via the item sheet |
| VIS-G1 | Visual diffs vs `docs/design/renders/gestures/*.png` for states r2, m2, s2, c3 at 390 px (allow token-level color differences) |

## 10. Out of scope

Reordering in the Kitchen home panels; shared (house-wide) order; a shopping list; dragging between
houses; haptics on desktop.
