import { memo, type CSSProperties, type ReactNode } from "react";
import { Box, Typography } from "@mui/material";
import { CircleIcon, OkIcon, PAGE_ICONS, SignalIcon, WarnIcon, type IconComponent } from "../icons.js";
import { hashFor } from "../router.js";
import { duration } from "../time.js";
import { useEventsData, useRigData } from "../dashboard/context.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

type Tone = "ok" | "warn" | "bad";
const TONE_COLOUR: Record<Tone, string | undefined> = { ok: undefined, warn: "warning.main", bad: "error.main" };

// The tile ids match `/api/health`'s fields (`devices`, `controllers`, `activities`).
const TILES = ["rig", "recording", "devices", "controllers", "conditions", "activities", "events", "uptime"] as const;
type TileId = (typeof TILES)[number];
const TILE_TITLES: Record<TileId, string> = { rig: "rig", recording: "recording", devices: "devices", controllers: "controllers", conditions: "conditions", activities: "activities", events: "events", uptime: "uptime" };

/**
 * One stat: icon, 11px label over a 13/600 value, the whole thing a link when
 * it has a page. Normal is quiet: only warn and bad colour it. Laid out by
 * `.dash-health` (dashboard.css): equal columns divided by hairlines, so the
 * strip reads as one row of the grid rather than five small cards.
 */
function Stat({ icon: Icon, label, value, tone, href }: { icon: IconComponent; label: string; value: ReactNode; tone?: Tone; href?: string }) {
  const colour = tone && TONE_COLOUR[tone];
  return (
    <Box component={href ? "a" : "div"} href={href} className={`dash-health-stat${tone ? ` dash-health-${tone}` : ""}`} sx={{ color: "inherit", textDecoration: "none" }}>
      <Icon fontSize="small" sx={{ color: colour ?? "text.secondary", flex: "none" }} />
      <span className="dash-health-text">
        <Typography variant="overline" color="text.secondary" component="span" className="dash-health-label">
          {label}
        </Typography>
        <Typography fontWeight={600} noWrap component="span" className="dash-health-value" sx={{ color: colour }}>
          {value}
        </Typography>
      </span>
    </Box>
  );
}

const HealthWidget = memo(function HealthWidget({ config }: WidgetComponentProps) {
  const { health } = useRigData();
  const events = useEventsData();
  const h = health.data;
  const wanted = (Array.isArray(config.tiles) && config.tiles.length ? (config.tiles as unknown[]).map(String) : TILES.slice(0, 5)).filter((t): t is TileId => (TILES as readonly string[]).includes(t));
  const polled = h ? Object.keys(h.devices).length : 0;
  const stopped = h ? Object.values(h.devices).filter((d) => !d.running).length : 0;
  const problems = events.filter((e) => e.level === "ERROR" || e.level === "WARNING").length;
  const errors = events.filter((e) => e.level === "ERROR").length;
  // Alarm summary (research §6): `/api/health.alarms` folds the signals outside their warn/alarm band with the
  // device conditions at WARNING/ERROR, so this tile, the Overview and the app-bar chip agree.
  const amber = h?.alarms.warn ?? 0;
  const red = h?.alarms.alarm ?? 0;
  const conditionsCount = amber + red;
  const conditionsTone: Tone | undefined = red > 0 || (h?.alarms.max_level ?? 0) >= 40 ? "bad" : conditionsCount > 0 ? "warn" : undefined;
  const tiles: Record<TileId, ReactNode> = {
    rig: <Stat key="rig" icon={h?.ok ? OkIcon : WarnIcon} label="rig" value={h ? (h.ok ? "ok" : "fault") : "…"} tone={h ? (h.ok ? "ok" : "bad") : undefined} href={hashFor("events")} />,
    recording: <Stat key="recording" icon={SignalIcon} label="recording" value={h ? (h.recording ? "on" : "off") : "…"} tone={h?.recording ? "ok" : undefined} href={hashFor("sessions")} />,
    devices: <Stat key="devices" icon={PAGE_ICONS.devices} label="devices" value={h ? `${polled - stopped}/${polled} polling` : "…"} tone={stopped ? "warn" : undefined} href={hashFor("devices")} />,
    controllers: <Stat key="controllers" icon={PAGE_ICONS.controllers} label="controllers" value={h ? Object.keys(h.controllers).length : "…"} href={hashFor("controllers")} />,
    conditions: <Stat key="conditions" icon={WarnIcon} label="conditions" value={h ? conditionsCount : "…"} tone={h ? conditionsTone : undefined} href={hashFor("events", null, { level: "WARNING" })} />,
    activities: <Stat key="activities" icon={CircleIcon} label="activities" value={h ? h.activities.length : "…"} href={hashFor("events")} />,
    events: <Stat key="events" icon={PAGE_ICONS.events} label="events" value={`${problems} warn/error of ${events.length}`} tone={errors ? "bad" : problems ? "warn" : undefined} href={hashFor("events")} />,
    uptime: <Stat key="uptime" icon={PAGE_ICONS.sessions} label="uptime" value={h ? duration(h.uptime_s) : "…"} />,
  };
  // Active conditions are the count (linked to Events); listing them here would outgrow a 2-row strip -- that is the `conditions` widget's job (§3.8).
  return (
    <div className="dash-health" style={{ "--dash-health-n": wanted.length } as CSSProperties}>
      {wanted.map((t) => tiles[t])}
    </div>
  );
});

export const health: WidgetKind = {
  kind: "health",
  label: "Health",
  description: "The rig's condition at a glance: fault or ok, recording, devices polling, controllers, conditions, activities, uptime.",
  category: "status",
  // 24×2: a 60px body holds the 32px label-over-value pair; the strip never grows a second row (measured, DESIGN-SPEC.md §10).
  defaultSize: { w: 24, h: 2 },
  minSize: { w: 6, h: 2 },
  cost: "cheap",
  configSchema: () => ({
    type: "object",
    properties: {
      tiles: { type: "array", title: "Tiles", items: { type: "string", oneOf: TILES.map((t) => ({ const: t, title: TILE_TITLES[t] })) }, uniqueItems: true, default: TILES.slice(0, 5), description: "Which stats, in this order; none ticked shows the first five." },
    },
  }),
  defaultConfig: () => ({ tiles: TILES.slice(0, 5) }),
  titleFor: () => undefined,
  Component: HealthWidget,
};
