import { useEffect, useState } from "react";
import type { LoopOut } from "@flyball/client";
import { useRig } from "../provider.js";
import { useStream, type StreamStatus } from "./useStream.js";

/**
 * A loop's recent ticks as parallel arrays, oldest first: `t` in seconds
 * since the epoch, the rest in the loop's own units (`demand` and
 * `expected` in the actuator's demand unit). Null where the loop had no
 * value at that tick, e.g. `expected` for an actuator that returns none.
 */
export interface LoopTrace {
  t: number[];
  reference: (number | null)[];
  reading: (number | null)[];
  demand: (number | null)[];
  expected: (number | null)[];
}

export type LoopTraces = Record<string, LoopTrace>;

const empty = (): LoopTrace => ({ t: [], reference: [], reading: [], demand: [], expected: [] });

/**
 * Trim to the last `windowS` seconds and append the loop's current values.
 * A tick at the same time as the last point replaces it (the socket resends
 * every loop on connect), so a reconnect adds no duplicate.
 */
function push(trace: LoopTrace, loop: LoopOut, windowS: number): LoopTrace {
  const time = loop.reading ? loop.reading.time_ns / 1e9 : Date.now() / 1000;
  const cutoff = time - windowS;
  let start = 0;
  while (start < trace.t.length && trace.t[start]! < cutoff) start++;
  let end = trace.t.length;
  if (end > start && trace.t[end - 1] === time) end--;
  const slice = <T>(a: T[], value: T) => [...a.slice(start, end), value];
  return {
    t: slice(trace.t, time),
    reference: slice(trace.reference, loop.reference),
    reading: slice(trace.reading, loop.reading ? loop.reading.value : null),
    demand: slice(trace.demand, loop.demand),
    expected: slice(trace.expected, loop.expected),
  };
}

interface Held {
  loops: Record<string, LoopOut>;
  history: LoopTraces;
}

function fold(held: Held, loops: LoopOut[], windowS: number): Held {
  const next: Held = { loops: { ...held.loops }, history: { ...held.history } };
  for (const loop of loops) {
    next.loops[loop.name] = loop;
    next.history[loop.name] = push(next.history[loop.name] ?? empty(), loop, windowS);
  }
  return next;
}

/**
 * Every loop: the latest `LoopOut` per name, and a trace of the last
 * `windowS` seconds of reference, reading, demand and expected per loop,
 * so a chart can draw them. Seeded from `GET /api/loops`, then live from
 * `/ws/loops`. Points are held only from when the page opened.
 */
export function useLoops(windowS = 3600): { loops: Record<string, LoopOut>; history: LoopTraces; status: StreamStatus } {
  const rig = useRig();
  const [seed, setSeed] = useState<Held | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    rig
      .loops()
      .then((loops) => {
        if (!controller.signal.aborted) setSeed(fold({ loops: {}, history: {} }, loops, windowS));
      })
      .catch(() => undefined); // the socket sends every loop on connect anyway
    return () => controller.abort();
  }, [rig, windowS]);

  const { state: live, status } = useStream("loops", { loops: {}, history: {} } as Held, (held, message) =>
    fold(held, message.loops, windowS),
  );

  // The seed only matters until the socket's first message; after that every
  // loop it named is in `live` with a longer trace.
  if (!seed) return { loops: live.loops, history: live.history, status };
  const loops = { ...seed.loops, ...live.loops };
  const history = { ...seed.history, ...live.history };
  return { loops, history, status };
}
