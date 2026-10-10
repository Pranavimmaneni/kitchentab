/**
 * Real-time sync for one house (PRD §21.5, §21.6): wires the leader election, the stream
 * transport, the in-order sequencer, the event reducer and the cache together.
 *
 *   leader tab:   EventSource ──► Sequencer (gap fill via ?after=) ──► apply ──► cache
 *                                                                      └──► BroadcastChannel
 *   other tabs:   BroadcastChannel ──► Sequencer ──► apply ──► cache
 *
 * Own echoes (events whose `client_mutation_id` this tab sent) are not patched in and do not
 * refetch the optimistic aggregate, so own changes never flicker; non-optimistic views (money,
 * activity, lists) still refresh.
 */
import type { QueryClient } from "@tanstack/react-query";
import { API_PREFIX, api, isApiError } from "../api/client";
import type { BillView } from "../api/types";
import { recordActor } from "./actors";
import { isOwnMutation } from "./echo";
import { cachedBills, createInvalidator, INVALIDATE_DEBOUNCE_MS, type Refetch } from "./invalidate";
import {
  defaultChannel,
  defaultLocks,
  openRelay,
  runAsLeader,
  type ChannelFactory,
  type LockManagerLike,
} from "./leader";
import { planEvent } from "./plan";
import { Sequencer } from "./sequencer";
import { StreamTransport, type EventSourceFactory, type TransportStatus } from "./transport";
import type { CatchUp, HouseEvent } from "./types";

export type HouseSyncOptions = {
  houseId: string;
  /** The signed-in member of this house (for "was I removed?"). */
  memberId: string | null;
  queryClient: QueryClient;
  onRevoked: () => void;
  /** The session itself was revoked (sign out everywhere, AUTH-09). */
  onSignedOut?: () => void;
  fetchAfter?: (after: number) => Promise<CatchUp>;
  createEventSource?: EventSourceFactory;
  /** null disables Web Locks (every tab leads); undefined uses navigator.locks. */
  locks?: LockManagerLike | null;
  /** null disables the relay; undefined uses BroadcastChannel. */
  channel?: ChannelFactory | null;
  random?: () => number;
  pollIntervalMs?: number;
  debounceMs?: number;
  /** Event target for the catch-up triggers (default: window / document). */
  windowTarget?: Pick<Window, "addEventListener" | "removeEventListener"> | null;
  onStatus?: (
    status: TransportStatus,
    info: { failures: number; polling: boolean; leader: boolean },
  ) => void;
};

export type HouseSync = {
  stop: () => void;
  isLeader: () => boolean;
  lastSeq: () => number | null;
  /** Catch up now (focus, online, visible). */
  catchUp: () => void;
  transport: () => StreamTransport | null;
};

export function streamUrl(houseId: string, lastSeq: number | null): string {
  const base = `${API_PREFIX}/houses/${houseId}/events`;
  return lastSeq === null ? base : `${base}?last_event_id=${lastSeq}`;
}

export function startHouseSync(options: HouseSyncOptions): HouseSync {
  const { houseId, queryClient } = options;
  const invalidator = createInvalidator(
    queryClient,
    houseId,
    options.debounceMs ?? INVALIDATE_DEBOUNCE_MS,
  );
  const fetchAfter =
    options.fetchAfter ??
    ((after: number) => api.get<CatchUp>(`/houses/${houseId}/events?after=${after}`));
  let leader = false;
  let stopped = false;
  let transport: StreamTransport | null = null;

  function revoke(): void {
    if (stopped) return;
    if (leader) relay.post({ kind: "revoked" });
    stop();
    options.onRevoked();
  }

  function applyEvent(event: HouseEvent): void {
    recordActor(event.aggregate.id, event.actor_member_id);
    const own = isOwnMutation(event.client_mutation_id);
    const plan = planEvent(event, {
      myMemberId: options.memberId,
      bills: () => cachedBills(queryClient, houseId),
    });
    if (!own) {
      for (const patch of plan.patches) {
        const key = ["bill", patch.billId];
        // A GET that started before this commit would land after the patch and erase it: cancel
        // it (reverts synchronously), patch, then refetch from after the commit.
        const inflight = queryClient.isFetching({ queryKey: key, exact: true }) > 0;
        if (inflight) void queryClient.cancelQueries({ queryKey: key, exact: true });
        queryClient.setQueryData<BillView>(key, (old) => (old ? patch.update(old) : old));
        if (inflight) invalidator.schedule([{ kind: "bill", id: patch.billId }]);
      }
      invalidator.schedule(plan.invalidate);
    } else {
      for (const target of plan.invalidate) {
        const optimistic = target.kind === "bill" || target.kind === "bills";
        invalidator.schedule([target], optimistic ? ("none" satisfies Refetch) : "active");
      }
    }
    if (leader) relay.post({ kind: "event", event });
    if (plan.revoked) revoke();
  }

  function resync(): void {
    invalidator.everything();
    if (leader) relay.post({ kind: "resync" });
  }

  const sequencer = new Sequencer({
    fetchAfter,
    apply: applyEvent,
    onResync: resync,
    onError: (error) => {
      if (isApiError(error) && error.code === "NOT_A_MEMBER") revoke();
    },
  });

  const relay = openRelay(
    `kt-sync:${houseId}`,
    (message) => {
      if (stopped) return;
      if (leader) {
        if (message.kind === "catchup") void sequencer.catchUp();
        return;
      }
      switch (message.kind) {
        case "event":
          sequencer.receive(message.event);
          break;
        case "resync":
          sequencer.reset(null);
          invalidator.everything();
          break;
        case "revoked":
          revoke();
          break;
        default:
          break;
      }
    },
    options.channel === null ? undefined : (options.channel ?? defaultChannel()),
  );
  // Followers depend on the relay; without one, every tab opens its own stream.
  const locks = relay.available
    ? options.locks === null
      ? undefined
      : (options.locks ?? defaultLocks())
    : undefined;

  function lead(): () => void {
    leader = true;
    const t = new StreamTransport({
      url: (last) => streamUrl(houseId, last),
      lastSeq: () => sequencer.lastSeq,
      onReady: (seq) => {
        if (sequencer.lastSeq === null) {
          // Fresh stream: anything committed between this tab's first GETs and now is covered
          // by refetching what the house shows once.
          sequencer.reset(seq);
          invalidator.schedule([{ kind: "bills" }, { kind: "house" }]);
        }
      },
      onEvent: (event) => sequencer.receive(event),
      onResync: (seq) => {
        sequencer.reset(seq);
        resync();
      },
      onRevoked: (reason) => {
        if (reason === "signed_out") {
          stop();
          options.onSignedOut?.();
        } else revoke();
      },
      poll: async () => {
        if (sequencer.lastSeq === null) {
          // The stream never opened, so there is no baseline: take the latest seq from the
          // catch-up and rebuild what is on screen with plain GETs; later polls use `?after=`.
          try {
            const page = await fetchAfter(0);
            if (!page.resync && sequencer.lastSeq === null) {
              sequencer.reset(
                page.events.length > 0 ? page.events[page.events.length - 1]!.seq : 0,
              );
            }
          } catch (error) {
            if (isApiError(error) && error.code === "NOT_A_MEMBER") revoke();
            return;
          }
          invalidator.schedule([{ kind: "bills" }, { kind: "house" }, { kind: "payments" }]);
          return;
        }
        await sequencer.catchUp();
      },
      createEventSource: options.createEventSource,
      random: options.random,
      pollIntervalMs: options.pollIntervalMs,
      onStatus: (status, info) => options.onStatus?.(status, { ...info, leader }),
    });
    transport = t;
    t.start();
    return () => {
      t.stop();
      if (transport === t) transport = null;
      leader = false;
    };
  }

  const stepDown = runAsLeader(`kt-sse:${houseId}`, lead, locks);

  function catchUp(): void {
    if (stopped) return;
    if (leader) {
      transport?.kick();
      void sequencer.catchUp();
    } else {
      relay.post({ kind: "catchup" });
    }
  }

  const target =
    options.windowTarget === null
      ? null
      : (options.windowTarget ?? (typeof window === "undefined" ? null : window));
  const onVisible = () => {
    if (typeof document === "undefined" || document.visibilityState === "visible") catchUp();
  };
  target?.addEventListener("online", catchUp);
  target?.addEventListener("focus", catchUp);
  if (target && typeof document !== "undefined")
    document.addEventListener("visibilitychange", onVisible);

  function stop(): void {
    if (stopped) return;
    stopped = true;
    target?.removeEventListener("online", catchUp);
    target?.removeEventListener("focus", catchUp);
    if (target && typeof document !== "undefined")
      document.removeEventListener("visibilitychange", onVisible);
    stepDown();
    relay.close();
    sequencer.stop();
    invalidator.cancel();
  }

  return {
    stop,
    isLeader: () => leader,
    lastSeq: () => sequencer.lastSeq,
    catchUp,
    transport: () => transport,
  };
}
