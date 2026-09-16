import type { DashboardRow } from "@flyball/client";
import { useRig } from "../provider.js";
import { useQuery, type QueryState } from "./useQuery.js";

/** `GET /api/dashboards`: the newest version of each of this rig's dashboards, newest saved first. */
export function useDashboards(every = false): QueryState<DashboardRow[]> {
  const rig = useRig();
  return useQuery(async () => (await rig.dashboards(every)).slice().sort((a, b) => b.created_ns - a.created_ns), [rig, every]);
}
