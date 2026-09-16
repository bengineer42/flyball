import { memo, useState, type ReactNode } from "react";
import { Alert, Box, Button, ButtonBase, Chip, Link, Paper, Stack, Typography } from "@mui/material";
import { Readout, ObjectView, UnitCharts, groupByUnit, useHealth, channelKey, countRender, useActuatorState, useAlarmSummary, useEvents, useLatest, useReaderPeriods, useReaderRuns, useTraceRef, type TraceRef } from "@flyball/react";
import type { ActuatorSchema, ChannelOut, ReaderRun, ReaderSchema, RigSchema, SourceOut } from "@flyball/client";
import { CircleIcon, OkIcon, SourceIcon, WarnIcon, channelIcon, PAGE_ICONS, type IconComponent } from "../icons.js";
import { ChartControls, type ChartSettings } from "../YScaleSelect.js";
import { hashFor, hrefFor, type Page } from "../router.js";
import { DeviceCard, ReaderCard, SectionHead, StateBlock, StatusDot, clickThrough, clickableSx } from "../cards.js";
import { useRecordingExports } from "../model.js";
import { PageBar } from "../PageBar.js";
import { GroupingSelect, readGrouping, writeGrouping, type Grouping } from "../grouping.js";

export interface OverviewProps extends ChartSettings {
  schema: RigSchema;
  sources: SourceOut[];
  onOpen(page: Page): void;
}

/* Normal is quiet: only warn and bad colour a tile (ISA-101), so an abnormal one is the one the eye lands on. */
type Tone = "ok" | "warn" | "bad";
const TONE_COLOR: Record<Tone, string | undefined> = { ok: undefined, warn: "warning.main", bad: "error.main" };

const uptime = (s: number) => {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : m ? `${m}m ${Math.floor(s % 60)}s` : `${Math.floor(s)}s`;
};

/** A stat tile; with `href` the whole tile is a link (focusable, middle-clickable). One grid cell either way. */
function Stat({ icon: Icon, label, value, tone, href }: { icon: IconComponent; label: string; value: ReactNode; tone?: Tone; href?: string }) {
  const body = (
    <>
      <Icon fontSize="small" sx={{ color: (tone && TONE_COLOR[tone]) ?? "text.secondary" }} />
      <div>
        <Typography variant="overline" color="text.secondary" component="div" sx={{ lineHeight: 1.3 }}>
          {label}
        </Typography>
        <Typography fontWeight={600}>{value}</Typography>
      </div>
    </>
  );
  const sx = { display: "flex", alignItems: "center", gap: 3, px: 3, py: 2.25, height: "100%", boxSizing: "border-box", borderColor: tone ? TONE_COLOR[tone] : undefined } as const;
  if (!href) return <Paper className="c3" sx={sx}>{body}</Paper>;
  return (
    <ButtonBase component="a" href={href} className="c3" sx={{ display: "block", textAlign: "left", borderRadius: 1, "&.Mui-focusVisible": { outline: 2, outlineColor: "primary.main" } }}>
      <Paper sx={{ ...sx, ...clickableSx }}>{body}</Paper>
    </ButtonBase>
  );
}

/** A section's "→ somewhere" text action, for `SectionHead`'s `end` slot. */
function GoTo({ label, onClick }: { label: string; onClick(): void }) {
  return (
    <Button onClick={onClick} sx={{ py: 0 }}>
      {label} →
    </Button>
  );
}

/**
 * A channel's readout tile, with its kind's icon in the corner. Reads the store itself, so only
 * the tile re-renders on its channel's samples; memoised on primitives, so the page re-rendering
 * (a reader's run once a second, say) does not touch forty tiles.
 */
const Tile = memo(function Tile({ channel, live, windowS, every, showSource, exportHref, periodS, className }: { channel: ChannelOut; live: TraceRef; windowS: number; every: number; showSource: boolean; exportHref?: string; periodS?: number | null; className?: string }) {
  const Icon = channelIcon(channel);
  return (
    <div className={className ?? "tile-with-icon c3"}>
      <Icon fontSize="small" className="tile-icon" />
      <Readout channel={channel} source={live} showSource={showSource} windowS={windowS} every={every} exportHref={exportHref} fresh={{ periodS }} />
    </div>
  );
});

/** When a source last reported, from its first channel's newest point; re-renders this line alone. */
function LastSample({ source }: { source: SourceOut }) {
  const first = source.channels[0];
  const point = useLatest(first ? channelKey(first) : undefined);
  return <>{point ? `sample ${new Date(point.t * 1000).toLocaleTimeString()}` : "no sample yet"}</>;
}

/** The readers' cards, subscribed to their runs (a last-read time that moves once a second) so the page above is not. */
function ReaderCards({ readers, fallback }: { readers: ReaderSchema[]; fallback: Record<string, Partial<ReaderRun>> | undefined }) {
  const runs = useReaderRuns();
  return (
    <div className="grid">
      {readers.map((r) => (
        <ReaderCard key={r.name} className="c3" reader={r} run={runs[r.name] ?? fallback?.[r.name]} />
      ))}
    </div>
  );
}

/** One actuator's card, subscribed to its own state. */
const ActuatorCard = memo(function ActuatorCard({ actuator }: { actuator: ActuatorSchema }) {
  const state = useActuatorState(actuator.name);
  const conditions = state?.conditions ?? [];
  return (
    <DeviceCard
      className="c3"
      icon={PAGE_ICONS.actuators}
      name={actuator.name}
      label={actuator.label}
      href={hrefFor({ kind: "actuator", name: actuator.name })}
      type={actuator.type}
      chip={conditions.length > 0 ? <Chip label={conditions.map((c) => c.kind).join(", ")} color="warning" variant="outlined" /> : <StatusDot />}
    >
      <ObjectView schema={actuator.state} value={state} omit={["conditions"]} />
    </DeviceCard>
  );
});

/** Everything the rig knows about itself on one page: health strip, channels, actuators, readers. */
export function Overview({ schema, sources, onOpen, ...charts }: OverviewProps) {
  countRender("Overview");
  const { windowS, yScale, every } = charts;
  const channels = sources.flatMap((s) => s.channels);
  // One handle on the store for every chart and tile on the page; nothing here re-renders on a sample.
  const live = useTraceRef(channels);
  const { events } = useEvents(500);
  const stored = useRecordingExports();
  const [grouping, setGrouping] = useState<Grouping>(readGrouping);
  const group = (g: Grouping) => {
    setGrouping(g);
    writeGrouping(g);
  };
  const health = useHealth(5000);
  const h = health.data;
  // The readers' periods (for stale thresholds) change rarely; the runs themselves move every read and stay in `ReaderCards`.
  const periods = useReaderPeriods();
  const problems = events.filter((e) => e.level === "ERROR" || e.level === "WARNING").length;
  const errors = events.filter((e) => e.level === "ERROR").length;
  const actuators = Object.values(schema.actuators);
  const readers = Object.values(schema.readers);
  const readerCount = h ? Object.keys(h.readers).length : 0;
  const readersDown = h ? Object.values(h.readers).filter((r) => !r.running).length : 0;
  const warnings = hashFor("events", null, null, { level: "WARNING" });


  // Freshness for stale detection (DESIGN-SPEC.md §2/B-3): the channel's reader period; the
  // sample times come from the store (a Readout in `source` mode fills them in itself, in rig time).
  const readerOfSource = new Map<string, string>();
  for (const reader of readers) for (const src of reader.sources) readerOfSource.set(src.name, reader.name);
  const periodOf = (channel: ChannelOut) => periods[readerOfSource.get(channel.source) ?? ""];

  // Alarm summary (research §6): device conditions plus channels outside their warn/alarm band —
  // a rig with every tile amber must not read "0 conditions". `/api/health` has no channel-band
  // state, so this is computed here from the store's latest values (re-rendering only when a
  // count changes); a server-side summary that folds this in (so the app-bar chip need not
  // re-derive it) would be better.
  const activeConditions = (h?.conditions ?? []).filter((c) => c.level >= 30);
  // `/api/health` carries the summary (`alarms`: channels plus device conditions) on current
  // daemons; an older one leaves the channels to the store and the conditions to this page.
  const local = useAlarmSummary(h?.alarms ? [] : channels, periodOf);
  const amberChannels = h?.alarms ? h.alarms.warn : local.warn + activeConditions.filter((c) => c.level < 40).length;
  const redChannels = h?.alarms ? h.alarms.alarm : local.alarm + activeConditions.filter((c) => c.level >= 40).length;
  const conditionsCount = amberChannels + redChannels;
  const worstDeviceLevel = Math.max(0, ...activeConditions.map((c) => c.level));
  const conditionsTone: Tone | undefined = redChannels > 0 || worstDeviceLevel >= 40 ? "bad" : conditionsCount > 0 ? "warn" : undefined;

  return (
    <>
      <PageBar end={<ChartControls {...charts} unit={sources[0]?.channels[0]?.unit} />}>
        <GroupingSelect value={grouping} onChange={group} />
      </PageBar>
      <div className="grid stats">
        <Stat icon={h?.ok ? OkIcon : WarnIcon} label="rig" value={h ? (h.ok ? "ok" : "fault") : "…"} tone={h ? (h.ok ? "ok" : "bad") : undefined} href={hashFor("events")} />
        <Stat icon={SourceIcon} label="recording" value={h ? (h.recording ? "on" : "off") : "…"} tone={h?.recording ? "ok" : undefined} href={hashFor("sessions")} />
        <Stat icon={PAGE_ICONS.sources} label="readers" value={h ? `${readerCount - readersDown}/${readerCount} running` : "…"} tone={readersDown ? "warn" : undefined} href={hashFor("readers")} />
        <Stat icon={PAGE_ICONS.controllers} label="controllers" value={h ? Object.keys(h.loops).length : "…"} href={hashFor("controllers")} />
        <Stat icon={WarnIcon} label="conditions" value={h ? conditionsCount : "…"} tone={h ? conditionsTone : undefined} href={warnings} />
        <Stat icon={CircleIcon} label="signals waiting" value={h ? h.signals.length : "…"} href={hashFor("events")} />
        <Stat icon={PAGE_ICONS.events} label="events" value={`${problems} warn/error of ${events.length}`} tone={errors ? "bad" : problems ? "warn" : undefined} href={hashFor("events")} />
        <Stat icon={PAGE_ICONS.sessions} label="uptime" value={h ? uptime(h.uptime_s) : "…"} />
      </div>

      {h && h.conditions.length > 0 && (
        <Stack spacing={0.5} sx={{ mb: 3 }}>
          {h.conditions.map((c) => (
            <Alert key={c.kind + c.since_ns} severity={c.level >= 40 ? "error" : "warning"}>
              <strong>{c.kind}</strong> {c.message}
            </Alert>
          ))}
        </Stack>
      )}

      <section>
        <SectionHead icon={PAGE_ICONS.sources} title="Sources" count={sources.length} end={<GoTo label="charts" onClick={() => onOpen("sources")} />} />
        {sources.length === 0 && <StateBlock state="empty" message="No sources declared. Add a reader to the rig file to see channels here." action={{ label: "View sources page", onClick: () => onOpen("sources") }} />}
        {sources.length > 0 && (
        <div className="grid">
        {grouping === "channel" && channels.map((c) => <Tile key={channelKey(c)} channel={c} live={live} windowS={windowS} every={every} showSource exportHref={stored.series(c)} periodS={periodOf(c)} />)}
        {grouping === "unit" &&
          groupByUnit(channels).map(({ unit, channels: cs }) => (
            <div key={unit} className="c12 unit-group">
              <Stack direction="row" alignItems="center" spacing={1} className="source-head">
                <Typography fontWeight={600}>{unit}</Typography>
                <Typography variant="body2" color="text.secondary">
                  {cs.length} channel{cs.length === 1 ? "" : "s"}
                </Typography>
              </Stack>
              <div className="grid">
                {cs.map((c) => <Tile key={channelKey(c)} channel={c} live={live} windowS={windowS} every={every} showSource exportHref={stored.series(c)} periodS={periodOf(c)} />)}
              </div>
            </div>
          ))}
        {grouping === "source" && sources.map((src) => {
          const href = hrefFor({ kind: "source", name: src.name });
          // A quarter of the row per channel, up to the whole row: its tiles then sit beside the other sources' tiles.
          const span = Math.min(12, 3 * Math.max(1, src.channels.length));
          return (
            <div key={src.name} className={`source-group c${span}`}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                className="source-head"
                sx={{ cursor: "pointer", borderRadius: 1, "&:hover .source-name": { textDecoration: "underline" } }}
                onClick={clickThrough(href)}
              >
                <SourceIcon fontSize="inherit" sx={{ color: "text.disabled", alignSelf: "center" }} />
                <Typography fontWeight={600}>
                  <Link href={href} underline="hover" color="inherit" className="source-name">
                    {src.label ?? src.name}
                  </Link>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {src.label && `${src.name} · `}
                  <LastSample source={src} />
                </Typography>
              </Stack>
              <div className="tiles">
                {src.channels.map((c) => (
                  <Tile key={channelKey(c)} channel={c} live={live} windowS={windowS} every={every} showSource={false} exportHref={stored.series(c)} periodS={periodOf(c)} className="tile-with-icon" />
                ))}
              </div>
            </div>
          );
        })}
        {sources.length > 0 && (
          <div className="c12 fb-charts">
            <UnitCharts channels={channels} source={live} sources={sources} height="auto" windowS={windowS} yScale={yScale} every={every} exportHref={stored.channels} />
          </div>
        )}
        </div>
        )}
      </section>

      <section>
        <SectionHead icon={PAGE_ICONS.actuators} title="Actuators" count={actuators.length} end={<GoTo label="commands" onClick={() => onOpen("actuators")} />} />
        {actuators.length === 0 && <StateBlock state="empty" message="No actuators declared. Add one to the rig file to command it here." action={{ label: "View actuators page", onClick: () => onOpen("actuators") }} />}
        {actuators.length > 0 && (
        <div className="grid">
          {actuators.map((a) => (
            <ActuatorCard key={a.name} actuator={a} />
          ))}
        </div>
        )}
      </section>

      <section>
        <SectionHead icon={SourceIcon} title="Readers" count={readers.length} end={<GoTo label="all readers" onClick={() => onOpen("readers")} />} />
        {readers.length === 0 && <StateBlock state="empty" message="No readers declared. Add one to the rig file to see it here." action={{ label: "View readers page", onClick: () => onOpen("readers") }} />}
        {readers.length > 0 && <ReaderCards readers={readers} fallback={h?.readers} />}
      </section>
    </>
  );
}
