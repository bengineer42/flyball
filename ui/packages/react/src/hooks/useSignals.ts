import { useCallback } from "react";
import type { SignalState } from "@flyball/client";
import { useRig } from "../provider.js";
import { useQuery, type QueryState } from "./useQuery.js";

/**
 * The rig's signals, polled, since a person answering one is a slow loop.
 * Every blocking step registers one (a hold, an arrival, a wait); `pending`
 * is only those waiting on a *person* -- a program's `wait` -- the ones a UI
 * should put a button in front of. `fire` and `interrupt` answer one and
 * refresh straight away.
 */
export function useSignals(refreshMs = 2000): QueryState<Record<string, SignalState>> & {
  /** Prompts still waiting for an answer. */
  pending: SignalState[];
  fire: (name: string) => Promise<void>;
  interrupt: (name: string) => Promise<void>;
} {
  const rig = useRig();
  const query = useQuery(() => rig.signals(), [rig], { refreshMs });
  const pending = Object.values(query.data ?? {}).filter((s) => s.outcome === "pending" && s.prompt);
  const fire = useCallback(
    async (name: string) => {
      await rig.fireSignal(name);
      query.refresh();
    },
    [rig, query],
  );
  const interrupt = useCallback(
    async (name: string) => {
      await rig.interruptSignal(name);
      query.refresh();
    },
    [rig, query],
  );
  return { ...query, pending, fire, interrupt };
}
