import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { announceSignedOut, onSignedOut } from "../../lib/sync/session";
import { writeThrough } from "../../lib/sync/write-cache";

describe("sign-out reaches every tab (PRD §21.6)", () => {
  it("AUTH-09 this tab hears its own sign-out, and another tab's over the BroadcastChannel [FR-01]", async () => {
    const heard = vi.fn();
    const off = onSignedOut(heard);
    announceSignedOut();
    expect(heard).toHaveBeenCalledTimes(1);

    // Another tab of this browser.
    const other = new BroadcastChannel("kt-session");
    other.postMessage({ kind: "signed-out" });
    await vi.waitFor(() => expect(heard).toHaveBeenCalledTimes(2));
    other.postMessage({ kind: "something-else" });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(heard).toHaveBeenCalledTimes(2);
    other.close();
    off();
    announceSignedOut();
    expect(heard).toHaveBeenCalledTimes(2);
  });
});

describe("writeThrough (PRD §21.6)", () => {
  it("SYNC-32 a GET that started before the write cannot overwrite the write's result", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const key = ["bill", "b1"];
    client.setQueryData(key, { storage: "fridge", version: 1 });
    let release: (value: { storage: string; version: number }) => void = () => undefined;
    const stale = client.fetchQuery({
      queryKey: key,
      queryFn: () =>
        new Promise<{ storage: string; version: number }>((resolve) => (release = resolve)),
      staleTime: 0,
    });
    writeThrough<{ storage: string; version: number }>(client, key, () => ({
      storage: "freezer",
      version: 2,
    }));
    release({ storage: "fridge", version: 1 }); // the old answer lands late
    await stale.catch(() => undefined);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(client.getQueryData(key)).toEqual({ storage: "freezer", version: 2 });
    // …and the query is marked stale so the next read refetches from after the commit.
    expect(client.getQueryState(key)?.isInvalidated).toBe(true);
  });
});
