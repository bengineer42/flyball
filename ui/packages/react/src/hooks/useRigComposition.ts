import type { JsonSchema, RigDocument, RigVersion } from "@flyball/client";
import { useRig } from "../provider.js";
import { useQuery, type QueryState } from "./useQuery.js";

/** `GET /api/rig/schema` once: the rig file's JSON schema, with every driver and link type installed. */
export function useRigFileSchema(): QueryState<JsonSchema> {
  const rig = useRig();
  return useQuery((signal) => rig.rigSchema(signal), [rig]);
}

/** `GET /api/rig/document`: the running rig as a rig file would build it, polled every `refreshMs` (default: once). */
export function useRigDocument(refreshMs?: number): QueryState<RigDocument> {
  const rig = useRig();
  return useQuery((signal) => rig.rigDocument(signal), [rig], refreshMs ? { refreshMs } : {});
}

/** `GET /api/rig/changes`: the overlay of what differs from the rig as this run started; `{}` when nothing has. */
export function useRigChanges(refreshMs?: number): QueryState<Record<string, unknown>> {
  const rig = useRig();
  return useQuery((signal) => rig.rigChanges(signal), [rig], refreshMs ? { refreshMs } : {});
}

/** `GET /api/rig/versions`: every version this store has seen, newest first. */
export function useRigVersions(limit?: number): QueryState<RigVersion[]> {
  const rig = useRig();
  return useQuery(() => rig.rigVersions(limit), [rig, limit]);
}
