/**
 * Who last changed an aggregate, from the event stream. A 409 VERSION_CONFLICT carries the
 * current row but not who changed it; the stream already told us, so the conflict prompt can
 * say "Arjun just changed this" (PRD §21.3).
 */
const lastActor = new Map<string, string>();
const MAX = 500;

export function recordActor(aggregateId: string, memberId: string | null): void {
  if (!memberId) return;
  lastActor.delete(aggregateId);
  lastActor.set(aggregateId, memberId);
  if (lastActor.size > MAX) {
    const oldest = lastActor.keys().next().value;
    if (oldest !== undefined) lastActor.delete(oldest);
  }
}

export function lastActorOf(aggregateId: string): string | undefined {
  return lastActor.get(aggregateId);
}

/** Tests only. */
export function forgetActors(): void {
  lastActor.clear();
}
