import { Alert } from "@mui/material";
import { EventsPanel } from "@flyball/react";
import type { RigEvent } from "@flyball/client";

export interface EventsProps {
  events: RigEvent[];
  error: Error | undefined;
}

/** The rig's event log, live from `/ws/events` (seeded from `/api/events`); the app holds the events. */
export function Events({ events, error }: EventsProps) {
  return (
    <>
      {error && (
        <Alert severity="error" sx={{ mb: 1.5 }}>
          {error.message}
        </Alert>
      )}
      <EventsPanel events={events} />
    </>
  );
}
