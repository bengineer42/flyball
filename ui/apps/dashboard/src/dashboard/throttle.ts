import { useEffect, useRef, useState } from "react";
import type { Address, ControllerOut } from "@flyball/client";
import type { ControllerTraces } from "@flyball/react";

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

/** The same for controllers: one object per controller and per trace, kept until a tick moves it. */
export function useStableControllers(controllers: Record<Address, ControllerOut>, history: ControllerTraces): { controllers: Record<Address, ControllerOut>; history: ControllerTraces } {
  const held = useRef<{ controllers: Record<Address, ControllerOut>; history: ControllerTraces }>({ controllers: {}, history: {} });
  let changed = false;
  const nextControllers: Record<Address, ControllerOut> = {};
  for (const [name, controller] of Object.entries(controllers)) {
    if (held.current.controllers[name] !== controller) changed = true;
    nextControllers[name] = controller;
  }
  const nextHistory: ControllerTraces = {};
  for (const [name, trace] of Object.entries(history)) {
    const prev = held.current.history[name];
    if (prev && sameTrace({ t: prev.t, v: prev.reading }, { t: trace.t, v: trace.reading })) nextHistory[name] = prev;
    else {
      nextHistory[name] = trace;
      changed = true;
    }
  }
  if (Object.keys(held.current.controllers).length !== Object.keys(nextControllers).length || Object.keys(held.current.history).length !== Object.keys(nextHistory).length) changed = true;
  if (changed) held.current = { controllers: nextControllers, history: nextHistory };
  return held.current;
}
