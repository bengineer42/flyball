import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import type { ChannelOut, SourceOut } from "@flyball/client";
import { useRig, useTelemetry } from "../provider.js";
import { useQuery, type QueryState } from "./useQuery.js";
import type { StreamStatus } from "./useStream.js";
import { channelKey, emptyTrace } from "../store/telemetry.js";
import { READOUT_MS, useStoreStatus } from "../store/hooks.js";

export { channelKey };

/** `GET /api/sources`: every source with its channels (unit, range, precision) and latest sample. */
export function useSources(): QueryState<SourceOut[]> {
  const rig = useRig();
  return useQuery(() => rig.sources(), [rig]);
}

/** A channel's recent points: parallel arrays, seconds since the epoch and value, oldest first. */
export interface Trace {
  channel: ChannelOut;
  t: number[];
  v: number[];
}

export type Traces = Record<string, Trace>;

const SEP = "\n";

/**
 * Traces for every channel of the given sources, as plain arrays: history
 * from the recording store, then live. A compatibility adapter over the
 * telemetry store for panels that take `t`/`v` props -- it copies each
 * channel that moved out of its ring at most four times a second, and the
 * component using it re-renders at that rate. A chart should take a
 * `TraceRef` (`useTraceRef`) instead and never re-render on samples; a
 * value should come from `useLatest`.
 */
export function useSamples(sources: SourceOut[] | undefined, windowS = 3600): { traces: Traces; status: StreamStatus } {
  const store = useTelemetry();
  const channels = (sources ?? []).flatMap((s) => s.channels);
  const ident = channels.map(channelKey).join(SEP);
  const held = useRef<{ traces: Traces; seen: Map<string, number>; ident: string }>({ traces: {}, seen: new Map(), ident: "" });
  const bump = useRef(0);

  useEffect(() => {
    if (channels.length) void store.seed(channels);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store, ident]);

  const subscribe = useCallback(
    (cb: () => void) => {
      const keys = ident ? ident.split(SEP) : [];
      return store.subscribeTrace(
        keys,
        () => {
          bump.current++;
          cb();
        },
        READOUT_MS,
      );
    },
    [store, ident],
  );
  // The snapshot is a counter: the arrays are rebuilt below, only for channels that moved.
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
  for (const c of channels) {
    const key = channelKey(c);
    const version = store.version(key);
    const have = next[key];
    if (have && have.channel === c && current.seen.get(key) === version) continue;
    const latest = store.latest(key);
    const view = store.read(key, emptyTrace(), windowS < store.windowS && latest ? { fromS: latest.t - windowS } : {});
    next[key] = { channel: c, t: view.t, v: view.v };
    current.seen.set(key, version);
    changed = true;
  }
  if (changed) current.traces = next;
  return { traces: current.traces, status };
}
