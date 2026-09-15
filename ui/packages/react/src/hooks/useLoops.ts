import { useEffect, useState } from "react";
import type { LoopOut, RigClient } from "@flyball/client";
import { setpointOf } from "@flyball/client";
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
    reference: slice(trace.reference, setpointOf(loop)),
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

/** A loop's identity across sessions and restarts: what it drives and what it reads. */
const pairOf = (loop: LoopOut) => `${loop.name}|${loop.channel.source}.${loop.channel.measurand}`;

/** Traces already fetched, by loop pair, so a page revisited shows its chart at once. */
const cache = new Map<string, LoopTrace>();

function appendTicks(trace: LoopTrace, startS: number, ticks: Awaited<ReturnType<RigClient["ticks"]>>): LoopTrace {
  return {
    t: [...trace.t, ...ticks.map((k) => startS + k.offset_ns / 1e9)],
    reference: [...trace.reference, ...ticks.map((k) => k.setpoint)],
    reading: [...trace.reading, ...ticks.map((k) => k.reading)],
    demand: [...trace.demand, ...ticks.map((k) => k.demand)],
    expected: [...trace.expected, ...ticks.map((k) => (k as { expected?: number | null }).expected ?? null)],
  };
}

/**
 * The last `windowS` seconds of every loop's ticks from the store, as
 * traces -- across sessions, so a daemon restart or a new recording does
 * not blank the chart. A loop is matched by its (actuator, channel) pair in
 * each session's loop rows; sessions are walked newest first until the
 * window is covered. Ticks from different sessions are on one absolute time
 * axis, so a gap between sessions shows as a gap.
 */
async function fromStore(rig: RigClient, loops: LoopOut[], windowS: number, every?: number): Promise<LoopTraces> {
  const [sessions, clock] = await Promise.all([rig.sessions(20).catch(() => []), rig.clock()]);
  if (!sessions.length) return {};
  const nowS = clock.now_ns / 1e9; // the rig's now: a simulated clock runs ahead of the wall
  const wanted = new Map(loops.map((l) => [pairOf(l), l.name]));
  const perLoop = new Map<string, Array<{ startS: number; ticks: Awaited<ReturnType<RigClient["ticks"]>> }>>();
  const horizonS = nowS - windowS;

  // Sessions must sit one after another on the time axis. A simulated clock
  // restarts with the daemon, so an older session can carry *later* stamps
  // than the current one; such a session cannot share the axis and is skipped.
  let floorS = Number.POSITIVE_INFINITY;
  for (const session of sessions) {
    const startS = session.start_ns / 1e9;
    const endS = session.end_ns === null ? nowS : session.end_ns / 1e9;
    if (endS > floorS) continue;
    floorS = startS;
    if (endS < horizonS) break; // older than the window: nothing further back matters
    const rows = await rig.sessionLoops(session.id).catch(() => []);
    await Promise.all(
      rows.map(async (row) => {
        const pair = `${row.actuator.name}|${row.channel.source.name}.${row.channel.measurand.name}`;
        const name = wanted.get(pair);
        if (!name) return;
        // History routes take offsets from the session's own start.
        const startOffset = Math.max(0, (horizonS - startS) * 1e9);
        const ticks = await rig.ticks(session.id, row.actuator.name, { start_ns: Math.floor(startOffset), ...(every && every > 1 ? { every } : {}) }).catch(() => []);
        if (ticks.length) (perLoop.get(name) ?? perLoop.set(name, []).get(name)!).push({ startS, ticks });
      }),
    );
  }

  const traces: LoopTraces = {};
  for (const [name, parts] of perLoop) {
    parts.sort((a, b) => a.startS - b.startS);
    traces[name] = parts.reduce((trace, part) => appendTicks(trace, part.startS, part.ticks), empty());
  }
  return traces;
}

/** `history` first, then the points of `live` newer than its last one. */
function splice(history: LoopTrace | undefined, live: LoopTrace): LoopTrace {
  if (!history || !history.t.length) return live;
  const last = history.t[history.t.length - 1]!;
  let i = 0;
  while (i < live.t.length && live.t[i]! <= last) i++;
  const join = <T>(a: T[], b: T[]) => [...a, ...b.slice(i)];
  return {
    t: join(history.t, live.t),
    reference: join(history.reference, live.reference),
    reading: join(history.reading, live.reading),
    demand: join(history.demand, live.demand),
    expected: join(history.expected, live.expected),
  };
}

/**
 * Every loop: the latest `LoopOut` per name, and a trace of the last
 * `windowS` seconds of reference, reading, demand and expected per loop,
 * so a chart can draw them. Seeded from `GET /api/loops` and, when a
 * session is recording, from its stored ticks -- so a freshly opened page
 * shows the window already full -- then live from `/ws/loops`.
 */
export function useLoops(windowS = 3600, every?: number): { loops: Record<string, LoopOut>; history: LoopTraces; status: StreamStatus } {
  const rig = useRig();
  const [seed, setSeed] = useState<Held | null>(null);
  const [stored, setStored] = useState<LoopTraces>({});

  useEffect(() => {
    const controller = new AbortController();
    rig
      .loops()
      .then(async (loops) => {
        if (controller.signal.aborted) return;
        setSeed(fold({ loops: {}, history: {} }, loops, windowS));
        // What we already hold for these pairs, at once; the store fills in behind it.
        const held: LoopTraces = {};
        for (const loop of loops) {
          const cached = cache.get(pairOf(loop));
          if (cached) held[loop.name] = cached;
        }
        if (Object.keys(held).length) setStored(held);
        const traces = await fromStore(rig, loops, windowS, every);
        if (controller.signal.aborted) return;
        for (const loop of loops) if (traces[loop.name]) cache.set(pairOf(loop), traces[loop.name]!);
        setStored((prev) => ({ ...prev, ...traces }));
      })
      .catch(() => undefined); // the socket sends every loop on connect anyway; no store is fine
    return () => controller.abort();
  }, [rig, windowS, every]);

  const { state: live, status } = useStream("loops", { loops: {}, history: {} } as Held, (held, message) =>
    fold(held, message.loops, windowS),
  );

  // The seed only matters until the socket's first message; after that every
  // loop it named is in `live` with a longer trace.
  const loops = seed ? { ...seed.loops, ...live.loops } : live.loops;
  const recent = seed ? { ...seed.history, ...live.history } : live.history;
  const history: LoopTraces = { ...recent };
  for (const name of Object.keys(stored)) history[name] = splice(stored[name], recent[name] ?? empty());
  for (const loop of Object.values(loops)) if (history[loop.name]) cache.set(pairOf(loop), history[loop.name]!);
  return { loops, history, status };
}
