import type { ReactNode } from "react";
import { Alert, Box, Button, ButtonBase, Chip, Link, Paper, Stack, Typography } from "@mui/material";
import { Readout, ObjectView, UnitCharts, useHealth, channelKey, type Traces } from "@flyball/react";
import type { DeviceState, RigEvent, RigSchema, SourceOut } from "@flyball/client";
import { CircleIcon, OkIcon, SourceIcon, WarnIcon, channelIcon, PAGE_ICONS, type IconComponent } from "../icons.js";
import { ChartControls, type ChartSettings } from "../YScaleSelect.js";
import { hashFor, hrefFor, type Page } from "../router.js";
import { DeviceCard, ReaderCard, clickThrough, clickableSx } from "../cards.js";
import { useReaderRuns } from "../model.js";

export interface OverviewProps extends ChartSettings {
  schema: RigSchema;
  sources: SourceOut[];
  traces: Traces;
  states: Record<string, DeviceState>;
  /** The held event log, for the warning/error count. */
  events: RigEvent[];
  onOpen(page: Page): void;
}

type Tone = "ok" | "warn" | "bad";
const TONE_COLOR: Record<Tone, string> = { ok: "success.main", warn: "warning.main", bad: "error.main" };

const uptime = (s: number) => {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : m ? `${m}m ${Math.floor(s % 60)}s` : `${Math.floor(s)}s`;
};

/** A stat tile; with `href` the whole tile is a link (focusable, middle-clickable). */
function Stat({ icon: Icon, label, value, tone, href }: { icon: IconComponent; label: string; value: ReactNode; tone?: Tone; href?: string }) {
  const body = (
    <>
      <Icon fontSize="small" sx={{ color: tone ? TONE_COLOR[tone] : "text.secondary" }} />
      <div>
        <Typography variant="overline" color="text.secondary" component="div" sx={{ lineHeight: 1.3 }}>
          {label}
        </Typography>
        <Typography fontWeight={600}>{value}</Typography>
      </div>
    </>
  );
  const sx = { display: "flex", alignItems: "center", gap: 1.5, px: 1.5, py: 1, borderColor: tone ? TONE_COLOR[tone] : undefined } as const;
  if (!href) return <Paper sx={sx}>{body}</Paper>;
  return (
    <ButtonBase component="a" href={href} sx={{ display: "block", textAlign: "left", borderRadius: 1, "&.Mui-focusVisible": { outline: 2, outlineColor: "primary.main" } }}>
      <Paper sx={{ ...sx, ...clickableSx, height: "100%" }}>{body}</Paper>
    </ButtonBase>
  );
}

function SectionHead({ icon: Icon, title, action, end }: { icon: IconComponent; title: string; action?: { label: string; onClick(): void }; end?: ReactNode }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }} flexWrap="wrap" useFlexGap>
      <Icon fontSize="small" sx={{ color: "text.disabled" }} />
      <Typography variant="h2" component="h2" color="text.secondary">
        {title}
      </Typography>
      {action && (
        <Button onClick={action.onClick} sx={{ py: 0 }}>
          {action.label} →
        </Button>
      )}
      {end && <Box sx={{ ml: "auto !important" }}>{end}</Box>}
    </Stack>
  );
}

/** Everything the rig knows about itself on one page: health strip, channels, actuators, readers. */
export function Overview({ schema, sources, traces, states, events, onOpen, ...charts }: OverviewProps) {
  const { windowS, yScale, every } = charts;
  const health = useHealth(5000);
  const h = health.data;
  // Live reader runs carry the period; health only says running or not.
  const runs = useReaderRuns();
  const problems = events.filter((e) => e.level === "ERROR" || e.level === "WARNING").length;
  const errors = events.filter((e) => e.level === "ERROR").length;
  const actuators = Object.values(schema.actuators);
  const readers = Object.values(schema.readers);
  const readerCount = h ? Object.keys(h.readers).length : 0;
  const readersDown = h ? Object.values(h.readers).filter((r) => !r.running).length : 0;
  const warnings = hashFor("events", null, null, { level: "WARNING" });

  return (
    <>
      <div className="stats">
        <Stat icon={h?.ok ? OkIcon : WarnIcon} label="rig" value={h ? (h.ok ? "ok" : "fault") : "…"} tone={h ? (h.ok ? "ok" : "bad") : undefined} href={hashFor("events")} />
        <Stat icon={SourceIcon} label="recording" value={h ? (h.recording ? "on" : "off") : "…"} tone={h?.recording ? "ok" : undefined} href={hashFor("sessions")} />
        <Stat icon={PAGE_ICONS.sources} label="readers" value={h ? `${readerCount - readersDown}/${readerCount} running` : "…"} tone={readersDown ? "warn" : undefined} href={hashFor("readers")} />
        <Stat icon={PAGE_ICONS.loops} label="loops" value={h ? Object.keys(h.loops).length : "…"} href={hashFor("loops")} />
        <Stat icon={WarnIcon} label="conditions" value={h ? h.conditions.length : "…"} tone={h?.conditions.length ? "warn" : undefined} href={warnings} />
        <Stat icon={CircleIcon} label="signals waiting" value={h ? h.signals.length : "…"} href={hashFor("events")} />
        <Stat icon={PAGE_ICONS.events} label="events" value={`${problems} warn/error of ${events.length}`} tone={errors ? "bad" : problems ? "warn" : undefined} href={hashFor("events")} />
        <Stat icon={PAGE_ICONS.sessions} label="uptime" value={h ? uptime(h.uptime_s) : "…"} />
      </div>

      {h && h.conditions.length > 0 && (
        <Stack spacing={0.5} sx={{ mb: 2 }}>
          {h.conditions.map((c) => (
            <Alert key={c.kind + c.since_ns} severity={c.level >= 40 ? "error" : "warning"}>
              <strong>{c.kind}</strong> {c.message}
            </Alert>
          ))}
        </Stack>
      )}

      <section>
        <SectionHead
          icon={PAGE_ICONS.sources}
          title="Sources"
          action={{ label: "charts", onClick: () => onOpen("sources") }}
          end={<ChartControls {...charts} unit={sources[0]?.channels[0]?.unit} />}
        />
        {sources.map((src) => {
          const first = src.channels[0] && traces[channelKey(src.channels[0])];
          const lastT = first && first.t.length ? first.t[first.t.length - 1]! : undefined;
          const href = hrefFor({ kind: "source", name: src.name });
          return (
            <div key={src.name} className="source-group">
              <Stack
                direction="row"
                alignItems="baseline"
                spacing={1}
                sx={{ mb: 1, cursor: "pointer", borderRadius: 1, "&:hover .source-name": { textDecoration: "underline" } }}
                onClick={clickThrough(href)}
              >
                <SourceIcon fontSize="inherit" sx={{ color: "text.disabled", alignSelf: "center" }} />
                <Typography fontWeight={600}>
                  <Link href={href} underline="hover" color="inherit" className="source-name">
                    {src.name}
                  </Link>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {lastT !== undefined ? `sample ${new Date(lastT * 1000).toLocaleTimeString()}` : "no sample yet"}
                  {src.latest && ` · #${src.latest.seq}`}
                </Typography>
              </Stack>
              <div className="tiles">
                {src.channels.map((c) => {
                  const trace = traces[channelKey(c)];
                  const Icon = channelIcon(c);
                  return (
                    <div key={channelKey(c)} className="tile-with-icon">
                      <Icon fontSize="small" className="tile-icon" />
                      <Readout channel={c} t={trace?.t ?? []} v={trace?.v ?? []} showSource={false} windowS={windowS} every={every} />
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
        {sources.length > 0 && (
          <Paper sx={{ p: 1.5 }}>
            <div className="tiles tiles-wide">
              <UnitCharts channels={sources.flatMap((s) => s.channels)} traces={traces} height={160} windowS={windowS} yScale={yScale} every={every} />
            </div>
          </Paper>
        )}
        {sources.length === 0 && <Typography color="text.secondary">no sources declared</Typography>}
      </section>

      <section>
        <SectionHead icon={PAGE_ICONS.actuators} title="Actuators" action={{ label: "commands", onClick: () => onOpen("actuators") }} />
        <div className="tiles tiles-wide">
          {actuators.map((a) => {
            const state = states[a.name];
            const conditions = state?.conditions ?? [];
            return (
              <DeviceCard
                key={a.name}
                icon={PAGE_ICONS.actuators}
                name={a.name}
                href={hrefFor({ kind: "actuator", name: a.name })}
                type={a.type}
                chip={
                  conditions.length > 0 ? (
                    <Chip label={conditions.map((c) => c.kind).join(", ")} color="warning" variant="outlined" />
                  ) : (
                    <Chip label="ok" color="success" variant="outlined" />
                  )
                }
              >
                <ObjectView schema={a.state} value={state} omit={["conditions"]} />
              </DeviceCard>
            );
          })}
          {actuators.length === 0 && <Typography color="text.secondary">no actuators attached</Typography>}
        </div>
      </section>

      <section>
        <SectionHead icon={SourceIcon} title="Readers" action={{ label: "all readers", onClick: () => onOpen("readers") }} />
        <div className="tiles tiles-wide">
          {readers.map((r) => (
            <ReaderCard key={r.name} reader={r} run={runs[r.name] ?? h?.readers[r.name]} />
          ))}
          {readers.length === 0 && <Typography color="text.secondary">no readers attached</Typography>}
        </div>
      </section>
    </>
  );
}
