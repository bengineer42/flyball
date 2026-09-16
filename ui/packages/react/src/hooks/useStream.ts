import { useEffect, useRef, useState } from "react";
import type { StreamName, Streams } from "@flyball/client";
import { useRig } from "../provider.js";

import type { StreamStatus } from "../store/telemetry.js";
export type { StreamStatus };

/** How long a stream's socket outlives the component that opened it. */
const CLOSE_GRACE_MS = 5000;

/**
 * Subscribe to one websocket stream for the life of the component. `reduce`
 * folds each message into the held state, so a panel keeps only what it
 * shows (the latest per name, a ring of samples) rather than every message.
 */
export function useStream<S extends StreamName, T>(
  name: S,
  initial: T,
  reduce: (state: T, message: Streams[S]) => T,
  options: {
    /** Least milliseconds between two state updates; one animation frame by default. What arrives in between is folded in order. */
    everyMs?: number;
  } = {},
): { state: T; status: StreamStatus } {
  const everyMs = options.everyMs ?? 0;
  const rig = useRig();
  const [state, setState] = useState<T>(initial);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const reducer = useRef(reduce);
  reducer.current = reduce;

  useEffect(() => {
    // Messages can arrive faster than a render + commit can complete (a simulated clock
    // running at 60x, say): folding each one into state as it arrives calls `setState`
    // once per message, and once the queue backs up React sees update after update
    // scheduled before the last one has committed -- "Maximum update depth exceeded",
    // even though nothing here is truly an infinite loop. Coalesce everything that
    // arrives within one animation frame into a single update instead; order is kept
    // (each queued message still folds in turn), nothing is dropped, only the number
    // of times React is asked to re-render is capped.
    let queued: Array<Streams[S]> = [];
    let frame: number | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let lastAt = 0;
    let gone = false;
    const flush = () => {
      frame = null;
      timer = null;
      if (!queued.length || gone) return;
      lastAt = Date.now();
      const batch = queued;
      queued = [];
      setState((s) => batch.reduce((acc, message) => reducer.current(acc, message), s));
    };
    const subscription = rig.stream(name, {
      onMessage: (message) => {
        if (gone) return;
        queued.push(message);
        if (frame !== null || timer !== null) return;
        const wait = lastAt + everyMs - Date.now();
        if (wait > 0) timer = setTimeout(flush, wait);
        else frame = requestAnimationFrame(flush);
      },
      onOpen: () => {
        if (!gone) setStatus("open");
      },
      onClose: () => {
        if (!gone) setStatus("closed");
      },
    });
    return () => {
      gone = true;
      if (frame !== null) cancelAnimationFrame(frame);
      if (timer !== null) clearTimeout(timer);
      queued = [];
      // Closed after a grace period, not at once: a socket closed while still connecting (a Strict
      // Mode remount, a page flipped through) makes the browser log a warning, and the store's
      // streams close the same way (see `TelemetryStore`).
      setTimeout(() => subscription.close(), CLOSE_GRACE_MS);
    };
  }, [rig, name, everyMs]);

  return { state, status };
}
