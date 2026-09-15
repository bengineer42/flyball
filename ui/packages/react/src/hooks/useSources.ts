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
      const [session] = await rig.sessions(1);
      if (!session || session.end_ns !== null) return; // nothing open: no history to seed from
      const startS = session.start_ns / 1e9;
      const seeded: Traces = {};
      await Promise.all(
        sources.flatMap((s) =>
          s.channels.map(async (c) => {
            const series = await rig.series(session.id, c.source, c.measurand, {
              start_ns: Math.max(0, Date.now() * 1e6 - session.start_ns - windowS * 1e9),
              max_points: 2000,
            });
            seeded[channelKey(c)] = {
              channel: c,
              t: series.points.map((p) => startS + p.offset_ns / 1e9),
              v: series.points.map((p) => p.value),
            };
          }),
        ),
      );
      if (!controller.signal.aborted) setHistory(seeded);
    })().catch(() => undefined); // no store, or a closed session: live-only is fine
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
