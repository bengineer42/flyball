import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import type { Address } from "@flyball/client";
import { useTelemetry } from "../provider.js";
import type { StreamStatus } from "./useStream.js";
import { emptyTrace } from "../store/telemetry.js";
import { READOUT_MS, useStoreStatus } from "../store/hooks.js";

/** A signal's recent points: parallel arrays, seconds since the epoch and value, oldest first. */
export interface Trace {
  address: Address;
  t: number[];
  v: number[];
}

/** Traces by signal address. */
export type Traces = Record<Address, Trace>;

const SEP = "\n";

/**
 * Traces for the given signals, as plain arrays: history from the
 * recording store, then live. A compatibility adapter over the telemetry
 * store for panels that take `t`/`v` props -- it copies each signal that
 * moved out of its ring at most four times a second, and the component
 * using it re-renders at that rate. A chart should take a `TraceRef`
 * (`useTraceRef`) instead and never re-render on samples; a value should
 * come from `useSignal`.
 */
export function useTraces(addresses: ReadonlyArray<Address> | undefined, windowS = 3600): { traces: Traces; status: StreamStatus } {
  const store = useTelemetry();
  const keys = addresses ?? [];
  const ident = keys.join(SEP);
  const held = useRef<{ traces: Traces; seen: Map<string, number>; ident: string }>({ traces: {}, seen: new Map(), ident: "" });
  const bump = useRef(0);

  useEffect(() => {
    if (keys.length) void store.seed([...keys]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store, ident]);

  const subscribe = useCallback(
    (cb: () => void) => {
      return store.subscribeTrace(
        ident ? ident.split(SEP) : [],
        () => {
          bump.current++;
          cb();
        },
        READOUT_MS,
      );
    },
    [store, ident],
  );
  // The snapshot is a counter: the arrays are rebuilt below, only for signals that moved.
  useSyncExternalStore(subscribe, () => bump.current);

  const status = useStoreStatus("samples");
  const current = held.current;
  if (current.ident !== ident) {
    current.ident = ident;
    current.traces = {};
    current.seen.clear();
  }
  let changed = false;
  const next: Traces = { ...current.traces };
  for (const address of keys) {
    const version = store.version(address);
    if (address in next && current.seen.get(address) === version) continue;
    const latest = store.latest(address);
    const view = store.read(address, emptyTrace(), windowS < store.windowS && latest ? { fromS: latest.t - windowS } : {});
    next[address] = { address, t: view.t, v: view.v };
    current.seen.set(address, version);
    changed = true;
  }
  if (changed) current.traces = next;
  return { traces: current.traces, status };
}
