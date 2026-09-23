import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import type { Address, ControllerOut } from "@flyball/client";
import { useTelemetry } from "../provider.js";
import type { StreamStatus } from "./useStream.js";
import { emptyControllerView, type ControllerView } from "../store/telemetry.js";
import { READOUT_MS, useStoreStatus } from "../store/hooks.js";

/**
 * A controller's recent ticks as parallel arrays, oldest first: `t` in
 * seconds since the epoch, `reference` and `measured` in the measured unit,
 * `output`, `expected` and `correction` in the output's
 * (`ControllerOut.output_unit`). Null where the controller had no value at
 * that tick, e.g. `expected` for a device that returns none, or `reference`
 * while a ramp runs under a feedforward the setpoint cannot be recovered
 * through (see `setpointOf`).
 */
export type ControllerTrace = ControllerView;

/** Traces by controller name (the output's address). */
export type ControllerTraces = Record<Address, ControllerTrace>;

/**
 * Every controller: the latest `ControllerOut` per name, and a trace of the
 * last `windowS` seconds of reference, reading, demand, expected and
 * correction per controller, so a chart can draw them. Seeded from
 * `GET /api/controllers` and, when a session is recording, from its stored
 * ticks -- so a freshly opened page shows the window already full -- then
 * live from `/ws/controllers`. An adapter over the telemetry store: the
 * arrays of a controller are rebuilt only when it ticked, at most four
 * times a second.
 */
export function useControllers(windowS = 3600, every?: number): { controllers: Record<Address, ControllerOut>; history: ControllerTraces; status: StreamStatus } {
  const store = useTelemetry();
  const held = useRef<{ history: ControllerTraces; seen: Map<string, number>; every: number | undefined; windowS: number }>({ history: {}, seen: new Map(), every, windowS });

  useEffect(() => {
    void store.seedControllers(every);
  }, [store, every]);

  const subscribe = useCallback((cb: () => void) => store.subscribeController(null, cb, READOUT_MS), [store]);
  useSyncExternalStore(subscribe, () => store.controllerVersion());
  const status = useStoreStatus("controllers");

  const controllers = store.controllers();
  const current = held.current;
  if (current.every !== every || current.windowS !== windowS) {
    current.every = every;
    current.windowS = windowS;
    current.history = {};
    current.seen.clear();
  }
  let changed = false;
  const next: ControllerTraces = { ...current.history };
  for (const name of Object.keys(controllers)) {
    const version = store.controllerVersion(name);
    if (name in next && current.seen.get(name) === version) continue;
    const last = store.controller(name)?.measured;
    const fromS = windowS < store.windowS && last ? last.time_ns / 1e9 - windowS : undefined;
    next[name] = store.readController(name, emptyControllerView(), {
      ...(fromS !== undefined ? { fromS } : {}),
      ...(every && every > 1 ? { every } : {}),
      spanS: windowS,
    });
    current.seen.set(name, version);
    changed = true;
  }
  if (changed) current.history = next;
  return { controllers, history: current.history, status };
}
