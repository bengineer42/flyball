import { useCallback, useRef, useState } from "react";

/**
 * A value with undo and redo: `set` commits a step (the last `limit` are
 * kept), `reset` starts over from a new baseline (a load, a save).
 *
 * `past`/`future`/`presentRef` are refs mutated directly in `set`/`undo`/`redo`/`reset`
 * themselves, never inside a `setPresent(prev => …)` updater: React 18 Strict Mode calls an
 * updater function twice to catch exactly this (an impure updater with a side effect); with the
 * mutation inside it, the second call sees `future`/`past` already spent by the first and
 * silently no-ops, so redo (say) right after a resize would intermittently do nothing. Keeping
 * `setPresent` a plain value call sidesteps that -- `presentRef` is what stands in for the
 * "current prev" a functional updater would have given us.
 */
export function useHistory<T>(initial: T, limit = 20) {
  const [present, setPresent] = useState<T>(initial);
  const presentRef = useRef(present);
  presentRef.current = present;
  const past = useRef<T[]>([]);
  const future = useRef<T[]>([]);
  const [, bump] = useState(0);
  const set = useCallback(
    (next: T | ((prev: T) => T)) => {
      const prev = presentRef.current;
      const value = typeof next === "function" ? (next as (p: T) => T)(prev) : next;
      if (value === prev) return;
      past.current = [...past.current.slice(-(limit - 1)), prev];
      future.current = [];
      presentRef.current = value;
      setPresent(value);
    },
    [limit],
  );
  const undo = useCallback(() => {
    const last = past.current[past.current.length - 1];
    if (last === undefined) return;
    past.current = past.current.slice(0, -1);
    future.current = [presentRef.current, ...future.current];
    presentRef.current = last;
    setPresent(last);
    bump((n) => n + 1);
  }, []);
  const redo = useCallback(() => {
    const next = future.current[0];
    if (next === undefined) return;
    future.current = future.current.slice(1);
    past.current = [...past.current, presentRef.current];
    presentRef.current = next;
    setPresent(next);
    bump((n) => n + 1);
  }, []);
  const reset = useCallback((value: T) => {
    past.current = [];
    future.current = [];
    presentRef.current = value;
    setPresent(value);
    bump((n) => n + 1);
  }, []);
  return { present, set, undo, redo, reset, canUndo: past.current.length > 0, canRedo: future.current.length > 0 };
}
