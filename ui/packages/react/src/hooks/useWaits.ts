import { useCallback } from "react";
import type { WaitState } from "@flyball/client";
import { useRig } from "../provider.js";
import type { StreamStatus } from "./useStream.js";
import { useStoreStatus, useWaitStates } from "../store/hooks.js";

/**
 * What the rig is waiting on, live from `/ws/waits` through the telemetry
 * store. Every blocking program step registers one (a hold, an arrival, a
 * wait); `pending` is only those waiting on a *person* -- a program's
 * `wait` -- the ones a UI should put a button in front of. `fire` and
 * `interrupt` answer one; the socket carries the settlement back.
 */
export function useWaits(): {
  waits: Record<string, WaitState>;
  /** Prompts still waiting for an answer. */
  pending: WaitState[];
  status: StreamStatus;
  fire: (name: string) => Promise<void>;
  interrupt: (name: string) => Promise<void>;
} {
  const rig = useRig();
  const { waits, pending } = useWaitStates();
  const status = useStoreStatus("waits");
  const fire = useCallback(
    async (name: string) => {
      await rig.fireWait(name);
    },
    [rig],
  );
  const interrupt = useCallback(
    async (name: string) => {
      await rig.interruptWait(name);
    },
    [rig],
  );
  return { waits, pending, status, fire, interrupt };
}
