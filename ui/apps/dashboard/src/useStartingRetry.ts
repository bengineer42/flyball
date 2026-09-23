import { useEffect, useRef } from "react";

const INITIAL_MS = 1000;
const MAX_MS = 8000;

/**
 * Retries `onRetry` on a backoff (1s, 2s, 4s, ..., capped at 8s) for as long as `active`. Used
 * for the "runner is starting" 503 (front/proxy.go's `Retry-After: 1`): the front comes up
 * before the runner does now, so a page opened straight away must recover on its own rather
 * than sit on a permanent "cannot reach the rig" alert until someone reloads.
 *
 * Starts a fresh cycle each time `active` goes from false to true; never overlaps a retry with
 * the wait before it.
 */
export function useStartingRetry(active: boolean, onRetry: () => void): void {
  const onRetryRef = useRef(onRetry);
  onRetryRef.current = onRetry;

  useEffect(() => {
    if (!active) return;
    let delay = INITIAL_MS;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      onRetryRef.current();
      delay = Math.min(delay * 2, MAX_MS);
      timer = setTimeout(tick, delay);
    };
    timer = setTimeout(tick, delay);
    return () => clearTimeout(timer);
  }, [active]);
}
