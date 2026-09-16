import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { EventLevel, RigEvent } from "@flyball/client";
import { describeEventKind, describeSubject } from "@flyball/client";
import { PanelFrame } from "./PanelFrame.js";
import { ValueView } from "./ValueView.js";
import { Ref, type RefKind } from "../links.js";

export interface EventsPanelProps {
  /** Oldest first, as `useEvents` holds them; the panel shows newest first. */
  events: RigEvent[];
  /** Levels shown until the viewer changes the filter. Default: all. */
  levels?: EventLevel[];
  /** Called when a row is clicked, as well as toggling its details. */
  onSelect?(event: RigEvent): void;
  /** Rendered at the end of the header. */
  controls?: ReactNode;
  /**
   * The rig's clock, in seconds (e.g. `useNowS()`), for ageing "N ago".
   * Fed by every sample, so it keeps advancing between events — unlike the
   * newest event's own timestamp, which freezes once events go quiet (a
   * long `hold` step). Omit to fall back to the wall clock / newest event.
   */
  nowS?: number;
}

export const EVENT_LEVELS: EventLevel[] = ["DEBUG", "INFO", "WARNING", "ERROR"];

/** A glyph per level, so severity does not rely on colour alone. */
const LEVEL_ICON: Record<EventLevel, string> = { DEBUG: "○", INFO: "ℹ", WARNING: "▲", ERROR: "✕" };

/** `42 s ago`, `3 m ago`, `2 h ago`; the day for anything older. */
function relative(ms: number, now: number): string {
  const s = Math.max(0, Math.round((now - ms) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s} s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  return new Date(ms).toLocaleDateString();
}

/** Event scopes that name a thing with a page. */
const SCOPE_KINDS: Record<string, RefKind> = { loop: "loop", reader: "reader", actuator: "actuator", source: "source", session: "session" };

const key = (e: RigEvent) => `${e.time_ns}:${e.scope}:${e.subject}:${e.kind}`;

/**
 * The rig's events as a table, newest first: time, level, scope·subject,
 * kind, message; click a row for its details. The level and text filters
 * are view state and live here. Pure; `useEvents` supplies the events.
 */
export function EventsPanel({ events, levels: initialLevels, onSelect, controls, nowS }: EventsPanelProps) {
  const [levels, setLevels] = useState<Set<EventLevel>>(() => new Set(initialLevels ?? EVENT_LEVELS));
  const [text, setText] = useState("");
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  // Ticks the "42 s ago" times without waiting on new events. A simulated
  // rig's clock can run well ahead of the wall clock, so "now" is whichever
  // is later: the wall clock, or the newest event's own timestamp -- the
  // rig's idea of now beats a stale reading from an accelerated one.
  const [wallNow, setWallNow] = useState(Date.now);
  useEffect(() => {
    const id = setInterval(() => setWallNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const now = Math.max(wallNow, nowS !== undefined ? nowS * 1000 : 0, events.length ? events[events.length - 1]!.time_ns / 1e6 : 0);

  const shown = useMemo(() => {
    const needle = text.trim().toLowerCase();
    const out: RigEvent[] = [];
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i]!;
      if (!levels.has(e.level)) continue;
      if (needle && !`${e.scope} ${e.subject} ${e.kind} ${e.message}`.toLowerCase().includes(needle)) continue;
      out.push(e);
    }
    return out;
  }, [events, levels, text]);

  const toggleLevel = (level: EventLevel) =>
    setLevels((s) => {
      const next = new Set(s);
      if (next.has(level)) next.delete(level);
      else next.add(level);
      return next;
    });

  const toggleRow = (e: RigEvent) => {
    const k = key(e);
    setOpen((s) => {
      const next = new Set(s);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
    onSelect?.(e);
  };

  return (
    <PanelFrame
      className="fb-events"
      title="events"
      subtitle={`${shown.length} of ${events.length}`}
      status={
        <span className="fb-events-filters">
          {EVENT_LEVELS.map((level) => (
            <label key={level} className={`fb-events-filter fb-event-${level}`}>
              <input type="checkbox" checked={levels.has(level)} onChange={() => toggleLevel(level)} />
              {level}
            </label>
          ))}
          <input
            type="search"
            className="fb-events-search"
            placeholder="filter"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </span>
      }
      actions={controls}
    >
      <div className="fb-events-scroll">
        <table className="fb-events-table">
          <thead>
            <tr>
              <th>time</th>
              <th>level</th>
              <th>scope</th>
              <th>kind</th>
              <th>message</th>
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 && (
              <tr>
                <td colSpan={5} className="fb-muted">
                  no events
                </td>
              </tr>
            )}
            {shown.map((e) => {
              const k = key(e);
              const date = new Date(e.time_ns / 1e6);
              const expanded = open.has(k);
              return [
                <tr
                  key={k}
                  className={`fb-event-row${expanded ? " fb-event-open" : ""}`}
                  onClick={() => toggleRow(e)}
                  title={date.toLocaleString()}
                >
                  <td className="fb-event-time" title={date.toLocaleString()}>{relative(date.getTime(), now)}</td>
                  <td>
                    <span className={`fb-badge fb-event-level fb-event-${e.level}`} title={e.level}>
                      <span aria-hidden="true">{LEVEL_ICON[e.level]}</span> {e.level}
                    </span>
                  </td>
                  <td className="fb-event-scope" onClick={(ev) => ev.stopPropagation()}>
                    {e.scope}
                    <span className="fb-muted">·</span>
                    {SCOPE_KINDS[e.scope] ? <Ref kind={SCOPE_KINDS[e.scope]!} name={e.subject} /> : describeSubject(e.subject)}
                  </td>
                  <td className="fb-event-kind" title={e.kind}>{describeEventKind(e.kind)}</td>
                  <td className="fb-event-message">{e.message}</td>
                </tr>,
                expanded && (
                  <tr key={`${k}:details`} className="fb-event-details">
                    <td colSpan={5}>
                      <div style={{ fontFamily: "var(--fb-mono, monospace)" }}>
                        <ValueView value={e.details} />
                      </div>
                    </td>
                  </tr>
                ),
              ];
            })}
          </tbody>
        </table>
      </div>
    </PanelFrame>
  );
}
