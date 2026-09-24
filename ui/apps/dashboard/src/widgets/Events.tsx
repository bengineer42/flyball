import { memo } from "react";
import { Box, Typography } from "@mui/material";
import { SEVERITIES, atLeast, humanise, type Severity } from "@flyball/client";
import { hashFor } from "../router.js";
import { useEventsData, useRigData } from "../dashboard/context.js";
import { rowsThatFit } from "./size.js";
import type { WidgetType, WidgetComponentProps } from "./types.js";

/** `anneal[2]` → "anneal · step 3": a program's step subjects count from zero; people count from one. */
export const humaniseSubject = (subject: string) => subject.replace(/^(.*)\[(\d+)\]$/, (_m, name: string, i: string) => `${name} · step ${Number(i) + 1}`);

export const TIME = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });

const SEVERITY_COLOUR: Record<Severity, string> = { debug: "text.disabled", info: "text.secondary", warning: "warning.main", error: "error.main" };

/** One table row: `line-height` 20 + 2×2 padding + 1px rule (dashboard.css `.dash-events td`). */
const ROW_PX = 25;
/** The "all events →" line under the rows. */
const MORE_PX = 20;

/**
 * Newest first, as many rows as the body holds -- computed from the widget's
 * `h`, never scrolled or clipped mid-row -- capped by `limit`. Body only; the
 * frame is `WidgetFrame`'s.
 */
const EventsWidget = memo(function EventsWidget({ config, widget }: WidgetComponentProps) {
  const events = useEventsData();
  const { rowHeight } = useRigData();
  const from = SEVERITIES.indexOf(String(config.severity ?? "info").toLowerCase() as Severity);
  const allowed = new Set(SEVERITIES.slice(Math.max(0, from)));
  const subjectKind = String(config.subject_kind ?? "").trim();
  const limit = Math.min(Math.max(1, Number(config.limit ?? 20)), rowsThatFit(widget.h, rowHeight, true, MORE_PX, ROW_PX));
  const shown: typeof events = [];
  let matching = 0;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i]!;
    if (!allowed.has(e.severity)) continue;
    if (subjectKind && e.subject_kind !== subjectKind) continue;
    matching++;
    if (shown.length < limit) shown.push(e);
  }
  return (
    <div className="dash-events">
      {shown.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          no events
        </Typography>
      )}
      <table>
        <tbody>
          {shown.map((e, i) => (
            <tr key={`${e.time_ns}-${i}`} title={`${e.subject_kind} · ${humaniseSubject(e.subject)} · ${e.code}\n${new Date(e.time_ns / 1e6).toLocaleString()}`}>
              <td className="dash-events-time">{TIME.format(new Date(e.time_ns / 1e6))}</td>
              <Box component="td" className="dash-events-kind" sx={{ color: SEVERITY_COLOUR[e.severity], fontWeight: atLeast(e.severity, "warning") ? 600 : 400 }}>
                {humanise(e.code)}
              </Box>
              <td className="dash-events-subject">{humaniseSubject(e.subject)}</td>
              <td className="dash-events-message">{e.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {matching > shown.length && (
        <a className="dash-events-more fb-muted" href={hashFor("events")}>
          all {matching} events →
        </a>
      )}
    </div>
  );
});

export const events: WidgetType = {
  type: "events",
  label: "Events",
  description: "The rig's latest events, newest first, from a severity up; optionally one kind of subject only.",
  category: "status",
  // 12×6: a 150px body shows 5 rows and the "all events" line; 6×3 shows one row (DESIGN-SPEC.md §10).
  defaultSize: { w: 12, h: 6 },
  minSize: { w: 6, h: 3 },
  cost: "cheap",
  configSchema: () => ({
    type: "object",
    properties: {
      severity: { type: "string", title: "From severity", default: "info", enum: [...SEVERITIES], description: "This severity and above." },
      limit: { type: "integer", title: "Rows", default: 20, minimum: 1, maximum: 200, description: "At most; the widget shows as many as its height holds." },
      subject_kind: { type: "string", title: "Subject kind", default: "", description: "Only events about this kind of subject (`program`, `controller`, `device`, …); blank for all." },
    },
  }),
  defaultConfig: () => ({ severity: "info", limit: 20, subject_kind: "" }),
  labelFor: (config) => (config.subject_kind ? `${String(config.subject_kind)} events` : "Events"),
  Component: EventsWidget,
};
