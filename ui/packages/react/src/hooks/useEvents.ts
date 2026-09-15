import { useEffect, useState } from "react";
import type { Event } from "@flyball/client";
import { useRig } from "../provider.js";
import { useStream, type StreamStatus } from "./useStream.js";

/** Keep the newest `limit` of an oldest-first list. */
const cap = (events: Event[], limit: number) => (events.length > limit ? events.slice(events.length - limit) : events);

/**
 * The rig's recent events, oldest first, at most `limit` of them: seeded
 * from `GET /api/events`, then live from `/ws/events`. The socket resends
 * the recent events on connect; anything at or before the seed's newest
 * event is dropped, so the seam has no duplicates.
 */
export function useEvents(limit = 500): { events: Event[]; status: StreamStatus; error: Error | undefined } {
  const rig = useRig();
  const [seed, setSeed] = useState<Event[] | null>(null);
  const [error, setError] = useState<Error>();

  useEffect(() => {
    const controller = new AbortController();
    rig.events({ limit }).then(
      (events) => {
        if (!controller.signal.aborted) setSeed(events);
      },
      (err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err : new Error(String(err)));
      },
    );
    return () => controller.abort();
  }, [rig, limit]);

  const { state: live, status } = useStream("events", [] as Event[], (events, message) =>
    cap([...events, ...message.events], limit),
  );

  if (!seed?.length) return { events: cap(live, limit), status, error };
  const last = seed[seed.length - 1]!.time_ns;
  let i = 0;
  while (i < live.length && live[i]!.time_ns <= last) i++;
  return { events: cap([...seed, ...live.slice(i)], limit), status, error };
}
