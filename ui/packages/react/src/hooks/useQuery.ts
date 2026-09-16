import { useCallback, useEffect, useState } from "react";

/** Structurally equal, for the small JSON documents the rig serves; `false` for anything that cannot be serialised. */
function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}

export interface QueryState<T> {
  data: T | undefined;
  error: Error | undefined;
  loading: boolean;
  /** Re-run the fetch. */
  refresh(): void;
}

/**
 * The smallest useful fetch hook: runs `fetcher` when `deps` change, keeps the
 * last data while refreshing, aborts on unmount. Swap for TanStack Query when
 * caching across panels matters; the hooks above expose the same shape.
 */
export function useQuery<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
  options: { refreshMs?: number } = {},
): QueryState<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    fetcher(controller.signal).then(
      (result) => {
        if (controller.signal.aborted) return;
        // A poll that brought back the same document must not re-render everything under it.
        setData((prev) => (prev !== undefined && same(prev, result) ? prev : result));
        setError(undefined);
        setLoading(false);
      },
      (err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err : new Error(String(err)));
        setLoading(false);
      },
    );
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!options.refreshMs) return;
    const id = setInterval(refresh, options.refreshMs);
    return () => clearInterval(id);
  }, [options.refreshMs, refresh]);

  return { data, error, loading, refresh };
}
