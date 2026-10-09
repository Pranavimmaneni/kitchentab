import { describe, expect, it, vi } from "vitest";
import { ApiError, createApiClient, readCookie } from "../../lib/api/client";

type Call = { url: string; init: RequestInit };

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json", ...headers },
  });
}

function setup(responses: ((call: Call) => Response | Promise<Response>)[], cookie: Record<string, string> = {}) {
  const calls: Call[] = [];
  const jar = { ...cookie };
  let n = 0;
  const fetchImpl = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const call = { url: String(url), init: init ?? {} };
    calls.push(call);
    const next = responses[Math.min(n++, responses.length - 1)]!;
    return next(call);
  });
  let ids = 0;
  const client = createApiClient({
    fetch: fetchImpl as unknown as typeof fetch,
    cookie: (name) => jar[name],
    newId: () => `id-${++ids}`,
    retryDelayMs: () => 0,
    timeoutMs: 50,
  });
  const header = (i: number, name: string) => new Headers(calls[i]!.init.headers).get(name);
  return { client, calls, jar, header };
}

describe("API client", () => {
  it("creates send the CSRF cookie value, an Idempotency-Key and an X-Client-Mutation-Id", async () => {
    const { client, calls, header } = setup([() => json(201, { id: "b1" })], { kt_csrf: "tok" });
    const out = await client.post<{ id: string }>("/houses/h1/bills", { store_name: "ShopRite" }, { idempotencyKey: "key-1" });
    expect(out).toEqual({ id: "b1" });
    expect(calls[0]!.url).toBe("/api/v1/houses/h1/bills");
    expect(calls[0]!.init.method).toBe("POST");
    expect(header(0, "X-CSRF-Token")).toBe("tok");
    expect(header(0, "Idempotency-Key")).toBe("key-1");
    expect(header(0, "X-Client-Mutation-Id")).toBeTruthy();
    expect(header(0, "Content-Type")).toBe("application/json");
    expect(JSON.parse(String(calls[0]!.init.body))).toEqual({ store_name: "ShopRite" });
  });

  it("fetches /auth/csrf first when the cookie is missing", async () => {
    const { client, calls, header } = setup([() => json(200, { token: "fresh" }), () => json(200, { applied: [] })]);
    await client.put("/bills/b1/allocations", { changes: [] });
    expect(calls[0]!.url).toBe("/api/v1/auth/csrf");
    expect(calls[1]!.init.method).toBe("PUT");
    expect(header(1, "X-CSRF-Token")).toBe("fresh");
    expect(header(1, "Idempotency-Key")).toBeNull();
    expect(header(1, "If-Match")).toBeNull();
  });

  it("guarded edits send If-Match with the row version and are never retried", async () => {
    const { client, calls, header } = setup([() => Promise.reject(new TypeError("offline"))], { kt_csrf: "t" });
    await expect(client.patch("/bills/b1", { tax_cents: 89 }, 7)).rejects.toMatchObject({ status: 0 });
    expect(calls).toHaveLength(1);
    expect(header(0, "If-Match")).toBe('"7"');
    expect(header(0, "Idempotency-Key")).toBeNull();
  });

  it("retries a timed-out create up to 3 times with the same Idempotency-Key and mutation id", async () => {
    const hang = (call: Call) =>
      new Promise<Response>((_, reject) => {
        call.init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    const { client, calls, header } = setup([hang, hang, hang, () => json(200, { id: "b1", state: "open" })], {
      kt_csrf: "t",
    });
    const out = await client.post("/bills/b1/send", undefined, { idempotencyKey: "send-key" });
    expect(out).toEqual({ id: "b1", state: "open" });
    expect(calls).toHaveLength(4);
    const keys = calls.map((_, i) => header(i, "Idempotency-Key"));
    const mutations = calls.map((_, i) => header(i, "X-Client-Mutation-Id"));
    expect(new Set(keys)).toEqual(new Set(["send-key"]));
    expect(new Set(mutations).size).toBe(1);
  });

  it("gives up after 3 retries and reports a transient error", async () => {
    const { client, calls } = setup([() => Promise.reject(new TypeError("offline"))], { kt_csrf: "t" });
    const error = await client.post("/houses", { name: "x" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).transient).toBe(true);
    expect(calls).toHaveLength(4);
  });

  it("maps RFC 9457 problems to ApiError with code, title, extras and Retry-After", async () => {
    const { client } = setup(
      [
        () =>
          json(
            429,
            { title: "Too many tries", code: "RATE_LIMITED", status: 429, request_id: "r-1", tries_left: 2 },
            { "Retry-After": "120" },
          ),
      ],
      { kt_csrf: "t" },
    );
    const error = (await client.post("/auth/otp/send", {}, { retries: 0 }).catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(429);
    expect(error.code).toBe("RATE_LIMITED");
    expect(error.title).toBe("Too many tries");
    expect(error.message).toBe("Too many tries");
    expect(error.retryAfterSeconds).toBe(120);
    expect(error.requestId).toBe("r-1");
    expect(error.body.tries_left).toBe(2);
    expect(error.transient).toBe(false);
  });

  it("HTTP errors are not retried, only lost responses are", async () => {
    const { client, calls } = setup([() => json(409, { code: "VERSION_CONFLICT", title: "Changed" })], { kt_csrf: "t" });
    await expect(client.post("/x", {})).rejects.toMatchObject({ code: "VERSION_CONFLICT" });
    expect(calls).toHaveLength(1);
  });

  it("refreshes a stale CSRF token once on 403 and resends", async () => {
    const { client, calls, jar, header } = setup(
      [
        () => json(403, { code: "FORBIDDEN", title: "Missing or stale security token" }),
        () => {
          jar.kt_csrf = "new";
          return json(200, { token: "new" });
        },
        () => new Response(null, { status: 204 }),
      ],
      { kt_csrf: "old" },
    );
    await expect(client.post("/auth/logout")).resolves.toBeUndefined();
    expect(calls.map((c) => c.url)).toEqual(["/api/v1/auth/logout", "/api/v1/auth/csrf", "/api/v1/auth/logout"]);
    expect(header(2, "X-CSRF-Token")).toBe("new");
  });

  it("reads one cookie out of document.cookie", () => {
    expect(readCookie("kt_csrf", "a=1; kt_csrf=abc%3D; b=2")).toBe("abc=");
    expect(readCookie("missing", "a=1")).toBeUndefined();
  });
});
