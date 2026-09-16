import { memo } from "react";
import { Box, Typography } from "@mui/material";
import { humanise, type EventLevel } from "@flyball/client";
import { EVENT_LEVELS } from "@flyball/react";
import { hashFor } from "../router.js";
import { useEventsData } from "../dashboard/context.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

/** `anneal[2]` → "anneal · step 3": a program's step subjects count from zero; people count from one. */
export const humaniseSubject = (subject: string) => subject.replace(/^(.*)\[(\d+)\]$/, (_m, name: string, i: string) => `${name} · step ${Number(i) + 1}`);

const TIME = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });

const LEVEL_COLOUR: Record<EventLevel, string> = { DEBUG: "text.disabled", INFO: "text.secondary", WARNING: "warning.main", ERROR: "error.main" };

const EventsWidget = memo(function EventsWidget({ config }: WidgetComponentProps) {
  const events = useEventsData();
  const from = EVENT_LEVELS.indexOf(String(config.level ?? "INFO") as EventLevel);
  const allowed = new Set(EVENT_LEVELS.slice(Math.max(0, from)));
  const scope = String(config.scope ?? "").trim();
  const limit = Math.max(1, Number(config.limit ?? 20));
  const shown: typeof events = [];
  for (let i = events.length - 1; i >= 0 && shown.length < limit; i--) {
    const e = events[i]!;
    if (!allowed.has(e.level)) continue;
    if (scope && e.scope !== scope) continue;
    shown.push(e);
  }
  return (
    <Box sx={{ overflow: "auto", minHeight: 0, flex: "1 1 auto", fontSize: "0.85rem" }}>
      {shown.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          no events
        </Typography>
      )}
      <Box component="table" sx={{ borderCollapse: "collapse", width: "100%", "& td": { py: 0.25, px: 0.5, verticalAlign: "baseline", borderBottom: 1, borderColor: "divider" }, "& tr:last-child td": { border: 0 } }}>
        <tbody>
          {shown.map((e, i) => (
            <tr key={`${e.time_ns}-${i}`} title={`${e.scope} · ${humaniseSubject(e.subject)} · ${e.kind}\n${new Date(e.time_ns / 1e6).toLocaleString()}`}>
              <Box component="td" sx={{ color: "text.secondary", whiteSpace: "nowrap", width: "1%", fontVariantNumeric: "tabular-nums" }}>
                {TIME.format(new Date(e.time_ns / 1e6))}
              </Box>
              <Box component="td" sx={{ color: LEVEL_COLOUR[e.level], whiteSpace: "nowrap", width: "1%", fontWeight: e.level === "ERROR" || e.level === "WARNING" ? 600 : 400 }}>
                {humanise(e.kind)}
              </Box>
              <Box component="td" sx={{ color: "text.secondary", whiteSpace: "nowrap", width: "1%" }}>
                {humaniseSubject(e.subject)}
              </Box>
              <td>{e.message}</td>
            </tr>
          ))}
        </tbody>
      </Box>
      {events.length > shown.length && (
        <Typography variant="caption" color="text.secondary" component="a" href={hashFor("events")} sx={{ display: "block", mt: 0.5, textDecoration: "none", "&:hover": { textDecoration: "underline" } }}>
          all events →
        </Typography>
      )}
    </Box>
  );
});

export const events: WidgetKind = {
  kind: "events",
  label: "Events",
  description: "The rig's latest events, newest first, from a level up; optionally one scope only.",
  category: "status",
  defaultSize: { w: 6, h: 6 },
  minSize: { w: 3, h: 3 },
  cost: "cheap",
  configSchema: () => ({
    type: "object",
    properties: {
      level: { type: "string", title: "From level", default: "INFO", enum: EVENT_LEVELS, description: "This level and above." },
      limit: { type: "integer", title: "Rows", default: 20, minimum: 1, maximum: 200 },
      scope: { type: "string", title: "Scope", default: "", description: "Only this scope (`program`, `loop`, `actuator`, …); blank for all." },
    },
  }),
  defaultConfig: () => ({ level: "INFO", limit: 20, scope: "" }),
  titleFor: (config) => (config.scope ? `${String(config.scope)} events` : "Events"),
  Component: EventsWidget,
};
