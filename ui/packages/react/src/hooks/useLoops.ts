import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import type { LoopOut } from "@flyball/client";
import { useTelemetry } from "../provider.js";
import type { StreamStatus } from "./useStream.js";
import { emptyLoopView, type LoopView } from "../store/telemetry.js";
import { READOUT_MS, useStoreStatus } from "../store/hooks.js";

/**
 * A loop's recent ticks as parallel arrays, oldest first: `t` in seconds
 * since the epoch, `reference` and `reading` in the channel's unit,
 * `demand`, `expected` and `correction` in the actuator's (`LoopOut.demand_unit`).
 * Null where the loop had no value at that tick, e.g. `expected` for an
 * actuator that returns none, or `reference` while a ramp runs under a
 * feedforward the setpoint cannot be recovered through (see `setpointOf`).
 */
export type LoopTrace = LoopView;

export type LoopTraces = Record<string, LoopTrace>;

/**
 * Every loop: the latest `LoopOut` per name, and a trace of the last
 * `windowS` seconds of reference, reading, demand, expected and correction per loop,
 * so a chart can draw them. Seeded from `GET /api/loops` and, when a
 * session is recording, from its stored ticks -- so a freshly opened page
 * shows the window already full -- then live from `/ws/loops`. An adapter
 * over the telemetry store: the arrays of a loop are rebuilt only when it
 * ticked, at most four times a second.
 */
export function useLoops(windowS = 3600, every?: number): { loops: Record<string, LoopOut>; history: LoopTraces; status: StreamStatus } {
  const store = useTelemetry();
  const held = useRef<{ history: LoopTraces; seen: Map<string, number>; every: number | undefined; windowS: number }>({ history: {}, seen: new Map(), every, windowS });

  useEffect(() => {
    void store.seedLoops(every);
  }, [store, every]);

  const subscribe = useCallback((cb: () => void) => store.subscribeLoop(null, cb, READOUT_MS), [store]);
  useSyncExternalStore(subscribe, () => store.loopVersion());
  const status = useStoreStatus("loops");

  const loops = store.loops();
  const current = held.current;
  if (current.every !== every || current.windowS !== windowS) {
    current.every = every;
    current.windowS = windowS;
    current.history = {};
    current.seen.clear();
  }
  let changed = false;
  const next: LoopTraces = { ...current.history };
  for (const name of Object.keys(loops)) {
    const version = store.loopVersion(name);
    if (name in next && current.seen.get(name) === version) continue;
    const last = store.loop(name)?.reading;
    const fromS = windowS < store.windowS && last ? last.time_ns / 1e9 - windowS : undefined;
    next[name] = store.readLoop(name, emptyLoopView(), { ...(fromS !== undefined ? { fromS } : {}), ...(every && every > 1 ? { every } : {}) });
    current.seen.set(name, version);
    changed = true;
  }
  if (changed) current.history = next;
  return { loops, history: current.history, status };
}
