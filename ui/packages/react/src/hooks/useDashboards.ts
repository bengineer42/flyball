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

/** `GET /api/dashboards`: the newest version of each of this rig's dashboards, newest saved first. */
export function useDashboards(every = false): QueryState<DashboardRow[]> {
  const rig = useRig();
  const query = useQuery(async () => (await rig.dashboards(every)).slice().sort((a, b) => b.created_ns - a.created_ns), [rig, every]);
  useEffect(() => {
    listeners.add(query.refresh);
    return () => {
      listeners.delete(query.refresh);
    };
  }, [query.refresh]);
  return query;
}
