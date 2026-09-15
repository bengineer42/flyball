import { Alert } from "@mui/material";
import { EventsPanel, EVENT_LEVELS } from "@flyball/react";
import type { EventLevel, RigEvent } from "@flyball/client";

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
  return (
    <>
      {error && (
        <Alert severity="error" sx={{ mb: 1.5 }}>
          {error.message}
        </Alert>
      )}
      {/* Keyed so arriving with another level resets the panel's own filter. */}
      <EventsPanel key={levels?.join() ?? "all"} events={events} levels={levels} />
    </>
  );
}
