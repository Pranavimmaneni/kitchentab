# ADR-0023: Concurrent ticks compute shares under the house lock

Status: accepted · Milestone 10 (found by SYNC-24) · Amends ADR-0001 point "house lock last"

**Problem.** Ticks on an open bill take a shared (`FOR SHARE`) bill lock so roommates can tick at
the same time (SYNC-01). Each tick then reloaded allocations, computed shares and wrote a share
snapshot before taking the house lock at commit. Two ticks on different items could each compute
shares that miss the other's change; the later snapshot overwrote the earlier and the stored
shares (change badges, "your share changed" notifications) no longer matched the allocations.
Separately, `kitchen.sync_owners` read owners and allocations in two statements under READ
COMMITTED, so a commit in between made it insert an existing owner (unique violation) or drop a
current one.

**Decision.**
1. `HouseWrite.lock_house()` takes the house lock early. The lock order for every write is
   bill → bill lines → kitchen items → (owner rows) → house. A tick changes owner rows *after* the
   house lock (`sync_owners`), so before `lock_house` it locks the bill's kitchen items in id order;
   a sharing switch or "used up" locks its kitchen item first, so it waits there instead of holding
   an owner row while wanting the house (a deadlock the fresh-eyes review found by reading; the
   race test in `test_kitchen.py` exercises the pair, though the window is too narrow to reproduce
   the deadlock on demand). A tick calls it
   after applying its allocation changes. Everything after that reads every committed tick:
   reloading allocations (only allocations: everything else needs the exclusive bill lock, which a
   tick's shared lock excludes), computing shares, the snapshot, owners and the event. Ticks still
   overlap for authorization, item locks and their own allocation writes; the share computation is
   serialized per house.
2. `sync_owners` is two set-based statements (`INSERT … SELECT … ON CONFLICT DO NOTHING` and
   `DELETE … WHERE NOT EXISTS`), each reading one snapshot.
3. SYNC-24 now runs the invariant checker (`app/services/invariants.py`) after the burst, so a
   stored snapshot or owner set that disagrees with the allocations fails the test.

**Cost.** One tick's critical section is about 9 statements. With local Docker on Windows
(~2–4 ms per statement) a house handles roughly 25–40 ticks a second; on Neon from the same region
it is several times that. A household of up to 20 is well inside either.

**Amendment (same day, after the fresh-eyes review and SYNC-24 profiling).**
- A tick now syncs the kitchen owners of the lines it changed *before* `lock_house`, scoped to those
  lines, while it holds their row locks (every other change to their allocations needs those locks or
  the exclusive bill lock). Nothing writes owner rows after the house lock any more, which removes the
  sharing-switch deadlock at its source; the extra kitchen-items lock is gone.
- `lock_house` reads `event_seq` while locking, so the commit path writes the events, their activity
  rows, outbox rows and the new sequence in one prepared statement (`unnest` arrays into
  data-modifying CTEs). Snapshot rows go in one prepared statement too, allocations are reloaded as
  plain rows, and `snapshot_state` reads one row per participant (`DISTINCT ON`) instead of the
  whole history.
- Measured locally (Windows, Docker Postgres, 4–16 uvicorn workers, 20 clients ticking flat out):
  about 66–68 ticks/s per house, p95 0.57–0.64 s. The house lock serializes about 15 ms per tick;
  more workers don't help. SYNC-24's p95 < 300 ms is **not met** on this machine; it is re-measured
  on the local-vercel rehearsal and recorded in TEST_REPORT. Realistic use (each person batches ticks
  every 250 ms in the client) is well inside the capacity.
