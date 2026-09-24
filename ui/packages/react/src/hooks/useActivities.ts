import { useCallback } from "react";
import type { ActivityOut } from "@flyball/client";
import { useRig } from "../provider.js";
import type { StreamStatus } from "./useStream.js";
import { useStoreStatus, useActivityStates } from "../store/hooks.js";

/**
 * What the rig is waiting on, live from `/ws/activities` through the telemetry
 * store. Every blocking program step registers one (a wait, a settle, a
 * prompt); `pending` is only those waiting on a *person* -- a program's
 * `prompt` -- the ones a UI should put a button in front of. `fire` and
 * `cancel` answer one; the socket carries the settlement back.
 */
export function useActivities(): {
  activities: Record<string, ActivityOut>;
  /** Prompts still waiting for an answer. */
  pending: ActivityOut[];
  status: StreamStatus;
  fire: (name: string) => Promise<void>;
  cancel: (name: string) => Promise<void>;
} {
  const rig = useRig();
  const { activities, pending } = useActivityStates();
  const status = useStoreStatus("activities");
  const fire = useCallback(
    async (name: string) => {
      await rig.fireActivity(name);
    },
    [rig],
  );
  const cancel = useCallback(
    async (name: string) => {
      await rig.cancelActivity(name);
    },
    [rig],
  );
  return { activities, pending, status, fire, cancel };
}
