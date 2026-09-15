import { useEffect, useRef, useState } from "react";
import type { ChannelOut, SourceOut } from "@flyball/client";
import { useRig } from "../provider.js";
import { useQuery, type QueryState } from "./useQuery.js";
import { useStream } from "./useStream.js";

/** `GET /api/sources`: every source with its channels (unit, range, precision) and latest sample. */
export function useSources(): QueryState<SourceOut[]> {
  const rig = useRig();
  return useQuery(() => rig.sources(), [rig]);
}

export const channelKey = (c: { source: string; measurand: string }) => `${c.source}.${c.measurand}`;

/** A channel's recent points: parallel arrays, seconds since the epoch and value, oldest first. */
export interface Trace {
  channel: ChannelOut;
  t: number[];
  v: number[];
}

export type Traces = Record<string, Trace>;

/** Trim to the last `windowS` seconds and append one point. */
function push(trace: Trace, time: number, value: number, windowS: number): Trace {
  const cutoff = time - windowS;
  let start = 0;
  while (start < trace.t.length && trace.t[start]! < cutoff) start++;
  return { channel: trace.channel, t: [...trace.t.slice(start), time], v: [...trace.v.slice(start), value] };
}

/**
 * Traces for every channel of the given sources: the last `windowS` seconds
 * from the open recording session (so a fresh page has history), then live
 * from `/ws/samples`. Points that arrive before the history has loaded are
 * merged in order, so the seam has no duplicates or gaps.
 */
export function useSamples(sources: SourceOut[] | undefined, windowS = 300): { traces: Traces; status: string } {
  const rig = useRig();
  const index = useRef<Array<{ source: SourceOut }>>([]);
  index.current = (sources ?? []).map((s) => ({ source: s }));
  const [history, setHistory] = useState<Traces | null>(null);

  useEffect(() => {
    if (!sources?.length) return;
    const controller = new AbortController();
    (async () => {
      // Sessions newest first until the window is covered; a channel is the
      // same channel in every session it was recorded in, so a restart or a
      // new recording does not blank the chart. Rig time, not wall time.
      const [sessions, clock] = await Promise.all([rig.sessions(20), rig.clock()]);
      const nowS = clock.now_ns / 1e9;
      const horizonS = nowS - windowS;
      const parts: Record<string, Array<{ t: number[]; v: number[] }>> = {};
      let floorS = Number.POSITIVE_INFINITY; // see useLoops: sessions must not overlap on the axis
      for (const session of sessions) {
        const startS = session.start_ns / 1e9;
        const endS = session.end_ns === null ? nowS : session.end_ns / 1e9;
        if (endS > floorS) continue;
        floorS = startS;
        if (endS < horizonS) break;
        const startOffset = Math.max(0, Math.floor((horizonS - startS) * 1e9));
        await Promise.all(
          sources.flatMap((s) =>
            s.channels.map(async (c) => {
              const series = await rig
                .series(session.id, c.source, c.measurand, { start_ns: startOffset, max_points: 2000 })
                .catch(() => null);
              if (!series || !series.points.length) return;
              (parts[channelKey(c)] ??= []).push({
                t: series.points.map((p) => startS + p.offset_ns / 1e9),
                v: series.points.map((p) => p.value),
              });
            }),
          ),
        );
      }
      const seeded: Traces = {};
      for (const s of sources) {
        for (const c of s.channels) {
          const chunks = (parts[channelKey(c)] ?? []).sort((a, b) => a.t[0]! - b.t[0]!);
          seeded[channelKey(c)] = { channel: c, t: chunks.flatMap((k) => k.t), v: chunks.flatMap((k) => k.v) };
        }
      }
      if (!controller.signal.aborted) setHistory(seeded);
    })().catch(() => undefined); // no store: live-only is fine
    return () => controller.abort();
  }, [rig, sources, windowS]);

  const seed = (): Traces => {
    const traces: Traces = {};
    for (const s of sources ?? []) {
      for (const c of s.channels) {
        const t: number[] = [];
        const v: number[] = [];
        if (s.latest && c.measurand in s.latest.values) {
          t.push(s.latest.time_ns / 1e9);
          v.push(s.latest.values[c.measurand]!);
        }
        traces[channelKey(c)] = { channel: c, t, v };
      }
    }
    return traces;
  };

  const { state: live, status } = useStream("samples", seed(), (traces, sample) => {
    const owner = index.current.find((e) => e.source.name === sample.source);
    if (!owner) return traces;
    const next = { ...traces };
    const time = sample.time_ns / 1e9;
    for (const c of owner.source.channels) {
      const value = sample.values[c.measurand];
      if (value === undefined) continue;
      const key = channelKey(c);
      next[key] = push(next[key] ?? { channel: c, t: [], v: [] }, time, value, windowS);
    }
    return next;
  });

  // History first, then whatever live points are newer than its last one.
  const traces: Traces = history
    ? Object.fromEntries(
        Object.entries(live).map(([key, l]) => {
          const h = history[key];
          if (!h || !h.t.length) return [key, l];
          const last = h.t[h.t.length - 1]!;
          let i = 0;
          while (i < l.t.length && l.t[i]! <= last) i++;
          return [key, { channel: l.channel, t: [...h.t, ...l.t.slice(i)], v: [...h.v, ...l.v.slice(i)] }];
        }),
      )
    : live;

  return { traces, status };
}
