import { Alert } from "@mui/material";
import { EventsPanel, EVENT_LEVELS, useNowS } from "@flyball/react";
import type { EventLevel, RigEvent } from "@flyball/client";
import { StateBlock } from "../cards.js";

export interface EventsProps {
  events: RigEvent[];
  error: Error | undefined;
  /** `#/events?level=WARNING`: show that level and above until the viewer changes the filter. */
  level?: string;
}

/** The rig's event log, live from `/ws/events` (seeded from `/api/events`); the app holds the events. */
export function Events({ events, error, level }: EventsProps) {
  const from = EVENT_LEVELS.indexOf((level ?? "").toUpperCase() as EventLevel);
  const levels = from > 0 ? EVENT_LEVELS.slice(from) : undefined;
  const nowS = useNowS();
  return (
    <>
      {error && (
        <Alert severity="error" sx={{ mb: 2.25 }}>
          {error.message}
        </Alert>
      )}
      {events.length === 0 ? (
        <StateBlock state="empty" message="No events yet — nothing has happened on this rig since it started." />
      ) : (
        // Keyed so arriving with another level resets the panel's own filter.
        <EventsPanel key={levels?.join() ?? "all"} events={events} levels={levels} nowS={nowS} />
      )}
    </>
  );
}
