/**
 * What the API client tells the app shell (PRD §20.1, §21.6), as window events so the client stays
 * free of React:
 *
 *   kt:stale-client   a 426 STALE_CLIENT: show the refresh prompt (PWA-09, SYNC-22)
 *   kt:read           a GET answered; `stale` when the service worker served its cached copy
 *                     after the 3 s network timeout (PWA-03)
 *   kt:write          a write finished OK (`method`, `path`): moments like "first bill sent"
 *   kt:writes         the number of writes in flight changed (the update toast waits for 0)
 *   kt:unauthorized   a request was answered 401: the session is gone (AUTH-09)
 */

export const STALE_HEADER = "x-kt-stale";

let inFlight = 0;

function fire(name: string, detail: unknown): void {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") return;
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

export function writesInFlight(): number {
  return inFlight;
}

export function writeStarted(): void {
  inFlight += 1;
  fire("kt:writes", inFlight);
}

export function writeEnded(method: string, path: string, ok: boolean): void {
  inFlight = Math.max(0, inFlight - 1);
  fire("kt:writes", inFlight);
  if (ok) fire("kt:write", { method, path });
}

export function readAnswered(path: string, res: Response): void {
  fire("kt:read", { path, stale: res.headers.get(STALE_HEADER) === "1" });
}

export function unauthorized(path: string): void {
  fire("kt:unauthorized", { path });
}

export function staleClient(): void {
  fire("kt:stale-client", null);
}

export type WriteDetail = { method: string; path: string };
export type ReadDetail = { path: string; stale: boolean };
