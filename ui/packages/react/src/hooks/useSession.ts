import type { ActuatorRow, ChannelRow, LoopRow, SessionEvent, SessionRow, Span, Tick } from "@flyball/client";
import { useRig } from "../provider.js";
import { useQuery, type QueryState } from "./useQuery.js";

/** One channel's series over a session, in seconds since the epoch. */
export interface SessionTrace {
  channel: ChannelRow;
  unit: string;
  t: number[];
  v: number[];
}

export interface SessionDetail {
  session: SessionRow;
  channels: ChannelRow[];
  actuators: ActuatorRow[];
  loops: LoopRow[];
  events: SessionEvent[];
  spans: Span[];
  /** Keyed `source.measurand`, downsampled to `maxPoints`. */
  traces: Record<string, SessionTrace>;
  /** Ticks per loop, keyed by loop name. */
  ticks: Record<string, Tick[]>;
  /** Wall-clock seconds of the session's start; add `offset_ns / 1e9` to get a point's time. */
  startS: number;
}

/**
 * Everything the store holds about one session, fetched together: rows,
 * every channel's series (downsampled), every loop's ticks, events, spans.
 */
export function useSession(id: number | null, maxPoints = 1500): QueryState<SessionDetail> {
  const rig = useRig();
  return useQuery(
    async () => {
      if (id === null) throw new Error("no session selected");
      const [session, channels, actuators, loops, events, spans] = await Promise.all([
        rig.session(id),
        rig.sessionChannels(id),
        rig.sessionActuators(id),
        rig.sessionLoops(id),
        rig.sessionEvents(id),
        rig.spans(id),
      ]);
      const startS = session.start_ns / 1e9;
      const traces: Record<string, SessionTrace> = {};
      await Promise.all(
        channels.map(async (c) => {
          const series = await rig.series(id, c.source.name, c.measurand.name, { max_points: maxPoints });
          traces[`${c.source.name}.${c.measurand.name}`] = {
            channel: c,
            unit: c.measurand.unit,
            t: series.points.map((p) => startS + p.offset_ns / 1e9),
            v: series.points.map((p) => p.value),
          };
        }),
      );
      const ticks: Record<string, Tick[]> = {};
      await Promise.all(
        loops.map(async (l, i) => {
          const name = l.name ?? l.actuator.name ?? String(i);
          ticks[name] = await rig.ticks(id, name).catch(() => []);
        }),
      );
      return { session, channels, actuators, loops, events, spans, traces, ticks, startS };
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
