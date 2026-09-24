import { Alert, Badge, Button } from "@mui/material";
import { EventsPanel, useNowS } from "@flyball/react";
import { SEVERITIES, type RigEvent, type Severity } from "@flyball/client";
import { StateBlock } from "../cards.js";

export interface EventsProps {
  events: RigEvent[];
  error: Error | undefined;
  /** `#/events?level=warning`: show that severity and above until the viewer changes the filter. */
  level?: string;
  /** Keys (`eventKey(e)`) of events not yet read (`useUnreadEvents`, WARNING+ only). Omit to show no read/unread state. */
  unread?: ReadonlySet<string>;
  /** Marks one event read, e.g. as its row is expanded. */
  onMarkRead?(e: RigEvent): void;
  /** Marks every event currently held as read (the "mark all read" button). */
  onMarkAllRead?(): void;
}

/** The rig's event log, live from `/ws/events` (seeded from `/api/events`); the app holds the events. */
export function Events({ events, error, level, unread, onMarkRead, onMarkAllRead }: EventsProps) {
  const from = SEVERITIES.indexOf((level ?? "").toLowerCase() as Severity);
  const severities = from > 0 ? SEVERITIES.slice(from) : undefined;
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
        // Keyed so arriving with another severity resets the panel's own filter.
        <EventsPanel
          key={severities?.join() ?? "all"}
          events={events}
          severities={severities}
          nowS={nowS}
          unread={unread}
          onSelect={onMarkRead}
          controls={
            onMarkAllRead && (
              <Badge badgeContent={unread?.size ?? 0} color="warning" max={99}>
                <Button size="small" onClick={onMarkAllRead} data-testid="events-mark-all-read">
                  Mark all read
                </Button>
              </Badge>
            )
          }
        />
      )}
    </>
  );
}
