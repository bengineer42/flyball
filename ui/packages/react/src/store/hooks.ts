import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from "react";
import { alarmLevel, type Address, type ActivityOut, type AlarmLevel, type Condition, type ControllerOut, type DeviceRunOut, type Event, type SampleOut, type SignalOut, type Value, type WriteOut } from "@flyball/client";
import { useTelemetry } from "../provider.js";
import type { SignalReading, SocketStream, StoreStream, StreamStatus, TelemetryStore } from "./telemetry.js";

/** How often a readout, tile or list is allowed to re-render on live data. */
export const READOUT_MS = 250;

/**
 * A signal's newest point (`t` seconds since the epoch on the rig's clock,
 * `v` in the signal's unit, null when that reading had none -- `useReading`
 * says why), re-rendering only the calling component and
 * at most four times a second however fast the samples come. Only a
 * publishing signal ever has one; a setting is read through `useRig().read`.
 */
export function useSignal(address: Address | undefined): { t: number; v: number | null } | undefined {
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
 * A signal's newest reading with its quality: `value` null with `quality`/`reason` saying why,
 * the `caveats` on a usable value, the last usable value while there is none. Re-renders only
 * the calling component, at most four times a second.
 */
export function useReading(address: Address | undefined): SignalReading | undefined {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => (address === undefined ? () => undefined : store.subscribeLatest(address, cb, READOUT_MS)), [store, address]);
  useSyncExternalStore(subscribe, () => (address === undefined ? 0 : store.version(address)));
  return address === undefined ? undefined : store.reading(address);
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

/** The latest state of one controller, by its name (the output's address); re-renders this component only, at most four times a second. */
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
 * Every activity the rig has reported, by name, and the ones still waiting on a
 * person (`pending`): from `/ws/activities`. Settled activities stay until the page
 * reloads; `pending` is what a UI puts a button in front of.
 */
export function useActivityStates(): { activities: Record<string, ActivityOut>; pending: ActivityOut[] } {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => store.subscribeActivities(cb, READOUT_MS), [store]);
  const version = useSyncExternalStore(subscribe, () => store.activitiesVersionNow());
  return useMemo(() => {
    const activities = store.activities();
    return { activities, pending: Object.values(activities).filter((w) => w.outcome === "pending" && w.prompt) };
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
export function useStreamStatus(): { streams: StreamStatus[]; byStream: Readonly<Record<SocketStream, StreamStatus | "idle">> } {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => store.subscribeStatus(cb), [store]);
  const version = useSyncExternalStore(subscribe, () => store.statusVersionNow());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => ({ streams: store.openStatuses(), byStream: store.status() }), [store, version]);
}

// A shared one-second tick for the rig's clock (`useNowS`).
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
 * or the rig's own clock carried forward from `/api/clock`, or the wall
 * clock before either is known. A simulated rig runs its clock ahead of the
 * wall (often far ahead), so this is the right anchor for a timestamp's age
 * or a ramp's end -- not `Date.now()`. Re-renders once a second. Staleness is
 * not judged against it: the rig pushes a `stale` reading itself.
 */
export function useNowS(): number {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => subscribeTick(cb), []);
  useSyncExternalStore(subscribe, () => Math.floor(Date.now() / 1000));
  return store.nowS() ?? Date.now() / 1000;
}

/** Keep the rig's conditions read (`/api/health` now and every 30 s, the events' edges between) while mounted with `on`. */
function useSeededConditions(on: boolean): void {
  const store = useTelemetry();
  useEffect(() => {
    if (!on) return;
    store.seedBands();
    const id = window.setInterval(() => store.seedBands(), 30_000);
    return () => window.clearInterval(id);
  }, [store, on]);
}

/**
 * The rig's band condition on a signal (`"ok"`, `"warn"`, `"alarm"`, `"unknown"` for `band_unknown`),
 * kept live from its conditions; `undefined` until the rig's conditions have been read once. Pass it
 * to `alarmLevel` so a tile shows what the rig says rather than judging the value against the bands itself.
 */
export function useBandLevel(address: Address | undefined): "ok" | "warn" | "alarm" | "unknown" | undefined {
  const store = useTelemetry();
  useSeededConditions(address !== undefined);
  const subscribe = useCallback((cb: () => void) => (address === undefined ? () => undefined : store.subscribeEvents(cb, 250)), [store, address]);
  return useSyncExternalStore(subscribe, () => (address === undefined ? undefined : store.bandOf(address)));
}

const NO_CONDITIONS: Condition[] = [];

/** Every condition the rig holds on `subject` (a controller's `frozen`, a signal's band), kept live; empty until read. */
export function useConditions(subject: string | undefined): Condition[] {
  const store = useTelemetry();
  useSeededConditions(subject !== undefined);
  const subscribe = useCallback((cb: () => void) => (subject === undefined ? () => undefined : store.subscribeEvents(cb, 250)), [store, subject]);
  return useSyncExternalStore(subscribe, () => (subject === undefined ? NO_CONDITIONS : store.conditionsOf(subject)));
}

export type AlarmSummary = Record<AlarmLevel, number>;

/**
 * How many of `signals` are ok, warning, in alarm, of unknown band or stale right now, for a
 * page-level count: the rig's band conditions and the qualities of their newest readings;
 * re-renders only when a count changes, checked at most once a second.
 */
export function useAlarmSummary(signals: ReadonlyArray<Pick<SignalOut, "address" | "warning" | "alarm">>): AlarmSummary {
  const store = useTelemetry();
  const ident = signals.map((s) => s.address).join("\n");
  const subscribe = useCallback(
    (cb: () => void) => {
      const stopKeys = store.subscribeTrace(ident ? ident.split("\n") : [], cb, 1000);
      // The rig's band alarms: read once whole, then kept by the events' edges.
      const stopBands = ident ? store.subscribeEvents(cb, 1000) : () => undefined;
      if (ident) store.seedBands();
      return () => {
        stopKeys();
        stopBands();
      };
    },
    [store, ident],
  );
  const snapshot = useSyncExternalStore(subscribe, () => {
    const counts: AlarmSummary = { ok: 0, warn: 0, alarm: 0, unknown: 0, stale: 0 };
    for (const s of signals) {
      const r = store.reading(s.address);
      counts[alarmLevel(typeof r?.value === "number" ? r.value : null, s, store.bandOf(s.address), r?.quality)]++;
    }
    return `${counts.ok}:${counts.warn}:${counts.alarm}:${counts.unknown}:${counts.stale}`;
  });
  return useMemo(() => {
    const [ok, warn, alarm, unknown, stale] = snapshot.split(":").map(Number) as [number, number, number, number, number];
    return { ok, warn, alarm, unknown, stale };
  }, [snapshot]);
}
