import { useMemo, useState, type ReactNode } from "react";
import type { EventLevel, RigEvent } from "@flyball/client";
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
}

export const EVENT_LEVELS: EventLevel[] = ["DEBUG", "INFO", "WARNING", "ERROR"];

const TIME = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  fractionalSecondDigits: 3,
  hourCycle: "h23",
});

/** Event scopes that name a thing with a page. */
const SCOPE_KINDS: Record<string, RefKind> = { loop: "loop", reader: "reader", actuator: "actuator", source: "source", session: "session" };

const key = (e: RigEvent) => `${e.time_ns}:${e.scope}:${e.subject}:${e.kind}`;

/**
 * The rig's events as a table, newest first: time, level, scope·subject,
 * kind, message; click a row for its details. The level and text filters
 * are view state and live here. Pure; `useEvents` supplies the events.
 */
export function EventsPanel({ events, levels: initialLevels, onSelect, controls }: EventsPanelProps) {
  const [levels, setLevels] = useState<Set<EventLevel>>(() => new Set(initialLevels ?? EVENT_LEVELS));
  const [text, setText] = useState("");
  const [open, setOpen] = useState<Set<string>>(() => new Set());

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
    <article className="fb-panel fb-events">
      <header className="fb-events-head">
        <h3>events</h3>
        <span className="fb-muted">
          {shown.length} of {events.length}
        </span>
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
        {controls && <span className="fb-events-controls">{controls}</span>}
      </header>
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
                  <td className="fb-event-time">{TIME.format(date)}</td>
                  <td>
                    <span className={`fb-badge fb-event-level fb-event-${e.level}`}>{e.level}</span>
                  </td>
                  <td className="fb-event-scope" onClick={(ev) => ev.stopPropagation()}>
                    {e.scope}
                    <span className="fb-muted">·</span>
                    {SCOPE_KINDS[e.scope] ? <Ref kind={SCOPE_KINDS[e.scope]!} name={e.subject} /> : e.subject}
                  </td>
                  <td className="fb-event-kind">{e.kind}</td>
                  <td className="fb-event-message">{e.message}</td>
                </tr>,
                expanded && (
                  <tr key={`${k}:details`} className="fb-event-details">
                    <td colSpan={5}>
                      <ValueView value={e.details} />
                    </td>
                  </tr>
                ),
              ];
            })}
          </tbody>
        </table>
      </div>
    </article>
  );
}
