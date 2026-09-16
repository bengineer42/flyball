import type { Address, ControllerRow, DeviceRow, SessionEvent, SessionRow, SignalRow, Span, Tick, WriteRow } from "@flyball/client";
import { useRig } from "../provider.js";
import { useQuery, type QueryState } from "./useQuery.js";

/** One signal's series over a session, in seconds since the epoch. */
export interface SessionTrace {
  signal: SignalRow;
  unit: string;
  t: number[];
  v: number[];
}

export interface SessionDetail {
  session: SessionRow;
  devices: DeviceRow[];
  signals: SignalRow[];
  writes: WriteRow[];
  controllers: ControllerRow[];
  events: SessionEvent[];
  spans: Span[];
  /** Keyed by signal address, downsampled to `maxPoints`. */
  traces: Record<Address, SessionTrace>;
  /** Ticks per controller, keyed by its name (the target's address). */
  ticks: Record<Address, Tick[]>;
  /** Wall-clock seconds of the session's start; add `offset_ns / 1e9` to get a point's time. */
  startS: number;
}

/**
 * Everything the store holds about one session, fetched together: rows,
 * every signal's series (downsampled), every controller's ticks, events, spans.
 */
export function useSession(id: number | null, maxPoints = 1500): QueryState<SessionDetail> {
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
      const traces: Record<Address, SessionTrace> = {};
      await Promise.all(
        signals.map(async (signal) => {
          const series = await rig.series(id, signal.address, { max_points: maxPoints });
          traces[signal.address] = {
            signal,
            unit: signal.unit,
            t: series.points.map((p) => startS + p.offset_ns / 1e9),
            v: series.points.map((p) => p.value),
          };
        }),
      );
      const ticks: Record<Address, Tick[]> = {};
      await Promise.all(
        controllers.map(async (c) => {
          ticks[c.name] = await rig.ticks(id, c.name).catch(() => []);
        }),
      );
      return { session, devices, signals, writes, controllers, events, spans, traces, ticks, startS };
    },
    [rig, id, maxPoints],
  );
}

/** The open session, polled, plus start/end actions. */
export function useRecording(refreshMs = 5000) {
  const rig = useRig();
  const current = useQuery(() => rig.recording(), [rig], { refreshMs });
  return {
    ...current,
    async start(details?: unknown) {
      const s = await rig.startRecording(details === undefined ? {} : { details });
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
