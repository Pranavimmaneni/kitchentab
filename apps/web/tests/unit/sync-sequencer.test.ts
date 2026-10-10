import { describe, expect, it, vi } from "vitest";
import { Sequencer } from "../../lib/sync/sequencer";
import type { CatchUp, HouseEvent } from "../../lib/sync/types";

function ev(seq: number): HouseEvent {
  return {
    seq,
    type: "allocation_changed",
    aggregate: { type: "bill", id: "b1", version: 1 },
    actor_member_id: "arjun",
    client_mutation_id: null,
    data: null,
  };
}

function setup(server: (after: number) => CatchUp | Promise<CatchUp>) {
  const applied: number[] = [];
  const fetchAfter = vi.fn(async (after: number) => server(after));
  const onResync = vi.fn();
  const seq = new Sequencer({
    fetchAfter,
    apply: (e) => applied.push(e.seq),
    onResync,
    emptyFillDelayMs: 0,
  });
  return { seq, applied, fetchAfter, onResync };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("in-order, gapless event application (PRD §21.5)", () => {
  it("SYNC-17 applies events in sequence order and drops duplicates from a replay", async () => {
    const { seq, applied, fetchAfter } = setup(() => ({ resync: false, events: [] }));
    seq.reset(10);
    for (const n of [11, 12, 12, 11, 13]) seq.receive(ev(n));
    await flush();
    expect(applied).toEqual([11, 12, 13]);
    expect(fetchAfter).not.toHaveBeenCalled();
  });

  it("SYNC-18 a sequence gap (lost publish) is filled with GET ?after=last before the later event applies", async () => {
    const all = [11, 12, 13, 14].map(ev);
    const { seq, applied, fetchAfter } = setup((after) => ({
      resync: false,
      events: all.filter((e) => e.seq > after),
    }));
    seq.reset(10);
    seq.receive(ev(11));
    seq.receive(ev(14)); // 12 and 13 were never delivered
    expect(applied).toEqual([11]);
    expect(seq.held).toBe(1);
    await flush();
    expect(fetchAfter).toHaveBeenCalledWith(11);
    expect(applied).toEqual([11, 12, 13, 14]);
    expect(seq.lastSeq).toBe(14);
    expect(seq.held).toBe(0);
  });

  it("SYNC-18 events that arrive while a gap fill is in flight wait their turn", async () => {
    let release!: (value: CatchUp) => void;
    const { seq, applied } = setup(
      () =>
        new Promise<CatchUp>((resolve) => {
          release = resolve;
        }),
    );
    seq.reset(1);
    seq.receive(ev(3));
    seq.receive(ev(4));
    seq.receive(ev(5));
    expect(applied).toEqual([]);
    release({ resync: false, events: [ev(2), ev(3)] });
    await flush();
    expect(applied).toEqual([2, 3, 4, 5]);
  });

  it("SYNC-19 a catch-up that answers resync (8 days away, or 500+ behind) refetches everything [SYNC-27]", async () => {
    const { seq, onResync, applied } = setup(() => ({ resync: true, events: [] }));
    seq.reset(3);
    await seq.catchUp();
    expect(onResync).toHaveBeenCalledTimes(1);
    expect(seq.lastSeq).toBeNull();
    // The next event sets a fresh baseline.
    seq.receive(ev(900));
    expect(applied).toEqual([900]);
  });

  it("SYNC-18 a gap the server cannot fill is never waited on forever: resync after three empty fills", async () => {
    const { seq, onResync, fetchAfter } = setup(() => ({ resync: false, events: [] }));
    seq.reset(1);
    seq.receive(ev(5));
    await vi.waitFor(() => expect(onResync).toHaveBeenCalledTimes(1));
    expect(fetchAfter).toHaveBeenCalledTimes(3);
  });

  it("SYNC-17 catch-up on focus/online/reconnect applies what was missed, in order", async () => {
    const { seq, applied } = setup((after) => ({ resync: false, events: [ev(after + 1), ev(after + 2), ev(after + 3)] }));
    seq.reset(7);
    await seq.catchUp();
    expect(applied).toEqual([8, 9, 10]);
  });

  it("SYNC-20 a follower tab takes its baseline from the first relayed event", () => {
    const { seq, applied } = setup(() => ({ resync: false, events: [] }));
    seq.receive(ev(42));
    seq.receive(ev(43));
    expect(applied).toEqual([42, 43]);
  });

  it("SYNC-16 a failed catch-up keeps held events and reports the error", async () => {
    const onError = vi.fn();
    const applied: number[] = [];
    const seq = new Sequencer({
      fetchAfter: async () => {
        throw new Error("offline");
      },
      apply: (e) => applied.push(e.seq),
      onResync: vi.fn(),
      onError,
    });
    seq.reset(1);
    seq.receive(ev(3));
    await flush();
    expect(onError).toHaveBeenCalled();
    expect(seq.held).toBe(1);
    seq.receive(ev(2));
    expect(applied).toEqual([2, 3]);
  });
});
