import { useEffect, useRef, useState } from "react";
import type { LoopOut } from "@flyball/client";
import type { LoopTraces, Traces } from "@flyball/react";

/**
 * `value` at most once per `ms`: the samples arrive faster than anyone can
 * read a number or a chart can usefully redraw, so what the widgets see is
 * the newest value on a 10 Hz beat rather than every message.
 */
export function useThrottled<T>(value: T, ms: number): T {
  const [shown, setShown] = useState(value);
  const latest = useRef(value);
  latest.current = value;
  const timer = useRef<number | null>(null);
  const lastAt = useRef(0);
  useEffect(() => {
    if (timer.current !== null) return; // a flush is due; it takes whatever is newest then
    const wait = Math.max(0, lastAt.current + ms - Date.now());
    timer.current = window.setTimeout(() => {
      timer.current = null;
      lastAt.current = Date.now();
      setShown(latest.current);
    }, wait);
  }, [value, ms]);
  // Strict mode mounts, unmounts and mounts again: the ref must say "no flush pending" after the cleanup, or none is ever scheduled.
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
    },
    [],
  );
  return shown;
}

const sameTrace = (a: { t: number[]; v: unknown[] }, b: { t: number[]; v: unknown[] }) =>
  a.t.length === b.t.length && a.t[a.t.length - 1] === b.t[b.t.length - 1] && a.v[a.v.length - 1] === b.v[b.v.length - 1] && a.t[0] === b.t[0];

/**
 * The same traces with each channel's object kept from the previous render
 * unless its points changed (`useSamples` rebuilds every array on every
 * render, so identity alone says nothing). A chart memoised on its channels'
 * trace objects then only touches the canvas when one of *its* channels
 * moved.
 */
export function useStableTraces(traces: Traces): Traces {
  const held = useRef<Traces>({});
  let changed = false;
  const next: Traces = {};
  for (const [key, trace] of Object.entries(traces)) {
    const prev = held.current[key];
    if (prev && prev.channel === trace.channel && sameTrace(prev, trace)) next[key] = prev;
    else {
      next[key] = trace;
      changed = true;
    }
  }
  if (Object.keys(held.current).length !== Object.keys(next).length) changed = true;
  if (changed) held.current = next;
  return held.current;
}

/** The same for loops: one object per loop and per trace, kept until a tick moves it. */
export function useStableLoops(loops: Record<string, LoopOut>, history: LoopTraces): { loops: Record<string, LoopOut>; history: LoopTraces } {
  const held = useRef<{ loops: Record<string, LoopOut>; history: LoopTraces }>({ loops: {}, history: {} });
  let changed = false;
  const nextLoops: Record<string, LoopOut> = {};
  for (const [name, loop] of Object.entries(loops)) {
    if (held.current.loops[name] !== loop) changed = true;
    nextLoops[name] = loop;
  }
  const nextHistory: LoopTraces = {};
  for (const [name, trace] of Object.entries(history)) {
    const prev = held.current.history[name];
    if (prev && sameTrace({ t: prev.t, v: prev.reading }, { t: trace.t, v: trace.reading })) nextHistory[name] = prev;
    else {
      nextHistory[name] = trace;
      changed = true;
    }
  }
  if (Object.keys(held.current.loops).length !== Object.keys(nextLoops).length || Object.keys(held.current.history).length !== Object.keys(nextHistory).length) changed = true;
  if (changed) held.current = { loops: nextLoops, history: nextHistory };
  return held.current;
}
