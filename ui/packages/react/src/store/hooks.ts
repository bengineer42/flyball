import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from "react";
import { alarmLevel, deviceOf, staleAfterS, type Address, type AlarmLevel, type ControllerOut, type DeviceRunOut, type Event, type Freshness, type SampleOut, type SignalOut, type Value, type WaitState, type WriteOut } from "@flyball/client";
import { useTelemetry } from "../provider.js";
import type { StoreStream, StreamStatus, TelemetryStore } from "./telemetry.js";

/** How often a readout, tile or list is allowed to re-render on live data. */
export const READOUT_MS = 250;

/**
 * A signal's newest point (`t` seconds since the epoch on the rig's clock,
 * `v` in the signal's unit), re-rendering only the calling component and
 * at most four times a second however fast the samples come. Only a
 * publishing signal ever has one; a setting is read through `useRig().read`.
 */
export function useSignal(address: Address | undefined): { t: number; v: number } | undefined {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => (address === undefined ? () => undefined : store.subscribeLatest(address, cb, READOUT_MS)), [store, address]);
  useSyncExternalStore(subscribe, () => (address === undefined ? 0 : store.version(address)));
  return address === undefined ? undefined : store.latest(address);
}

/**
 * A signal's newest value whatever its dtype (a number, a bool, a string --
 * an enum's value -- or JSON), re-rendering only the calling component and
 * at most four times a second. A number is also on `useSignal`'s ring; a
 * bool/str/json signal only ever has one here.
 */
export function useLatestValue(address: Address | undefined): { t: number; value: Value } | undefined {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => (address === undefined ? () => undefined : store.subscribeLatest(address, cb, READOUT_MS)), [store, address]);
  useSyncExternalStore(subscribe, () => (address === undefined ? 0 : store.version(address)));
  return address === undefined ? undefined : store.latestValue(address);
}

/**
 * The newest sample of a node (a device, or an atomic namespace), its
 * `values` keyed relative to the node; the same object until the node
 * delivers again, at most four times a second.
 */
export function useSample(node: Address | undefined): SampleOut | undefined {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => (node === undefined ? () => undefined : store.subscribeSample(node, cb, READOUT_MS)), [store, node]);
  useSyncExternalStore(subscribe, () => (node === undefined ? 0 : store.sampleVersion(node)));
  return node === undefined ? undefined : store.sample(node);
}

/** What a chart subscribes to: the store and the signals, by address. */
export interface TraceRef {
  store: TelemetryStore;
  keys: Address[];
  /** Bumps when the set of signals changes, so a chart can rebuild. */
  version: number;
}

const NO_ADDRESSES: Address[] = [];

/**
 * A handle a chart draws from directly (`MultiSeries`/`TimeSeries` `source`
 * prop): the same object until the addresses change, so nothing re-renders
 * on samples. Asks the store for the signals' history once.
 */
export function useTraceRef(addresses: ReadonlyArray<Address> = NO_ADDRESSES): TraceRef {
  const store = useTelemetry();
  const ident = addresses.join("\n");
  const serial = useRef(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const ref = useMemo<TraceRef>(() => ({ store, keys: ident ? ident.split("\n") : [], version: ++serial.current }), [store, ident]);
  useEffect(() => {
    if (addresses.length) void store.seed([...addresses]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store, ident]);
  return ref;
}

/** The latest write state of one writable signal, from `/ws/writes`; re-renders this component only. */
export function useWriteState(address: Address | undefined): WriteOut | undefined {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => (address === undefined ? () => undefined : store.subscribeWrites(address, cb, READOUT_MS)), [store, address]);
  useSyncExternalStore(subscribe, () => (address === undefined ? 0 : store.writeVersion(address)));
  return address === undefined ? undefined : store.write(address);
}

/** Every writable signal's latest write state, by address; the object keeps its identity until one changes. */
export function useWriteStates(): Record<Address, WriteOut> {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => store.subscribeWrites(null, cb, READOUT_MS), [store]);
  useSyncExternalStore(subscribe, () => store.writeVersion());
  return store.writes();
}

/** The latest state of one controller, by its name (the target's address); re-renders this component only, at most four times a second. */
export function useController(name: Address | undefined): ControllerOut | undefined {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => (name === undefined ? () => undefined : store.subscribeController(name, cb, READOUT_MS)), [store, name]);
  useSyncExternalStore(subscribe, () => (name === undefined ? 0 : store.controllerVersion(name)));
  useEffect(() => {
    void store.seedControllers();
  }, [store]);
  return name === undefined ? undefined : store.controller(name);
}

/**
 * One polled device's run from `/ws/devices`: period, running, last read,
 * the runtime's conditions and the device's own state. Re-renders this
 * component only, at most once a second (a run moves on every read).
 */
export function useDeviceRun(name: string | undefined): DeviceRunOut | undefined {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => (name === undefined ? () => undefined : store.subscribeDevices(name, cb, 1000)), [store, name]);
  useSyncExternalStore(subscribe, () => (name === undefined ? 0 : store.deviceVersion(name)));
  return name === undefined ? undefined : store.deviceRun(name);
}

/**
 * Every polled device's run from `/ws/devices` (one shared socket), keyed
 * by name; a new object at most once a second, since `last_read_ns` moves
 * on every read.
 */
export function useDeviceRuns(): Record<string, DeviceRunOut> {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => store.subscribeDevices(null, cb, 1000), [store]);
  useSyncExternalStore(subscribe, () => store.deviceVersion());
  return store.deviceRuns();
}

/**
 * Every wait the rig has reported, by name, and the ones still waiting on a
 * person (`pending`): from `/ws/waits`. Settled waits stay until the page
 * reloads; `pending` is what a UI puts a button in front of.
 */
export function useWaitStates(): { waits: Record<string, WaitState>; pending: WaitState[] } {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => store.subscribeWaits(cb, READOUT_MS), [store]);
  const version = useSyncExternalStore(subscribe, () => store.waitsVersionNow());
  return useMemo(() => {
    const waits = store.waits();
    return { waits, pending: Object.values(waits).filter((w) => w.outcome === "pending" && w.prompt) };
    // `version` is the dependency that matters; the object behind it is in the store.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store, version]);
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

/** One stream's socket state; `connecting` until someone opens it. `writes`/`devices` share `samples`'s socket. */
export function useStoreStatus(stream: StoreStream): StreamStatus {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => store.subscribeStatus(cb), [store]);
  useSyncExternalStore(subscribe, () => store.statusVersionNow());
  const socket = stream === "writes" || stream === "devices" ? "samples" : stream;
  const status = store.status()[socket];
  return status === "idle" ? "connecting" : status;
}

/** For the app bar's live chip: the state of every stream the store has opened. */
export function useStreamStatus(): { streams: StreamStatus[] } {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => store.subscribeStatus(cb), [store]);
  const version = useSyncExternalStore(subscribe, () => store.statusVersionNow());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => ({ streams: store.openStatuses() }), [store, version]);
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
 * The rig's clock in seconds: the newest sample time across every signal,
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
 * Whether a signal is stale, and by how much, in rig time: its newest
 * point against the newest sample anywhere on the rig (a simulated clock
 * runs ahead of the wall, so `Date.now()` would be wrong). The threshold is
 * `max(3 × period, 5 s)` with the period of the signal's device from
 * `/ws/devices` (`DeviceOut.run.period_s`), unless `periodS` is given.
 * Re-renders once a second only while stale, and once when it turns stale
 * or fresh.
 */
export function useFreshness(address: Address | undefined, periodS?: number | null): Freshness {
  const store = useTelemetry();
  const device = address === undefined ? undefined : deviceOf(address);
  const subscribe = useCallback(
    (cb: () => void) => {
      const stopTick = subscribeTick(cb);
      const stopKey = address === undefined ? () => undefined : store.subscribeLatest(address, cb, READOUT_MS);
      const stopRun = periodS !== undefined || device === undefined ? () => undefined : store.subscribeDevices(device, cb, 1000);
      return () => {
        stopTick();
        stopKey();
        stopRun();
      };
    },
    [store, address, device, periodS],
  );
  const period = () => (periodS !== undefined ? periodS : address === undefined ? undefined : store.periodOf(address));
  // The snapshot is the stale age in whole seconds, or -1 while fresh: only that changing re-renders.
  const age = useSyncExternalStore(subscribe, () => {
    const last = address === undefined ? undefined : store.latest(address)?.t;
    const now = store.nowS();
    if (last === undefined || now === null) return -1;
    const ageS = now - last;
    return ageS > staleAfterS(period()) ? Math.round(ageS) : -1;
  });
  const last = address === undefined ? null : (store.latest(address)?.t ?? null);
  const now = store.nowS();
  const resolved = period() ?? null;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => ({ periodS: resolved, lastSampleS: last, nowS: age >= 0 && last !== null ? last + age : now }), [resolved, age, address]);
}

export type AlarmSummary = Record<AlarmLevel, number>;

/**
 * How many of `signals` are ok, warning, in alarm or stale right now, for
 * a page-level count, the stale threshold from each signal's device
 * (`/ws/devices`); re-renders only when a count changes, checked at most
 * once a second.
 */
export function useAlarmSummary(signals: ReadonlyArray<Pick<SignalOut, "address" | "warn" | "alarm">>): AlarmSummary {
  const store = useTelemetry();
  const ident = signals.map((s) => s.address).join("\n");
  const subscribe = useCallback(
    (cb: () => void) => {
      const stopTick = subscribeTick(cb);
      const stopKeys = store.subscribeTrace(ident ? ident.split("\n") : [], cb, 1000);
      const stopRuns = ident ? store.subscribeDevices(null, cb, 1000) : () => undefined;
      return () => {
        stopTick();
        stopKeys();
        stopRuns();
      };
    },
    [store, ident],
  );
  const snapshot = useSyncExternalStore(subscribe, () => {
    const counts: AlarmSummary = { ok: 0, warn: 0, alarm: 0, stale: 0 };
    const now = store.nowS();
    for (const s of signals) {
      const point = store.latest(s.address);
      counts[alarmLevel(point?.v, s, { periodS: store.periodOf(s.address), lastSampleS: point?.t ?? null, nowS: now })]++;
    }
    return `${counts.ok}:${counts.warn}:${counts.alarm}:${counts.stale}`;
  });
  return useMemo(() => {
    const [ok, warn, alarm, stale] = snapshot.split(":").map(Number) as [number, number, number, number];
    return { ok, warn, alarm, stale };
  }, [snapshot]);
}
