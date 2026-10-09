import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { keys } from "../../lib/api/hooks";
import type { BillView } from "../../lib/api/types";
import { useSplitWrites } from "../../lib/split/useSplitWrites";
import { billView } from "./fixtures/bill";

function harness(respond: (body: unknown) => Response) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(keys.bill("b1"), billView());
  const requests: { method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: RequestInit) => {
      const body = init.body ? JSON.parse(String(init.body)) : undefined;
      requests.push({ method: String(init.method), body });
      return respond(body);
    }),
  );
  const onError = vi.fn();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const hook = renderHook(() => useSplitWrites("b1", onError), { wrapper });
  return { client, requests, onError, hook };
}

describe("optimistic split writes", () => {
  beforeEach(() => {
    document.cookie = "kt_csrf=test-token";
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("a tick shows at once, goes out as a set-state PUT and the server's shares replace the preview", async () => {
    const { client, requests, hook } = harness(
      () =>
        new Response(
          JSON.stringify({
            applied: [{ item_id: "milk", participant: "m:arjun", on: true }],
            shares: { "m:pranav": 500, "m:arjun": 500 },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    );
    act(() => hook.result.current.setCells([{ itemId: "milk", participant: "m:arjun", on: true }]));
    expect(hook.result.current.overrides.get("milk|m:arjun")).toBe(true);
    expect(hook.result.current.pending).toBe(true);
    await waitFor(() => expect(hook.result.current.pending).toBe(false));
    expect(requests).toEqual([
      { method: "PUT", body: { changes: [{ item_id: "milk", participant: "m:arjun", on: true }] } },
    ]);
    const view = client.getQueryData<BillView>(keys.bill("b1"))!;
    expect(view.items[0]!.allocations.map((a) => a.participant)).toEqual(["m:pranav", "m:arjun"]);
    expect(view.shares).toEqual({ "m:pranav": 500, "m:arjun": 500 });
  });

  it("a failed write rolls the tick back and reports the cells so the screen can offer Retry", async () => {
    const { client, onError, hook } = harness(
      () =>
        new Response(JSON.stringify({ code: "INTERNAL", title: "Something jammed. Try again." }), {
          status: 500,
          headers: { "Content-Type": "application/problem+json" },
        }),
    );
    act(() => hook.result.current.setCells([{ itemId: "milk", participant: "m:arjun", on: true }]));
    expect(hook.result.current.overrides.size).toBe(1);
    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(hook.result.current.overrides.size).toBe(0));
    expect(onError.mock.calls[0]![0].changes).toEqual([{ itemId: "milk", participant: "m:arjun", on: true }]);
    const view = client.getQueryData<BillView>(keys.bill("b1"))!;
    expect(view.items[0]!.allocations.map((a) => a.participant)).toEqual(["m:pranav"]);
  });
  it("writes go out one at a time, so a tick and its undo can never commit out of order", async () => {
    const { hook } = harness(() => new Response("{}"));
    const started: boolean[] = [];
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit) => {
        const body = JSON.parse(String(init.body)) as { changes: { on: boolean }[] };
        started.push(body.changes[0]!.on);
        if (started.length === 1) await gate;
        return new Response(JSON.stringify({ applied: [], shares: null }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    act(() => hook.result.current.setCells([{ itemId: "milk", participant: "m:arjun", on: true }], { immediate: true }));
    act(() => hook.result.current.setCells([{ itemId: "milk", participant: "m:arjun", on: false }], { immediate: true }));
    await new Promise((r) => setTimeout(r, 50));
    expect(started).toEqual([true]);
    release();
    await waitFor(() => expect(started).toEqual([true, false]));
    await waitFor(() => expect(hook.result.current.pending).toBe(false));
  });
});
