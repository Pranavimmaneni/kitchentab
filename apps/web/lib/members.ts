/** Member colors are assigned in join order and cycle after 8 (PRD §14). */
export const MEMBER_COLOR_COUNT = 8;

/** 1-based slot (1..8) for a 0-based join index. */
export function memberSlot(joinIndex: number): number {
  const n = Math.trunc(joinIndex);
  return (((n % MEMBER_COLOR_COUNT) + MEMBER_COLOR_COUNT) % MEMBER_COLOR_COUNT) + 1;
}

export function memberColors(joinIndex: number): { fill: string; ink: string } {
  const slot = memberSlot(joinIndex);
  return { fill: `var(--member-${slot})`, ink: `var(--on-member-${slot})` };
}

export function initialOf(name: string): string {
  return (name.trim()[0] ?? "?").toUpperCase();
}
