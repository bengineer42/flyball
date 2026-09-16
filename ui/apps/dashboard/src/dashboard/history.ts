import { useCallback, useRef, useState } from "react";

/**
 * A value with undo and redo: `set` commits a step (the last `limit` are
 * kept), `reset` starts over from a new baseline (a load, a save).
 */
export function useHistory<T>(initial: T, limit = 20) {
  const [present, setPresent] = useState<T>(initial);
  const past = useRef<T[]>([]);
  const future = useRef<T[]>([]);
  const [, bump] = useState(0);
  const set = useCallback((next: T | ((prev: T) => T)) => {
    setPresent((prev) => {
      const value = typeof next === "function" ? (next as (p: T) => T)(prev) : next;
      if (value === prev) return prev;
      past.current = [...past.current.slice(-(limit - 1)), prev];
      future.current = [];
      return value;
    });
  }, [limit]);
  const undo = useCallback(() => {
    setPresent((prev) => {
      const last = past.current[past.current.length - 1];
      if (last === undefined) return prev;
      past.current = past.current.slice(0, -1);
      future.current = [prev, ...future.current];
      return last;
    });
    bump((n) => n + 1);
  }, []);
  const redo = useCallback(() => {
    setPresent((prev) => {
      const next = future.current[0];
      if (next === undefined) return prev;
      future.current = future.current.slice(1);
      past.current = [...past.current, prev];
      return next;
    });
    bump((n) => n + 1);
  }, []);
  const reset = useCallback((value: T) => {
    past.current = [];
    future.current = [];
    setPresent(value);
    bump((n) => n + 1);
  }, []);
  return { present, set, undo, redo, reset, canUndo: past.current.length > 0, canRedo: future.current.length > 0 };
}
