import { memo, type ReactNode } from "react";
import { Box, Typography } from "@mui/material";
import { CircleIcon, OkIcon, PAGE_ICONS, SourceIcon, WarnIcon, type IconComponent } from "../icons.js";
import { hashFor } from "../router.js";
import { duration } from "../time.js";
import { useEventsData, useRigData } from "../dashboard/context.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

type Tone = "ok" | "warn" | "bad";
const TONE_COLOUR: Record<Tone, string | undefined> = { ok: undefined, warn: "warning.main", bad: "error.main" };

const TILES = ["rig", "recording", "readers", "loops", "conditions", "signals", "events", "uptime"] as const;
type TileId = (typeof TILES)[number];
const TILE_TITLES: Record<TileId, string> = { rig: "rig", recording: "recording", readers: "readers", loops: "loops", conditions: "conditions", signals: "signals waiting", events: "events", uptime: "uptime" };

/** One stat, the whole thing a link when it has a page. Normal is quiet: only warn and bad colour it. */
function Stat({ icon: Icon, label, value, tone, href }: { icon: IconComponent; label: string; value: ReactNode; tone?: Tone; href?: string }) {
  return (
    <Box
      component={href ? "a" : "div"}
      href={href}
      sx={{ display: "flex", alignItems: "center", gap: 1.5, px: 1.5, py: 1, minWidth: 0, color: "inherit", textDecoration: "none", borderRadius: 1, border: 1, borderColor: (tone && TONE_COLOUR[tone]) ?? "divider", "&:hover": href ? { bgcolor: "action.hover" } : undefined }}
    >
      <Icon fontSize="small" sx={{ color: (tone && TONE_COLOUR[tone]) ?? "text.secondary", flex: "none" }} />
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="overline" color="text.secondary" component="div" sx={{ lineHeight: 1.2 }}>
          {label}
        </Typography>
        <Typography fontWeight={600} noWrap>
          {value}
        </Typography>
      </Box>
    </Box>
  );
}

const HealthWidget = memo(function HealthWidget({ config }: WidgetComponentProps) {
  const { health } = useRigData();
  const events = useEventsData();
  const h = health.data;
  const wanted = (Array.isArray(config.tiles) && config.tiles.length ? (config.tiles as unknown[]).map(String) : TILES.slice(0, 5)).filter((t): t is TileId => (TILES as readonly string[]).includes(t));
  const readerCount = h ? Object.keys(h.readers).length : 0;
  const readersDown = h ? Object.values(h.readers).filter((r) => !r.running).length : 0;
  const problems = events.filter((e) => e.level === "ERROR" || e.level === "WARNING").length;
  const errors = events.filter((e) => e.level === "ERROR").length;
  const tiles: Record<TileId, ReactNode> = {
    rig: <Stat key="rig" icon={h?.ok ? OkIcon : WarnIcon} label="rig" value={h ? (h.ok ? "ok" : "fault") : "…"} tone={h ? (h.ok ? "ok" : "bad") : undefined} href={hashFor("events")} />,
    recording: <Stat key="recording" icon={SourceIcon} label="recording" value={h ? (h.recording ? "on" : "off") : "…"} tone={h?.recording ? "ok" : undefined} href={hashFor("sessions")} />,
    readers: <Stat key="readers" icon={PAGE_ICONS.sources} label="readers" value={h ? `${readerCount - readersDown}/${readerCount} running` : "…"} tone={readersDown ? "warn" : undefined} href={hashFor("readers")} />,
    loops: <Stat key="loops" icon={PAGE_ICONS.loops} label="loops" value={h ? Object.keys(h.loops).length : "…"} href={hashFor("loops")} />,
    conditions: <Stat key="conditions" icon={WarnIcon} label="conditions" value={h ? h.conditions.length : "…"} tone={h?.conditions.length ? "warn" : undefined} href={hashFor("events", null, null, { level: "WARNING" })} />,
    signals: <Stat key="signals" icon={CircleIcon} label="signals waiting" value={h ? h.signals.length : "…"} href={hashFor("events")} />,
    events: <Stat key="events" icon={PAGE_ICONS.events} label="events" value={`${problems} warn/error of ${events.length}`} tone={errors ? "bad" : problems ? "warn" : undefined} href={hashFor("events")} />,
    uptime: <Stat key="uptime" icon={PAGE_ICONS.sessions} label="uptime" value={h ? duration(h.uptime_s) : "…"} />,
  };
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: `repeat(auto-fit, minmax(min(100%, 9rem), 1fr))`, gap: 1, alignContent: "center", flex: "1 1 auto", minHeight: 0, overflow: "auto" }}>
      {wanted.map((t) => tiles[t])}
      {h && h.conditions.length > 0 && (
        <Box sx={{ gridColumn: "1 / -1", color: "warning.main", fontSize: "0.85rem" }}>
          {h.conditions.map((c) => (
            <div key={c.kind + c.since_ns}>
              <strong>{c.kind}</strong> {c.message}
            </div>
          ))}
        </Box>
      )}
    </Box>
  );
});

export const health: WidgetKind = {
  kind: "health",
  label: "Health",
  description: "The rig's condition at a glance: fault or ok, recording, readers running, loops, conditions, signals, uptime.",
  category: "status",
  defaultSize: { w: 12, h: 2 },
  minSize: { w: 2, h: 2 },
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
