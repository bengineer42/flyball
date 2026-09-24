import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { RigEvent, Severity } from "@flyball/client";
import { SEVERITIES, describeEdge, describeEventCode, describeSubject } from "@flyball/client";
import { PanelFrame } from "./PanelFrame.js";
import { ValueView } from "./ValueView.js";
import { Ref, type RefKind } from "../links.js";

export interface EventsPanelProps {
  /** Oldest first, as `useEvents` holds them; the panel shows newest first. */
  events: RigEvent[];
  /** Severities shown until the viewer changes the filter. Default: all. */
  severities?: Severity[];
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
  /** Keys (`eventKey(e)`) of events not yet read, e.g. from `useUnreadEvents`. Omit to show no read/unread state. */
  unread?: ReadonlySet<string>;
}

/** A glyph per severity, so it does not rely on colour alone. */
const SEVERITY_ICON: Record<Severity, string> = { debug: "○", info: "ℹ", warning: "▲", error: "✕" };

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

/** Event scopes that name a thing with a page (`device` is the one the backend emits today; the rest are for a scope a driver might add). */
const SCOPE_KINDS: Record<string, RefKind> = { device: "device", controller: "controller", signal: "signal", session: "session" };

/**
 * The code as a person reads it. A controller's `interrupted` is "Put in
 * manual" (by a command that interrupts, or by a stop; its details are
 * `{was, by}` and its message names who), not a program's "Interrupted".
 */
export function describeEvent(e: Pick<RigEvent, "scope" | "code">): string {
  if (e.scope === "controller" && e.code === "interrupted") return "Put in manual";
  return describeEventCode(e.code);
}

/** An event's identity, stable across the seed/live boundary: used to key rows, track expansion and track read/unread (`useUnreadEvents`). */
export const eventKey = (e: RigEvent) => `${e.time_ns}:${e.scope}:${e.subject}:${e.code}:${e.edge ?? ""}`;

/**
 * The rig's events as a table, newest first: time, severity, scope·subject,
 * code, message; click a row for its details. A condition's start and end
 * are marked `raised` and `cleared after <how long>` beside the code. The severity and text filters
 * are view state and live here. Pure; `useEvents` supplies the events.
 */
export function EventsPanel({ events, severities: initialSeverities, onSelect, controls, nowS, unread }: EventsPanelProps) {
  const [severities, setSeverities] = useState<Set<Severity>>(() => new Set(initialSeverities ?? SEVERITIES));
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
      if (!severities.has(e.severity)) continue;
      if (needle && !`${e.scope} ${e.subject} ${e.code} ${e.edge ?? ""} ${e.message}`.toLowerCase().includes(needle)) continue;
      out.push(e);
    }
    return out;
  }, [events, severities, text]);

  const toggleSeverity = (severity: Severity) =>
    setSeverities((s) => {
      const next = new Set(s);
      if (next.has(severity)) next.delete(severity);
      else next.add(severity);
      return next;
    });

  const toggleRow = (e: RigEvent) => {
    const k = eventKey(e);
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
          {SEVERITIES.map((severity) => (
            <label key={severity} className={`fb-events-filter fb-event-${severity}`}>
              <input type="checkbox" checked={severities.has(severity)} onChange={() => toggleSeverity(severity)} />
              {severity}
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
              <th>severity</th>
              <th>scope</th>
              <th>code</th>
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
              const k = eventKey(e);
              const date = new Date(e.time_ns / 1e6);
              const expanded = open.has(k);
              const isUnread = unread?.has(k) ?? false;
              return [
                <tr
                  key={k}
                  className={`fb-event-row${expanded ? " fb-event-open" : ""}${isUnread ? " fb-event-unread" : ""}`}
                  onClick={() => toggleRow(e)}
                  title={date.toLocaleString()}
                >
                  <td className="fb-event-time" title={date.toLocaleString()}>
                    {isUnread && <span className="fb-event-dot" aria-label="unread" data-testid="event-unread-dot" />}
                    {relative(date.getTime(), now)}
                  </td>
                  <td>
                    <span className={`fb-badge fb-event-severity fb-event-${e.severity}`} title={e.severity}>
                      <span aria-hidden="true">{SEVERITY_ICON[e.severity]}</span> {e.severity}
                    </span>
                  </td>
                  <td className="fb-event-scope" onClick={(ev) => ev.stopPropagation()}>
                    {e.scope}
                    <span className="fb-muted">·</span>
                    {SCOPE_KINDS[e.scope] ? <Ref kind={SCOPE_KINDS[e.scope]!} name={e.subject} /> : describeSubject(e.subject)}
                  </td>
                  <td className="fb-event-code" title={e.edge ? `${e.code} ${e.edge}` : e.code}>
                    {describeEvent(e)}
                    {e.edge && (
                      <span className={`fb-event-edge fb-event-${e.edge}`} data-testid="event-edge">
                        {describeEdge(e.edge, e.details)}
                      </span>
                    )}
                  </td>
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
