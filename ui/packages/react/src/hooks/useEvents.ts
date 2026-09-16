import type { Event } from "@flyball/client";
import type { StreamStatus } from "./useStream.js";
import { useEventsFeed, useStoreStatus } from "../store/hooks.js";

/**
 * The rig's recent events, oldest first, at most `limit` of them: seeded
 * from `GET /api/events`, then live from `/ws/events`, through the telemetry
 * store (`useEventsFeed` filters as well). The array keeps its identity
 * until an event arrives.
 */
export function useEvents(limit = 500): { events: Event[]; status: StreamStatus; error: Error | undefined } {
  const events = useEventsFeed(undefined, limit);
  const status = useStoreStatus("events");
  return { events, status, error: undefined };
}
