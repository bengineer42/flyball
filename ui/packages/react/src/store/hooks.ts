import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from "react";
import { alarmLevel, staleAfterS, type AlarmLevel, type ChannelOut, type DeviceState, type Event, type Freshness, type LoopOut, type ReaderRun } from "@flyball/client";
import { useTelemetry } from "../provider.js";
import { channelKey, type StoreStream, type StreamStatus, type TelemetryStore } from "./telemetry.js";

/** How often a readout, tile or list is allowed to re-render on live data. */
export const READOUT_MS = 250;

/**
 * A channel's newest point, re-rendering only the calling component and at
 * most four times a second however fast the samples come.
 */
export function useLatest(key: string | undefined): { t: number; v: number } | undefined {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => (key === undefined ? () => undefined : store.subscribeLatest(key, cb, READOUT_MS)), [store, key]);
  useSyncExternalStore(subscribe, () => (key === undefined ? 0 : store.version(key)));
  return key === undefined ? undefined : store.latest(key);
}

/** What a chart subscribes to: the store and the channels, by key. */
export interface TraceRef {
  store: TelemetryStore;
  keys: string[];
  /** Bumps when the set of channels changes, so a chart can rebuild. */
  version: number;
}

const NO_CHANNELS: ChannelOut[] = [];

/**
 * A handle a chart draws from directly (`MultiSeries`/`TimeSeries` `source`
 * prop): the same object until the channels change, so nothing re-renders
 * on samples. Asks the store for the channels' history once.
 */
export function useTraceRef(channels: ReadonlyArray<ChannelOut> = NO_CHANNELS): TraceRef {
  const store = useTelemetry();
  const ident = channels.map(channelKey).join("\n");
  const serial = useRef(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const ref = useMemo<TraceRef>(() => ({ store, keys: ident ? ident.split("\n") : [], version: ++serial.current }), [store, ident]);
  useEffect(() => {
    if (channels.length) void store.seed([...channels]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store, ident]);
  return ref;
}

/** The latest state of one loop; re-renders this component only, at most four times a second. */
export function useLoopLatest(name: string | undefined): LoopOut | undefined {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => (name === undefined ? () => undefined : store.subscribeLoop(name, cb, READOUT_MS)), [store, name]);
  useSyncExternalStore(subscribe, () => (name === undefined ? 0 : store.loopVersion(name)));
  useEffect(() => {
    void store.seedLoops();
  }, [store]);
  return name === undefined ? undefined : store.loop(name);
}

/** The latest state of one actuator, from `/ws/actuators`; re-renders this component only. */
export function useActuatorState(name: string | undefined): DeviceState | undefined {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => (name === undefined ? () => undefined : store.subscribeActuators(name, cb, READOUT_MS)), [store, name]);
  useSyncExternalStore(subscribe, () => (name === undefined ? 0 : store.actuatorVersion(name)));
  return name === undefined ? undefined : store.actuator(name);
}

/**
 * The rig's recent events, oldest first, at most `limit` of them and only
 * those `filter` keeps: seeded from `GET /api/events`, then live. The array
 * keeps its identity until an event arrives.
 */
export function useEventsFeed(filter?: (event: Event) => boolean, limit = 500): Event[] {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => store.subscribeEvents(cb, READOUT_MS), [store]);
  const version = useSyncExternalStore(subscribe, () => store.eventsVersionNow());
  useEffect(() => {
    void store.seedEvents(limit);
  }, [store, limit]);
  return useMemo(() => {
    const all = store.events();
    const kept = filter ? all.filter(filter) : all;
    return kept.length > limit ? kept.slice(kept.length - limit) : kept;
    // `version` is the dependency that matters; the array behind it is in the store.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store, version, filter, limit]);
}

/**
 * Every reader's run from `/ws/readers` (one shared socket), keyed by name;
 * a new object at most once a second, since `last_read_ns` moves on every read.
 */
export function useReaderRuns(): Record<string, ReaderRun> {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => store.subscribeReaders(cb, 1000), [store]);
  useSyncExternalStore(subscribe, () => store.readersVersionNow());
  return store.readerRuns();
}

/**
 * The readers' periods by name, for stale thresholds: re-renders only when a
 * period changes, not on every read.
 */
export function useReaderPeriods(): Record<string, number | null> {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => store.subscribeReaders(cb, 1000), [store]);
  const key = useSyncExternalStore(subscribe, () => store.readerPeriodsKey());
  return useMemo(() => {
    const out: Record<string, number | null> = {};
    for (const [name, run] of Object.entries(store.readerRuns())) out[name] = run.period_s;
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store, key]);
}

/** One stream's socket state; `connecting` until someone opens it. */
export function useStoreStatus(stream: StoreStream): StreamStatus {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => store.subscribeStatus(cb), [store]);
  useSyncExternalStore(subscribe, () => store.statusVersionNow());
  const status = store.status()[stream];
  return status === "idle" ? "connecting" : status;
}

/**
 * For the app bar's live chip: the state of every stream the store has
 * opened, and whether the server has recently dropped samples for this
 * client (a `seq` gap).
 */
export function useStreamStatus(): { streams: StreamStatus[]; lagging: boolean } {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => store.subscribeStatus(cb), [store]);
  const version = useSyncExternalStore(subscribe, () => store.statusVersionNow());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => ({ streams: store.openStatuses(), lagging: store.lagging() }), [store, version]);
}

// A shared one-second tick for anything that ages: stale detection needs a clock, not a sample.
const tickers = new Set<() => void>();
let tickerId: ReturnType<typeof setInterval> | null = null;
function subscribeTick(cb: () => void): () => void {
  tickers.add(cb);
  if (tickerId === null) tickerId = setInterval(() => tickers.forEach((t) => t()), 1000);
  return () => {
    tickers.delete(cb);
    if (!tickers.size && tickerId !== null) {
      clearInterval(tickerId);
      tickerId = null;
    }
  };
}

/**
 * The rig's clock in seconds: the newest sample time across every channel,
 * or the wall clock before any sample has arrived. A simulated rig runs its
 * clock ahead of the wall (often far ahead), so this is the right anchor for
 * ageing a timestamp against — not `Date.now()`. Re-renders once a second.
 */
export function useNowS(): number {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => subscribeTick(cb), []);
  useSyncExternalStore(subscribe, () => Math.floor(Date.now() / 1000));
  return store.nowS() ?? Date.now() / 1000;
}

/**
 * Whether a channel is stale, and by how much, in rig time: its newest
 * point against the newest sample anywhere on the rig (a simulated clock
 * runs ahead of the wall, so `Date.now()` would be wrong). Re-renders once
 * a second only while stale, and once when it turns stale or fresh.
 */
export function useFreshness(key: string | undefined, periodS: number | null | undefined): Freshness {
  const store = useTelemetry();
  const subscribe = useCallback(
    (cb: () => void) => {
      const stopTick = subscribeTick(cb);
      const stopKey = key === undefined ? () => undefined : store.subscribeLatest(key, cb, READOUT_MS);
      return () => {
        stopTick();
        stopKey();
      };
    },
    [store, key],
  );
  // The snapshot is the stale age in whole seconds, or -1 while fresh: only that changing re-renders.
  const age = useSyncExternalStore(subscribe, () => {
    const last = key === undefined ? undefined : store.latest(key)?.t;
    const now = store.nowS();
    if (last === undefined || now === null) return -1;
    const ageS = now - last;
    return ageS > staleAfterS(periodS) ? Math.round(ageS) : -1;
  });
  const last = key === undefined ? null : (store.latest(key)?.t ?? null);
  const now = store.nowS();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => ({ periodS, lastSampleS: last, nowS: age >= 0 && last !== null ? last + age : now }), [periodS, age, key]);
}

export type AlarmSummary = Record<AlarmLevel, number>;

/**
 * How many of `channels` are ok, warning, in alarm or stale right now, for
 * a page-level count; re-renders only when a count changes, checked at most
 * once a second.
 */
export function useAlarmSummary(channels: ReadonlyArray<ChannelOut>, periodOf: (channel: ChannelOut) => number | null | undefined): AlarmSummary {
  const store = useTelemetry();
  const ident = channels.map(channelKey).join("\n");
  const periodRef = useRef(periodOf);
  periodRef.current = periodOf;
  const subscribe = useCallback(
    (cb: () => void) => {
      const stopTick = subscribeTick(cb);
      const stopKeys = store.subscribeTrace(ident ? ident.split("\n") : [], cb, 1000);
      return () => {
        stopTick();
        stopKeys();
      };
    },
    [store, ident],
  );
  const snapshot = useSyncExternalStore(subscribe, () => {
    const counts: AlarmSummary = { ok: 0, warn: 0, alarm: 0, stale: 0 };
    const now = store.nowS();
    for (const c of channels) {
      const point = store.latest(channelKey(c));
      counts[alarmLevel(point?.v, c, { periodS: periodRef.current(c), lastSampleS: point?.t ?? null, nowS: now })]++;
    }
    return `${counts.ok}:${counts.warn}:${counts.alarm}:${counts.stale}`;
  });
  return useMemo(() => {
    const [ok, warn, alarm, stale] = snapshot.split(":").map(Number) as [number, number, number, number];
    return { ok, warn, alarm, stale };
  }, [snapshot]);
}
