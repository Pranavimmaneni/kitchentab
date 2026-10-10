/**
 * Echo suppression (PRD §21.6): every write this tab sends carries an `X-Client-Mutation-Id`;
 * when the matching event comes back on the stream it is ignored as already applied, so a
 * roommate's own optimistic ticks never flicker (SYNC-20, SYNC-32). Ids live for 10 minutes,
 * far longer than an event takes to arrive (2 s target), and the set is capped.
 */
const TTL_MS = 10 * 60_000;
const MAX_IDS = 2000;
const mine = new Map<string, number>();

function prune(now: number): void {
  for (const [id, at] of mine) {
    if (now - at <= TTL_MS && mine.size <= MAX_IDS) break;
    mine.delete(id);
  }
}

/** Called by the API client for every write it sends (retries reuse the same id). */
export function rememberMutation(id: string, now = Date.now()): void {
  mine.delete(id);
  mine.set(id, now);
  prune(now);
}

/** True when an event was caused by a write from this tab. */
export function isOwnMutation(id: string | null | undefined, now = Date.now()): boolean {
  if (!id) return false;
  const at = mine.get(id);
  return at !== undefined && now - at <= TTL_MS;
}

/** Tests only. */
export function forgetMutations(): void {
  mine.clear();
}
