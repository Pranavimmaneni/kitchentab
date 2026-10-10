/**
 * "You're not in this house any more" (SYNC-12, SYNC-33): raised by the stream (`revoked`, or
 * our own member removed), by the polling fallback, or by any write answered `NOT_A_MEMBER`.
 * The sync root listens, stops that house's stream, refreshes `/me` and leaves the screen.
 */
type Listener = (houseId: string) => void;
const listeners = new Set<Listener>();

export function onRevoked(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function emitRevoked(houseId: string): void {
  listeners.forEach((listener) => listener(houseId));
}
