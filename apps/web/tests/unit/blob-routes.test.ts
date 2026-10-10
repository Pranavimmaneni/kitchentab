// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const issued: unknown[] = [];
vi.mock("@vercel/blob", () => ({
  issueSignedToken: vi.fn(async (o: unknown) => {
    issued.push(o);
    return { delegationToken: "dt", clientSigningToken: "cst", validUntil: Date.now() + 60_000 };
  }),
  presignUrl: vi.fn(async (_t: unknown, o: { pathname: string }) => ({
    presignedUrl: `https://store.private.blob.vercel-storage.com/${o.pathname}?vercel-blob-signature=x`,
  })),
}));

const { signRead, verifyRead, VIEWABLE } = await import("@/lib/blob/sign");
const { GET: blobView } = await import("@/app/api/blob-view/route");
const { POST: blobUpload } = await import("@/app/api/blob-upload/route");
const { authorizeBlobUpload, BlobRefusal } = await import("@/lib/blob/authorize");
const { getPayloadFromClientToken } = await import("@vercel/blob/client");

const TOKEN = "vercel_blob_rw_teststore123_0123456789abcdefSECRET";
const KEY = "receipts/0192a6f0-0000-7000-8000-000000000001/AbCdEfGhIjKlMnOpQrStUvWxYz012345.jpg";

beforeEach(() => {
  vi.stubEnv("STORAGE_PROVIDER", "vercel_blob");
  vi.stubEnv("BLOB_READ_WRITE_TOKEN", TOKEN);
  vi.stubEnv("KT_API_ORIGIN", "http://api.test");
  issued.length = 0;
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("signed receipt views (ADR-0024)", () => {
  it("EXTRACT-10 the HMAC matches apps/api storage.sign_read byte for byte [FR-18]", () => {
    // Vectors printed by `sign_read(token, pathname, expires)` in apps/api/app/adapters/storage.py.
    expect(signRead(TOKEN, KEY, 1760000000)).toBe(
      "9685de75be12c5fde0913517add07c09dfddc91d73e5f14e40f587ed76616827",
    );
    expect(signRead(TOKEN, "receipts/x y/ü.pdf", 1700000000)).toBe(
      "d6db20e1dc6c6a642713311c758b368d1e00f23652dae6a9f1cbe301e7b55df5",
    );
  });

  it("EXTRACT-10 wrong, malformed and expired links are refused [FR-18]", () => {
    const sig = signRead(TOKEN, KEY, 2000);
    expect(verifyRead(TOKEN, KEY, "2000", sig, 1999)).toBe("ok");
    expect(verifyRead(TOKEN, KEY, "2000", sig, 2001)).toBe("expired");
    expect(verifyRead(TOKEN, KEY, "2001", sig, 1999)).toBe("bad");
    expect(verifyRead(TOKEN, `${KEY}x`, "2000", sig, 1999)).toBe("bad");
    expect(verifyRead("other-token", KEY, "2000", sig, 1999)).toBe("bad");
    expect(verifyRead(TOKEN, KEY, "2000", sig.toUpperCase(), 1999)).toBe("bad");
    expect(verifyRead(TOKEN, KEY, null, sig, 1)).toBe("bad");
    expect(VIEWABLE.test(KEY)).toBe(true);
    expect(VIEWABLE.test("backups/kitchentab-2026-10-09.jsonl.gz")).toBe(false);
  });

  it("EXTRACT-10 a good link redirects to a 5-minute Vercel signed URL for exactly that blob [FR-18]", async () => {
    const expires = Math.floor(Date.now() / 1000) + 300;
    const url = `http://web.test/api/blob-view?pathname=${encodeURIComponent(KEY)}&expires=${expires}&sig=${signRead(TOKEN, KEY, expires)}`;
    const res = await blobView(new Request(url));
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toContain(`blob.vercel-storage.com/${KEY}`);
    expect(res.headers.get("cache-control")).toContain("no-store");
    expect(issued).toEqual([
      expect.objectContaining({ token: TOKEN, pathname: KEY, operations: ["get"] }),
    ]);
    const until = (issued[0] as { validUntil: number }).validUntil;
    expect(until).toBeLessThanOrEqual(Date.now() + 5 * 60_000);
  });

  it("EXTRACT-10 expired or tampered links get 403; without Vercel Blob the route is 404 [FR-18]", async () => {
    const past = Math.floor(Date.now() / 1000) - 1;
    const expired = await blobView(
      new Request(
        `http://web.test/api/blob-view?pathname=${encodeURIComponent(KEY)}&expires=${past}&sig=${signRead(TOKEN, KEY, past)}`,
      ),
    );
    expect(expired.status).toBe(403);
    const tampered = await blobView(
      new Request(
        `http://web.test/api/blob-view?pathname=${encodeURIComponent(KEY)}&expires=${past + 999}&sig=${signRead(TOKEN, KEY, past)}`,
      ),
    );
    expect(tampered.status).toBe(403);
    expect(issued).toEqual([]);
    vi.stubEnv("STORAGE_PROVIDER", "s3");
    expect((await blobView(new Request("http://web.test/api/blob-view"))).status).toBe(404);
  });
});

describe("client upload tokens (ADR-0015)", () => {
  const ticket = {
    provider: "vercel_blob",
    storage_key: KEY,
    method: "blob-client",
    upload_url: null,
    headers: {},
    content_type: "image/jpeg",
    max_bytes: 123456,
    expires_at: new Date(Date.now() + 600_000).toISOString(),
    blob: null,
  };

  it("EXTRACT-01 the token is minted only after the API authorizes that pathname, with the user's cookie and CSRF [FR-10]", async () => {
    const fetchMock = vi.fn(async () => Response.json(ticket));
    vi.stubGlobal("fetch", fetchMock);
    const req = new Request("http://web.test/api/blob-upload", {
      method: "POST",
      headers: {
        cookie: "kt_session=s; kt_csrf=c",
        "x-csrf-token": "c",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        type: "blob.generate-client-token",
        payload: {
          pathname: KEY,
          clientPayload: JSON.stringify({ bill_id: "b1", mime: "image/jpeg", bytes: 123456 }),
          multipart: false,
        },
      }),
    });
    const res = await blobUpload(req);
    expect(res.status).toBe(200);
    const out = (await res.json()) as { type: string; clientToken: string };
    expect(out.type).toBe("blob.generate-client-token");
    const payload = getPayloadFromClientToken(out.clientToken);
    expect(payload).toMatchObject({
      pathname: KEY,
      allowedContentTypes: ["image/jpeg"],
      maximumSizeInBytes: 123456,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://api.test/api/v1/uploads/authorize");
    const headers = init.headers as Record<string, string>;
    expect(headers.Cookie).toBe("kt_session=s; kt_csrf=c");
    expect(headers["X-CSRF-Token"]).toBe("c");
    expect(JSON.parse(String(init.body))).toEqual({
      bill_id: "b1",
      mime: "image/jpeg",
      bytes: 123456,
      storage_key: KEY,
    });
  });

  it("EXTRACT-01 the API's refusal (413, 403…) reaches the browser and no token is minted [FR-10]", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { title: "That file is over 20 MB", status: 413, code: "FILE_TOO_LARGE" },
          { status: 413 },
        ),
      ),
    );
    await expect(
      authorizeBlobUpload(
        new Request("http://web.test/x"),
        KEY,
        JSON.stringify({ bill_id: "b", mime: "image/jpeg", bytes: 1 }),
      ),
    ).rejects.toBeInstanceOf(BlobRefusal);
    await expect(
      authorizeBlobUpload(new Request("http://web.test/x"), KEY, "not json"),
    ).rejects.toMatchObject({ status: 422 });
    const res = await blobUpload(
      new Request("http://web.test/api/blob-upload", {
        method: "POST",
        body: JSON.stringify({
          type: "blob.generate-client-token",
          payload: {
            pathname: KEY,
            clientPayload: JSON.stringify({ bill_id: "b", mime: "image/jpeg", bytes: 1 }),
            multipart: false,
          },
        }),
      }),
    );
    expect(res.status).toBe(413);
    expect(((await res.json()) as { code: string }).code).toBe("FILE_TOO_LARGE");
  });

  it("EXTRACT-01 webhooks and other bodies are refused; without Vercel Blob the route is 404 [FR-10]", async () => {
    const res = await blobUpload(
      new Request("http://web.test/api/blob-upload", {
        method: "POST",
        body: JSON.stringify({ type: "blob.upload-completed" }),
      }),
    );
    expect(res.status).toBe(400);
    vi.stubEnv("STORAGE_PROVIDER", "fakeblob");
    expect(
      (
        await blobUpload(
          new Request("http://web.test/api/blob-upload", { method: "POST", body: "{}" }),
        )
      ).status,
    ).toBe(404);
  });
});
