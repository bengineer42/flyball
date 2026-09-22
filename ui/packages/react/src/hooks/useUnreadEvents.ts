import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { EventLevel, RigEvent } from "@flyball/client";
import { eventKey } from "../panels/EventsPanel.js";

const RANK: Record<EventLevel, number> = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3 };

const WATERMARK_KEY = "flyball.events.readWatermark";

/** The rig's clock (nanoseconds) everything at or before has been read; 0 before anything has. */
const readWatermark = (): number => {
  try {
    return Number(window.localStorage.getItem(WATERMARK_KEY)) || 0;
  } catch {
    return 0;
  }
};

const writeWatermark = (time_ns: number) => {
  try {
    window.localStorage.setItem(WATERMARK_KEY, String(time_ns));
  } catch {
    /* not persisted */
  }
};

export interface UnreadEvents {
  /** Keys (`eventKey(e)`) of events at or above the threshold that have not been marked read. */
  unread: ReadonlySet<string>;
  unreadCount: number;
  markRead(e: RigEvent): void;
  markAllRead(): void;
  /** Events at or above the threshold that arrived since mount and have not yet been dismissed as a toast, oldest first. */
  toasts: RigEvent[];
  dismissToast(e: RigEvent): void;
}

/**
 * Read/unread state for the rig's events, plus the toast queue for notable events that
 * arrive while the viewer is elsewhere. A single "read up to this timestamp" watermark,
 * persisted client-side (`localStorage`) -- not a per-event set: nothing relies on marking
 * one older event read out of order, and a watermark needs no pruning. Survives a reload,
 * unlike `EventsPanel`'s own `levels`/`text`/`open` view state. One instance is meant to
 * live near the app root (fed by the same `useEvents` the pages already call) so the nav
 * badge, toasts and the Events page all agree on what's unread.
 *
 * Only events at or above `minLevel` (default `WARNING`) count: INFO/DEBUG flow
 * continuously on a running rig and would otherwise keep the badge and the toast queue
 * permanently full.
 */
export function useUnreadEvents(events: RigEvent[], minLevel: EventLevel = "WARNING"): UnreadEvents {
  const [watermark, setWatermark] = useState<number>(readWatermark);
  const [toasts, setToasts] = useState<RigEvent[]>([]);
  const lastKeyRef = useRef<string | null>(null);
  const mountedRef = useRef(false);

  useEffect(() => {
    if (events.length === 0) return;
    const lastKey = lastKeyRef.current;
    const idx = lastKey === null ? -1 : events.findIndex((e) => eventKey(e) === lastKey);
    // First run, or the last-seen event fell off the front (the `useEvents` limit trimmed it): don't
    // toast the whole backlog, only what arrives from here on.
    const fresh = mountedRef.current && idx >= 0 ? events.slice(idx + 1) : [];
    lastKeyRef.current = eventKey(events[events.length - 1]!);
    mountedRef.current = true;
    const notable = fresh.filter((e) => RANK[e.level] >= RANK[minLevel]);
    if (notable.length) setToasts((q) => [...q, ...notable]);
  }, [events, minLevel]);

  const raise = useCallback((time_ns: number) => {
    setWatermark((w) => {
      if (time_ns <= w) return w;
      writeWatermark(time_ns);
      return time_ns;
    });
  }, []);

  const markRead = useCallback((e: RigEvent) => raise(e.time_ns), [raise]);

  const markAllRead = useCallback(() => {
    if (events.length) raise(events[events.length - 1]!.time_ns);
  }, [events, raise]);

  const dismissToast = useCallback(
    (e: RigEvent) => {
      markRead(e);
      setToasts((q) => q.filter((t) => t !== e));
    },
    [markRead],
  );

  const unread = useMemo(() => {
    const s = new Set<string>();
    for (const e of events) if (RANK[e.level] >= RANK[minLevel] && e.time_ns > watermark) s.add(eventKey(e));
    return s;
  }, [events, minLevel, watermark]);

  return { unread, unreadCount: unread.size, markRead, markAllRead, toasts, dismissToast };
}
