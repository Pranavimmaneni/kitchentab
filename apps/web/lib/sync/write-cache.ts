import type { QueryClient, QueryKey } from "@tanstack/react-query";

/**
 * Puts a write's response into the cache (PRD §21.6). A GET that started before the write
 * committed would land after this update with the old value, and the write's own echo event is
 * ignored as already applied, so the stale value would stick: cancel that GET (it reverts
 * synchronously), apply the update, then fetch again from after the commit.
 */
export function writeThrough<T>(client: QueryClient, key: QueryKey, update: (old: T) => T): void {
  const inflight = client.isFetching({ queryKey: key, exact: true }) > 0;
  if (inflight) void client.cancelQueries({ queryKey: key, exact: true });
  client.setQueryData<T>(key, (old) => (old === undefined ? old : update(old)));
  if (inflight) void client.invalidateQueries({ queryKey: key, exact: true });
}
