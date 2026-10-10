/**
 * Only server time is trusted (PRD §21.6 "Time", CHAOS-08). The API client notes the `Date`
 * header of every answer; the offset between it and this device's clock is applied wherever the
 * app says how long ago something happened ("12 MIN AGO", "STILL OPEN · DAY 3", "tweaked it
 * yesterday"), so a phone whose clock is 10 minutes off still reads right.
 *
 * `Date` has one-second resolution, so a sample is the middle of that second against the middle
 * of the request; listeners only hear about changes larger than the noise.
 */
const NOTIFY_THRESHOLD_MS = 2_000;

let offsetMs = 0;
let known = false;
const listeners = new Set<() => void>();

/** Server time minus device time, in ms (0 until the first answer). */
export function serverOffset(): number {
  return offsetMs;
}

/** Records one sample from a response's `Date` header. */
export function noteServerDate(header: string | null, sentAt: number, receivedAt: number): void {
  if (!header) return;
  const server = Date.parse(header);
  if (Number.isNaN(server)) return;
  const next = server + 500 - (sentAt + receivedAt) / 2;
  const moved = !known || Math.abs(next - offsetMs) > NOTIFY_THRESHOLD_MS;
  offsetMs = next;
  known = true;
  if (moved) listeners.forEach((listener) => listener());
}

/** Now on the server's clock (ms since the epoch). */
export function serverNow(): number {
  return Date.now() + offsetMs;
}

export function onServerOffset(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Tests only. */
export function resetServerOffset(): void {
  offsetMs = 0;
  known = false;
}
