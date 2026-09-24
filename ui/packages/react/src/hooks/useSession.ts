import { useEffect, useState } from "react";
import type { Address, ControllerRow, DeviceRow, RigClient, SessionEvent, SessionRow, SignalRow, Span, Tick, WriteRow, StartRecording } from "@flyball/client";
import { useRig } from "../provider.js";
import { useQuery, type QueryState } from "./useQuery.js";

/** One signal's series over a session, in seconds since the epoch; `v` null where a reading had none (the chart breaks there). */
export interface SessionTrace {
  signal: SignalRow;
  unit: string;
  t: number[];
  v: (number | null)[];
}

/**
 * The session's shell: rows only, nothing that grows with how long it ran or
 * how many signals it recorded. No series, no ticks -- `useSessionSeries` and
 * `useSessionTicks` fetch those lazily, only for what is actually shown.
 */
export interface SessionDetail {
  session: SessionRow;
  devices: DeviceRow[];
  signals: SignalRow[];
  writes: WriteRow[];
  controllers: ControllerRow[];
  events: SessionEvent[];
  spans: Span[];
  /** Wall-clock seconds of the session's start; add `offset_ns / 1e9` to get a point's time. */
  startS: number;
}

/**
 * The session's shell -- rows and metadata, cheap regardless of how much the
 * session recorded. Resolves as soon as the metadata calls land: it never
 * waits on a signal's series or a controller's ticks, so a session with
 * hundreds of signals opens exactly as fast as one with none. Charts fetch
 * their own series lazily (`useSessionSeries`), only for the group actually
 * shown; likewise a controller's ticks (`useSessionTicks`).
 */
export function useSession(id: number | null): QueryState<SessionDetail> {
  const rig = useRig();
  return useQuery(
    async () => {
      if (id === null) throw new Error("no session selected");
      const [session, devices, signals, writes, controllers, events, spans] = await Promise.all([
        rig.session(id),
        rig.sessionDevices(id),
        rig.sessionSignals(id),
        rig.sessionWrites(id),
        rig.sessionControllers(id),
        rig.sessionEvents(id),
        rig.spans(id),
      ]);
      const startS = session.start_ns / 1e9;
      return { session, devices, signals, writes, controllers, events, spans, startS };
    },
    [rig, id],
  );
}

/**
 * Caches for a session's series and ticks, keyed by session id (and, for a
 * series, its address and downsample) so that: (1) two chart instances asking
 * for the same signal share one request, and (2) a chart that unmounts and
 * remounts -- switching the "by unit"/"each signal" view, say, or scrolling a
 * group back into view -- is served from what is already held rather than
 * refetching. Entries are plain resolved/in-flight promises, never evicted:
 * a session's recorded data never changes once written, so nothing here can
 * go stale. Module-level (not per-component-instance) is the point: it is
 * what lets two mounts of the same chart share one fetch.
 */
const seriesCache = new Map<string, Promise<SessionTrace>>();
const ticksCache = new Map<string, Promise<Tick[]>>();

function seriesKey(id: number, address: Address, maxPoints: number): string {
  return `${id}\n${address}\n${maxPoints}`;
}

function fetchSeries(rig: RigClient, id: number, signal: SignalRow, startS: number, maxPoints: number): Promise<SessionTrace> {
  const key = seriesKey(id, signal.address, maxPoints);
  let held = seriesCache.get(key);
  if (!held) {
    held = rig.series(id, signal.address, { max_points: maxPoints }).then((series) => ({
      signal,
      unit: signal.unit,
      t: series.points.map((p) => startS + p.offset_ns / 1e9),
      v: series.points.map((p) => p.value),
    }));
    // A failed fetch must not poison the cache forever -- the next mount (or a manual retry) gets to try again.
    held.catch(() => seriesCache.delete(key));
    seriesCache.set(key, held);
  }
  return held;
}

function fetchTicks(rig: RigClient, id: number, controller: Address): Promise<Tick[]> {
  const key = `${id}\n${controller}`;
  let held = ticksCache.get(key);
  if (!held) {
    held = rig.ticks(id, controller).catch(() => []);
    held.catch(() => ticksCache.delete(key));
    ticksCache.set(key, held);
  }
  return held;
}

export interface SessionSeriesState {
  /** Keyed by signal address; only signals whose fetch has resolved so far. */
  traces: Record<Address, SessionTrace>;
  loading: boolean;
  error: Error | undefined;
}

const EMPTY_SERIES: SessionSeriesState = { traces: {}, loading: false, error: undefined };

/**
 * A group of signals' series for one session, fetched (and cached, see
 * above) only while `enabled` -- pass a group's on-screen state, gated so it
 * latches true rather than refetching every time a chart scrolls back into
 * view. Disabled (the default, before anything is shown) costs nothing: no
 * request, `traces` stays empty.
 */
export function useSessionSeries(id: number | null, signals: readonly SignalRow[], startS: number, enabled: boolean, maxPoints = 1500): SessionSeriesState {
  const rig = useRig();
  const [state, setState] = useState<SessionSeriesState>(EMPTY_SERIES);
  const addrKey = signals.map((s) => s.address).join("\n");

  useEffect(() => {
    if (!enabled || id === null || signals.length === 0) {
      setState(EMPTY_SERIES);
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: undefined }));
    Promise.all(signals.map((signal) => fetchSeries(rig, id, signal, startS, maxPoints))).then(
      (traces) => {
        if (cancelled) return;
        setState({ traces: Object.fromEntries(traces.map((t) => [t.signal.address, t])), loading: false, error: undefined });
      },
      (err: unknown) => {
        if (cancelled) return;
        setState({ traces: {}, loading: false, error: err instanceof Error ? err : new Error(String(err)) });
      },
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rig, id, addrKey, enabled, maxPoints, startS]);

  return state;
}

export interface SessionTicksState {
  ticks: Tick[];
  loading: boolean;
  error: Error | undefined;
}

const EMPTY_TICKS: SessionTicksState = { ticks: [], loading: false, error: undefined };

/** One controller's ticks for a session, fetched (and cached) only while `enabled` -- its section actually shown. */
export function useSessionTicks(id: number | null, controller: Address | null, enabled: boolean): SessionTicksState {
  const rig = useRig();
  const [state, setState] = useState<SessionTicksState>(EMPTY_TICKS);

  useEffect(() => {
    if (!enabled || id === null || controller === null) {
      setState(EMPTY_TICKS);
      return;
    }
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: undefined }));
    fetchTicks(rig, id, controller).then((ticks) => {
      if (cancelled) return;
      setState({ ticks, loading: false, error: undefined });
    });
    return () => {
      cancelled = true;
    };
  }, [rig, id, controller, enabled]);

  return state;
}

/**
 * Whether an element (a chart's own host) has ever been on screen: latches
 * `true` the first time `onScreen` does and stays there, so a chart that has
 * already fetched its data does not refetch just because it scrolled away
 * and back (the cache above would no-op that anyway, but this also skips the
 * IntersectionObserver churn of re-deciding).
 */
export function useEverShown(onScreen: boolean): boolean {
  const [shown, setShown] = useState(onScreen);
  useEffect(() => {
    if (onScreen) setShown(true);
  }, [onScreen]);
  return shown;
}

/** The open session, polled, plus start/end actions. */
export function useRecording(refreshMs = 5000) {
  const rig = useRig();
  const current = useQuery(() => rig.recording(), [rig], { refreshMs });
  return {
    ...current,
    async start(details?: unknown, extra: Omit<StartRecording, "details"> = {}) {
      const s = await rig.startRecording(details === undefined ? { ...extra } : { ...extra, details });
      current.refresh();
      return s;
    },
    async end() {
      const s = await rig.endRecording();
      current.refresh();
      return s;
    },
  };
}
