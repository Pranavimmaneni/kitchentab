/** Kitchen gestures (docs/features/kitchen-gestures/SPEC.md §2): every threshold in one place. */

/** Hold time before an item lifts. */
export const LONG_PRESS_MS = 350;
/** The charge ring appears after this much of the hold. */
export const CHARGE_RING_MS = 120;
/** Movement before the lift that cancels the press: it's a scroll. */
export const PRE_LIFT_SLOP_PX = 8;
/** Travel after the lift that decides menu vs drag. */
export const DECIDE_PX = 12;
/** Bubble distance from the item centre. */
export const MENU_RADIUS_PX = 84;
/** Fan width; bubbles at −150°, −90°, −30° (0° = right, y grows downward). */
export const MENU_FAN_DEG = 120;
/** Bubble diameter (resting) and when hovered. */
export const BUBBLE_PX = 52;
export const BUBBLE_HOVER_PX = 64;
/** Bubbles fold away when drag wins. */
export const MENU_FOLD_MS = 120;
/** Rest time over an item to collect it. */
export const COLLECT_DWELL_MS = 250;
/** Neighbour slide and the glide home on cancel. */
export const SHIFT_MS = 180;
export const RETURN_MS = 180;
export const SHIFT_EASE = "cubic-bezier(.2,.8,.2,1)";
/** Auto-scroll zone at the top and bottom of the viewport. */
export const AUTOSCROLL_EDGE_PX = 48;
/** Most items in one stack. */
export const STACK_MAX = 20;
/** Undo window for moves. */
export const UNDO_MS = 5000;
/** The lifted item and the stack ride this far above the finger. */
export const RIDE_ABOVE_PX = 28;
/** Fan placement near the edges (§2 "cone test"). */
export const FLIP_BELOW_TOP_PX = 180;
export const SIDE_EDGE_PX = 60;
export const SIDE_ROTATE_DEG = 30;
