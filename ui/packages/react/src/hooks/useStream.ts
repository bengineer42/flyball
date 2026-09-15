import { useEffect, useRef, useState } from "react";
import type { StreamName, Streams } from "@flyball/client";
import { useRig } from "../provider.js";

export type StreamStatus = "connecting" | "open" | "closed";

/**
 * Subscribe to one websocket stream for the life of the component. `reduce`
 * folds each message into the held state, so a panel keeps only what it
 * shows (the latest per name, a ring of samples) rather than every message.
 */
export function useStream<S extends StreamName, T>(
  name: S,
  initial: T,
  reduce: (state: T, message: Streams[S]) => T,
): { state: T; status: StreamStatus } {
  const rig = useRig();
  const [state, setState] = useState<T>(initial);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const reducer = useRef(reduce);
  reducer.current = reduce;

  useEffect(() => {
    const subscription = rig.stream(name, {
      onMessage: (message) => setState((s) => reducer.current(s, message)),
      onOpen: () => setStatus("open"),
      onClose: () => setStatus("closed"),
    });
    return () => subscription.close();
  }, [rig, name]);

  return { state, status };
}
