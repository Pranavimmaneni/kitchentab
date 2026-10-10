/**
 * Wire shapes of the house event stream (PRD §21.5; apps/api/app/routes/events.py `wire`).
 * `data` is a small delta or null ("refetch me").
 */
export type HouseEvent = {
  seq: number;
  type: string;
  aggregate: { type: string; id: string; version: number | null };
  actor_member_id: string | null;
  client_mutation_id: string | null;
  data: Record<string, unknown> | null;
};

/** `GET /houses/{id}/events?after=N` (JSON catch-up, max 500 events). */
export type CatchUp = { resync: boolean; events: HouseEvent[] };

/** What the leader tab relays to the other tabs over BroadcastChannel (PRD §21.6). */
export type RelayMessage =
  | { kind: "event"; event: HouseEvent }
  | { kind: "resync" }
  | { kind: "revoked" }
  /** A follower asks the leader to catch up now (focus, online, visible). */
  | { kind: "catchup" };
