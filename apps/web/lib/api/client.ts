/**
 * Typed fetch wrapper for `/api/v1` (PRD §11, §21.3, §21.6).
 *
 * - Every unsafe request sends `X-CSRF-Token` equal to the `kt_csrf` cookie (double submit,
 *   ADR-0006); the cookie is fetched from `GET /auth/csrf` when missing.
 * - The four write kinds: `put` (set-state, no If-Match), `patch` (guarded, `If-Match: version`),
 *   `post` (creates and transitions: `Idempotency-Key` + `X-Client-Mutation-Id`), `del`.
 * - A write that times out (or never reaches the server) is retried up to 3 times with the SAME
 *   Idempotency-Key and mutation id, so a lost response returns the original result (SYNC-16).
 *   Guarded edits are not retried: a replay would only conflict with itself.
 * - Errors are RFC 9457 problem details mapped to `ApiError`; show `title` to people.
 */

export const API_PREFIX = "/api/v1";
export const CSRF_COOKIE = "kt_csrf";
export const CLIENT_VERSION = "web/0.1.0";

export type ProblemBody = {
  type?: string;
  title?: string;
  status?: number;
  code?: string;
  detail?: string;
  request_id?: string;
  [key: string]: unknown;
};

/** A failed request. `status` 0 means the server never answered (offline, timeout). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly title: string;
  readonly detail?: string;
  readonly body: ProblemBody;
  readonly retryAfterSeconds?: number;
  readonly requestId?: string;

  constructor(status: number, body: ProblemBody, retryAfterSeconds?: number) {
    const title = typeof body.title === "string" && body.title ? body.title : "Something jammed. Try again.";
    super(title);
    this.name = "ApiError";
    this.status = status;
    this.code = typeof body.code === "string" ? body.code : status === 0 ? "NETWORK" : "UNKNOWN";
    this.title = title;
    this.detail = typeof body.detail === "string" ? body.detail : undefined;
    this.body = body;
    this.retryAfterSeconds = retryAfterSeconds;
    this.requestId = typeof body.request_id === "string" ? body.request_id : undefined;
  }

  /** True when retrying the same request may succeed (no answer from the server). */
  get transient(): boolean {
    return this.status === 0;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function newId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // RFC 4122 v4 from getRandomValues (older WebKit without randomUUID).
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function readCookie(name: string, cookieString?: string): string | undefined {
  const source = cookieString ?? (typeof document === "undefined" ? "" : document.cookie);
  for (const part of source.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return undefined;
}

export type ApiClientConfig = {
  /** Origin prefix; "" (same origin) in the browser. */
  baseUrl?: string;
  fetch?: typeof fetch;
  /** Per-attempt timeout in ms (default 10 s). */
  timeoutMs?: number;
  /** Retries after a timed-out or failed attempt (default 3, PRD §21.6). */
  maxRetries?: number;
  /** Wait before retry `attempt` (1-based). */
  retryDelayMs?: (attempt: number) => number;
  cookie?: (name: string) => string | undefined;
  /** Id generator for Idempotency-Key / X-Client-Mutation-Id. */
  newId?: () => string;
};

export type RequestOptions = {
  body?: unknown;
  /** Guarded edits: the row version (sent as If-Match). */
  ifMatch?: number;
  /** Creates and transitions: one key per user action, reused on retry. */
  idempotencyKey?: string;
  mutationId?: string;
  /** Override the retry count (0 for sign-in calls that send messages). */
  retries?: number;
  signal?: AbortSignal;
  headers?: Record<string, string>;
};

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

const TIMEOUT = Symbol("timeout");

export type ApiClient = ReturnType<typeof createApiClient>;

export function createApiClient(config: ApiClientConfig = {}) {
  const baseUrl = config.baseUrl ?? "";
  const doFetch = config.fetch ?? ((input, init) => globalThis.fetch(input, init));
  const timeoutMs = config.timeoutMs ?? 10_000;
  const maxRetries = config.maxRetries ?? 3;
  const retryDelay = config.retryDelayMs ?? ((attempt: number) => 250 * 2 ** (attempt - 1));
  const cookie = config.cookie ?? ((name: string) => readCookie(name));
  const makeId = config.newId ?? newId;
  let csrfPromise: Promise<string> | null = null;

  async function csrfToken(force = false): Promise<string> {
    const existing = cookie(CSRF_COOKIE);
    if (existing && !force) return existing;
    csrfPromise ??= (async () => {
      try {
        const res = await doFetch(`${baseUrl}${API_PREFIX}/auth/csrf`, {
          credentials: "same-origin",
          headers: { Accept: "application/json", "X-Client-Version": CLIENT_VERSION },
        });
        const data = (await res.json()) as { token: string };
        return cookie(CSRF_COOKIE) ?? data.token;
      } finally {
        csrfPromise = null;
      }
    })();
    return csrfPromise;
  }

  async function attempt(
    method: Method,
    url: string,
    headers: Record<string, string>,
    body: string | undefined,
    signal: AbortSignal | undefined,
  ): Promise<Response> {
    const controller = new AbortController();
    const onAbort = () => controller.abort(signal?.reason);
    signal?.addEventListener("abort", onAbort);
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort(TIMEOUT);
    }, timeoutMs);
    try {
      return await doFetch(url, {
        method,
        headers,
        body,
        credentials: "same-origin",
        signal: controller.signal,
      });
    } catch (error) {
      if (signal?.aborted && !timedOut) throw error;
      throw new ApiError(0, {
        code: timedOut ? "TIMEOUT" : "NETWORK",
        title: "Couldn't save",
      });
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
  }

  async function toError(res: Response): Promise<ApiError> {
    let body: ProblemBody = {};
    try {
      body = (await res.json()) as ProblemBody;
    } catch {
      body = {};
    }
    const retryAfter = res.headers.get("Retry-After");
    const seconds = retryAfter && /^\d+$/.test(retryAfter) ? Number(retryAfter) : undefined;
    return new ApiError(res.status, { status: res.status, ...body }, seconds);
  }

  async function request<T>(method: Method, path: string, options: RequestOptions = {}): Promise<T> {
    const url = path.startsWith("http") ? path : `${baseUrl}${path.startsWith("/api/") ? "" : API_PREFIX}${path}`;
    const unsafe = method !== "GET";
    const headers: Record<string, string> = {
      Accept: "application/json, application/problem+json",
      "X-Client-Version": CLIENT_VERSION,
      ...options.headers,
    };
    const body = options.body === undefined ? undefined : JSON.stringify(options.body);
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (unsafe) {
      headers["X-Client-Mutation-Id"] = options.mutationId ?? makeId();
      if (method === "POST") headers["Idempotency-Key"] = options.idempotencyKey ?? makeId();
      if (options.ifMatch !== undefined) headers["If-Match"] = `"${options.ifMatch}"`;
    }
    // Reads retry through TanStack Query; guarded edits never retry (a replay conflicts).
    const retries =
      options.retries ?? (unsafe && options.ifMatch === undefined ? maxRetries : 0);
    let csrfRefreshed = false;

    for (let n = 0; ; n++) {
      if (unsafe) headers["X-CSRF-Token"] = await csrfToken();
      let res: Response;
      try {
        res = await attempt(method, url, headers, body, options.signal);
      } catch (error) {
        if (isApiError(error) && error.transient && n < retries) {
          await sleep(retryDelay(n + 1));
          continue;
        }
        throw error;
      }
      if (res.ok) {
        if (res.status === 204) return undefined as T;
        const text = await res.text();
        return (text ? JSON.parse(text) : undefined) as T;
      }
      const error = await toError(res);
      if (unsafe && res.status === 403 && error.code === "FORBIDDEN" && !csrfRefreshed) {
        // Stale double-submit token (e.g. signed in on another tab): refresh once and resend.
        const before = headers["X-CSRF-Token"];
        const fresh = await csrfToken(true);
        csrfRefreshed = true;
        if (fresh && fresh !== before) {
          n--;
          continue;
        }
      }
      throw error;
    }
  }

  return {
    csrfToken,
    request,
    get: <T>(path: string, options?: Pick<RequestOptions, "signal" | "headers">) =>
      request<T>("GET", path, options),
    /** Set-state write (PRD §21.3): the desired value, never a toggle; no If-Match. */
    put: <T>(path: string, body: unknown, options?: Omit<RequestOptions, "body" | "ifMatch">) =>
      request<T>("PUT", path, { ...options, body }),
    /** Guarded edit: `If-Match` with the row version; 409 VERSION_CONFLICT carries `current`. */
    patch: <T>(
      path: string,
      body: unknown,
      version: number,
      options?: Omit<RequestOptions, "body" | "ifMatch">,
    ) => request<T>("PATCH", path, { ...options, body, ifMatch: version }),
    /** Create or state transition: `Idempotency-Key` (pass one per user action) + mutation id. */
    post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "body">) =>
      request<T>("POST", path, { ...options, body }),
    del: <T>(path: string, options?: Omit<RequestOptions, "body">) =>
      request<T>("DELETE", path, options),
  };
}

function sleep(ms: number): Promise<void> {
  return ms > 0 ? new Promise((resolve) => setTimeout(resolve, ms)) : Promise.resolve();
}

/** The browser client (same origin; the Next rewrite or Vercel routes /api to FastAPI). */
export const api = createApiClient();
