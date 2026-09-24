import { useEffect } from "react";
import type { DashboardRow } from "@flyball/client";
import { useRig } from "../provider.js";
import { useQuery, type QueryState } from "./useQuery.js";

const listeners = new Set<() => void>();

/**
 * Tell every mounted `useDashboards()` to refetch -- the app-bar switcher and
 * the dashboards page each hold their own copy of the list, so a save,
 * rename, delete or default change made through one must be told to the
 * other rather than waiting for its own poll (there isn't one).
 */
export function invalidateDashboards() {
  listeners.forEach((fn) => fn());
}

/**
 * The order dashboards' tabs sit in: by the document's `order`, ascending, then
 * the unordered ones newest saved first -- which is every dashboard's place
 * until someone moves a tab. A copy; `rows` is left as it was.
 */
export function byTabOrder(rows: readonly DashboardRow[]): DashboardRow[] {
  const order = (row: DashboardRow) => row.body?.order ?? null;
  return rows.slice().sort((a, b) => {
    const [oa, ob] = [order(a), order(b)];
    if (oa !== null && ob !== null && oa !== ob) return oa - ob;
    if (oa !== null && ob === null) return -1;
    if (oa === null && ob !== null) return 1;
    return b.created_ns - a.created_ns;
  });
}

/** `GET /api/dashboards`: the newest version of each of this rig's dashboards, in tab order (`byTabOrder`). */
export function useDashboards(every = false): QueryState<DashboardRow[]> {
  const rig = useRig();
  const query = useQuery(async () => byTabOrder(await rig.dashboards(every)), [rig, every]);
  useEffect(() => {
    listeners.add(query.refresh);
    return () => {
      listeners.delete(query.refresh);
    };
  }, [query.refresh]);
  return query;
}
