import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BillView } from "../../lib/api/types";
import { lastActorOf, forgetActors } from "../../lib/sync/actors";
import { forgetMutations, isOwnMutation, rememberMutation } from "../../lib/sync/echo";
import { startHouseSync, streamUrl, type HouseSyncOptions } from "../../lib/sync/house-sync";
import { runAsLeader } from "../../lib/sync/leader";
import type { CatchUp, HouseEvent } from "../../lib/sync/types";
import { billView } from "./fixtures/bill";
import { FakeEventSource, fakeChannels, fakeLocks, tick } from "./fixtures/sync";

const openBill = () => billView({ bill: { ...billView().bill, state: "open", version: 4 } });

function tickEvent(seq: number, participant: string, on: boolean, cmid: string | null = null): HouseEvent {
  return {
    seq,
    type: "allocation_changed",
    aggregate: { type: "bill", id: "b1", version: 4 },
    actor_member_id: "arjun",
    client_mutation_id: cmid,
    data: {
      bill_id: "b1",
      changes: [{ item_id: "milk", participant, on }],
      shares: on ? { "m:pranav": 500, "m:arjun": 500 } : { "m:pranav": 1000, "m:arjun": 0 },
    },
  };
}

function tab(shared: { locks: ReturnType<typeof fakeLocks>; channel: ReturnType<typeof fakeChannels> }, extra: Partial<HouseSyncOptions> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(["bill", "b1"], openBill());
  const onRevoked = vi.fn();
  const fetchAfter = vi.fn(async (): Promise<CatchUp> => ({ resync: false, events: [] }));
  const sync = startHouseSync({
    houseId: "h1",
    memberId: "pranav",
    queryClient: client,
    onRevoked,
    fetchAfter,
    createEventSource: (url) => new FakeEventSource(url),
    locks: shared.locks,
    channel: shared.channel,
    windowTarget: null,
    debounceMs: 0,
    random: () => 0.5,
    ...extra,
  });
  const bill = () => client.getQueryData<BillView>(["bill", "b1"])!;
  return { client, sync, onRevoked, fetchAfter, bill };
}

describe("house sync: leader tab, relay, echo suppression (PRD §21.6)", () => {
  beforeEach(() => {
    FakeEventSource.reset();
    forgetMutations();
    forgetActors();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("SYNC-31 two tabs elect one stream leader; closing the leader hands over at once [SYNC-20]", async () => {
    const shared = { locks: fakeLocks(), channel: fakeChannels() };
    const a = tab(shared);
    const b = tab(shared);
    await tick();
    expect(a.sync.isLeader()).toBe(true);
    expect(b.sync.isLeader()).toBe(false);
    expect(FakeEventSource.open()).toHaveLength(1);

    const stream = FakeEventSource.last();
    stream.emit("ready", { seq: 10 });
    stream.emit("message", tickEvent(11, "m:arjun", true));
    await tick();
    // The follower got the event over the relay and patched its own cache.
    for (const t of [a, b]) {
      expect(t.bill().items[0]!.allocations.map((x) => x.participant).sort()).toEqual(["m:arjun", "m:pranav"]);
      expect(t.bill().shares).toEqual({ "m:pranav": 500, "m:arjun": 500 });
    }
    expect(b.sync.lastSeq()).toBe(11);

    // The leader tab closes: the lock frees and the follower opens the stream from seq 11.
    a.sync.stop();
    await tick();
    await tick();
    expect(b.sync.isLeader()).toBe(true);
    const open = FakeEventSource.open();
    expect(open).toHaveLength(1);
    expect(open[0]!.url).toBe(streamUrl("h1", 11));
    b.sync.stop();
  });

  it("SYNC-32 the tab's own echo is not patched in and does not refetch the optimistic bill [SYNC-20]", async () => {
    const shared = { locks: fakeLocks(), channel: fakeChannels() };
    const a = tab(shared);
    await tick();
    const before = a.bill();
    rememberMutation("mine-1");
    expect(isOwnMutation("mine-1")).toBe(true);
    const stream = FakeEventSource.last();
    stream.emit("ready", { seq: 1 });
    await tick();
    const refreshes = vi.spyOn(a.client, "invalidateQueries");
    stream.emit("message", tickEvent(2, "m:arjun", true, "mine-1"));
    await tick();
    expect(a.bill()).toBe(before); // same object: nothing re-rendered, nothing to flicker
    const billCalls = refreshes.mock.calls.filter(([filters]) => {
      const key = (filters as { queryKey?: unknown[] } | undefined)?.queryKey;
      return key?.[0] === "bill" && key.length === 2;
    });
    expect(billCalls.every(([filters]) => (filters as { refetchType?: string }).refetchType === "none")).toBe(true);
    // Owed amounts are not optimistic: they still refresh after my own tick.
    const moneyCalls = refreshes.mock.calls.filter(
      ([filters]) => (filters as { queryKey?: unknown[] } | undefined)?.queryKey?.[2] === "money",
    );
    expect(moneyCalls.map(([filters]) => (filters as { refetchType?: string }).refetchType)).toEqual(["active"]);
    // A roommate's event right after is applied.
    stream.emit("message", tickEvent(3, "m:arjun", true, "theirs-1"));
    await tick();
    expect(a.bill()).not.toBe(before);
    expect(a.sync.lastSeq()).toBe(3);
    a.sync.stop();
  });

  it("SYNC-18 the leader fills a sequence gap with ?after= before applying, and relays in order", async () => {
    const shared = { locks: fakeLocks(), channel: fakeChannels() };
    const a = tab(shared, {
      fetchAfter: vi.fn(async (after: number) => ({
        resync: false,
        events: [tickEvent(2, "m:arjun", true), tickEvent(3, "m:arjun", false)].filter((e) => e.seq > after),
      })),
    });
    const b = tab(shared);
    await tick();
    const stream = FakeEventSource.last();
    stream.emit("ready", { seq: 1 });
    stream.emit("message", tickEvent(4, "m:arjun", true));
    await vi.waitFor(() => expect(a.sync.lastSeq()).toBe(4));
    await tick();
    expect(b.sync.lastSeq()).toBe(4);
    expect(b.bill().items[0]!.allocations.map((x) => x.participant).sort()).toEqual(["m:arjun", "m:pranav"]);
    a.sync.stop();
    b.sync.stop();
  });

  it("SYNC-12 the leader's revoked stream signs every tab of the house out of it [SYNC-33]", async () => {
    const shared = { locks: fakeLocks(), channel: fakeChannels() };
    const a = tab(shared);
    const b = tab(shared);
    await tick();
    FakeEventSource.last().emit("revoked", { reason: "not_a_member" });
    await tick();
    expect(a.onRevoked).toHaveBeenCalledTimes(1);
    expect(b.onRevoked).toHaveBeenCalledTimes(1);
    expect(FakeEventSource.open()).toHaveLength(0);
  });

  it("SYNC-12 an event removing my own member revokes", async () => {
    const shared = { locks: fakeLocks(), channel: fakeChannels() };
    const a = tab(shared);
    await tick();
    const stream = FakeEventSource.last();
    stream.emit("ready", { seq: 1 });
    stream.emit("message", {
      seq: 2,
      type: "member_removed",
      aggregate: { type: "member", id: "pranav", version: 2 },
      actor_member_id: "arjun",
      client_mutation_id: null,
      data: null,
    } satisfies HouseEvent);
    await tick();
    expect(a.onRevoked).toHaveBeenCalledTimes(1);
  });

  it("SYNC-05 remembers who last changed an aggregate for the conflict prompt", async () => {
    const a = tab({ locks: fakeLocks(), channel: fakeChannels() });
    await tick();
    const stream = FakeEventSource.last();
    stream.emit("ready", { seq: 1 });
    stream.emit("message", {
      seq: 2,
      type: "item_updated",
      aggregate: { type: "item", id: "milk", version: 2 },
      actor_member_id: "arjun",
      client_mutation_id: null,
      data: { ...openBill().items[0]!, version: 2, line_total_cents: 1250, bill_id: "b1" },
    } satisfies HouseEvent);
    await tick();
    expect(lastActorOf("milk")).toBe("arjun");
    expect(a.bill().items[0]!.line_total_cents).toBe(1250);
    a.sync.stop();
  });

  it("SYNC-34 a stream that never opens: polling takes its baseline from ?after=0, then polls ?after=N every 15 s", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
    const calls: number[] = [];
    const a = tab(
      { locks: fakeLocks(), channel: fakeChannels() },
      {
        fetchAfter: vi.fn(async (after: number) => {
          calls.push(after);
          return after === 0
            ? { resync: false, events: [tickEvent(1, "m:arjun", false), tickEvent(2, "m:arjun", false)] }
            : { resync: false, events: after < 3 ? [tickEvent(3, "m:arjun", true)] : [] };
        }),
      },
    );
    await vi.advanceTimersByTimeAsync(0);
    for (let n = 0; n < 3; n++) {
      FakeEventSource.last().fail();
      await vi.advanceTimersByTimeAsync(n < 2 ? 2500 : 0);
    }
    expect(a.sync.transport()!.polling).toBe(true);
    expect(calls).toEqual([0]);
    expect(a.sync.lastSeq()).toBe(2);
    await vi.advanceTimersByTimeAsync(15_000);
    expect(calls).toEqual([0, 2]);
    expect(a.sync.lastSeq()).toBe(3);
    expect(a.bill().items[0]!.allocations.map((x) => x.participant).sort()).toEqual(["m:arjun", "m:pranav"]);
    a.sync.stop();
  });

  it("SYNC-01 a patch is not erased by a GET that started before the event's commit", async () => {
    const shared = { locks: fakeLocks(), channel: fakeChannels() };
    const a = tab(shared);
    await tick();
    let release!: (view: BillView) => void;
    const stale = openBill();
    const fetching = a.client.fetchQuery({
      queryKey: ["bill", "b1"],
      queryFn: () =>
        new Promise<BillView>((resolve) => {
          release = resolve;
        }),
      staleTime: 0,
    });
    const stream = FakeEventSource.last();
    stream.emit("ready", { seq: 1 });
    stream.emit("message", tickEvent(2, "m:arjun", true));
    await tick();
    expect(a.bill().items[0]!.allocations).toHaveLength(2);
    release(stale); // the old response lands after the patch
    await fetching.catch(() => undefined);
    await tick();
    expect(a.bill().items[0]!.allocations).toHaveLength(2);
    a.sync.stop();
  });

  it("SYNC-20 without Web Locks every tab opens its own stream", async () => {
    const channel = fakeChannels();
    const a = tab({ locks: fakeLocks(), channel }, { locks: null });
    const b = tab({ locks: fakeLocks(), channel }, { locks: null });
    await tick();
    expect(a.sync.isLeader() && b.sync.isLeader()).toBe(true);
    expect(FakeEventSource.open()).toHaveLength(2);
    a.sync.stop();
    b.sync.stop();
  });

  it("SYNC-31 a queued tab that leaves before it leads never opens a stream", async () => {
    const locks = fakeLocks();
    const lead = vi.fn(() => () => undefined);
    const stopFirst = runAsLeader("x", lead, locks);
    const stopSecond = runAsLeader("x", lead, locks);
    await tick();
    expect(lead).toHaveBeenCalledTimes(1);
    stopSecond();
    stopFirst();
    await tick();
    expect(lead).toHaveBeenCalledTimes(1);
  });
});
