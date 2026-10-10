/**
 * Network-first with a 3 s timeout and a stale fallback (PRD §20.1, PWA-03). Written against small
 * interfaces (not the service-worker globals) so it is unit-tested with fakes.
 *
 * - The network answers within the timeout: that answer is used and, if storable, saved.
 * - It is slower: the saved copy is used now, marked `x-kt-stale: 1`, and the network keeps going
 *   in the background to refresh the copy. No copy yet: wait for the network.
 * - It fails (offline): the saved copy, marked stale; else the fallback (offline page); else an
 *   error, which the app treats as "no connection".
 */
import { CACHED_AT_HEADER, NETWORK_TIMEOUT_MS, STALE_HEADER, storable } from "./sw-rules";

export type CacheLike = {
  match(request: Request): Promise<Response | undefined>;
  put(request: Request, response: Response): Promise<void>;
};

export type NetworkFirstOptions = {
  request: Request;
  cache: CacheLike;
  fetch: (request: Request) => Promise<Response>;
  kind: "api" | "page";
  timeoutMs?: number;
  /** Keeps the background refresh alive (`event.waitUntil` in the worker). */
  waitUntil?: (promise: Promise<unknown>) => void;
  /** Used when there is neither network nor a saved copy (navigations: the offline page). */
  fallback?: () => Promise<Response | undefined>;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
};

function withHeaders(res: Response, extra: Record<string, string>): Response {
  const headers = new Headers(res.headers);
  for (const [k, v] of Object.entries(extra)) headers.set(k, v);
  return new Response(res.body, { status: res.status, statusText: res.statusText, headers });
}

export function markStale(res: Response): Response {
  return withHeaders(res, { [STALE_HEADER]: "1" });
}

const TIMEOUT = Symbol("timeout");

export async function networkFirst(o: NetworkFirstOptions): Promise<Response> {
  const timeoutMs = o.timeoutMs ?? NETWORK_TIMEOUT_MS;
  const now = o.now ?? Date.now;
  const setTimer = o.setTimer ?? ((fn: () => void, ms: number) => setTimeout(fn, ms));
  const clearTimer = o.clearTimer ?? ((h: unknown) => clearTimeout(h as ReturnType<typeof setTimeout>));

  const network: Promise<Response> = o.fetch(o.request.clone()).then(async (res) => {
    if (storable(res, o.kind)) {
      try {
        await o.cache.put(o.request, withHeaders(res.clone(), { [CACHED_AT_HEADER]: String(now()) }));
      } catch {
        // quota or a body that can't be cloned: answering matters more than saving
      }
    }
    return res;
  });

  let timer: unknown;
  const timeout = new Promise<typeof TIMEOUT>((resolve) => {
    timer = setTimer(() => resolve(TIMEOUT), timeoutMs);
  });
  const first = await Promise.race([
    network.then(
      (res) => ({ res }),
      (error: unknown) => ({ error }),
    ),
    timeout,
  ]);
  clearTimer(timer);

  const saved = async (): Promise<Response | undefined> => {
    const hit = await o.cache.match(o.request);
    return hit ? markStale(hit) : undefined;
  };

  if (first !== TIMEOUT && "res" in first) return first.res;
  if (first !== TIMEOUT) {
    return (await saved()) ?? (await o.fallback?.()) ?? Response.error();
  }
  // Slow network: answer from the saved copy, keep refreshing it in the background.
  const copy = await saved();
  if (copy) {
    o.waitUntil?.(network.catch(() => undefined));
    return copy;
  }
  try {
    return await network;
  } catch {
    return (await o.fallback?.()) ?? Response.error();
  }
}
