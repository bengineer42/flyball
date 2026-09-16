import { Chip, Link, Tooltip, useMediaQuery, useTheme, type ChipProps } from "@mui/material";
import FiberManualRecordIcon from "@mui/icons-material/FiberManualRecord";
import { useAlarmSummary, useHealth, useQuery, useReaderPeriods, useRig, useRigSchema, useSources, type StreamStatus } from "@flyball/react";
import type { ActuatorSchema, ChannelOut, DeviceState } from "@flyball/client";
import { sessionName, stepOf, type Programmer, type Recording } from "./model.js";
import { PAGE_ICONS, SourceIcon, WarnIcon, type IconComponent } from "./icons.js";
import { hashFor, hrefFor } from "./router.js";

type Colour = NonNullable<ChipProps["color"]>;
/** One tooltip line: a name (a link when the app has a page for it) and its state. */
interface Line {
  name: string;
  href?: string;
  state: string;
}

/** A status chip: icon, a short label (count only on narrow screens), a tooltip listing the names. */
function StatusChip({ icon: Icon, full, short, colour, lines, href }: { icon: IconComponent; full: string; short: string; colour: Colour; lines: Line[]; href?: string }) {
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("md"));
  const title = lines.length ? (
    <div>
      {lines.map((l) => (
        <div key={l.name}>
          {l.href ? (
            <Link href={l.href} color="inherit" underline="always">
              {l.name}
            </Link>
          ) : (
            l.name
          )}
          : {l.state}
        </div>
      ))}
    </div>
  ) : (
    full
  );
  return (
    <Tooltip title={title}>
      <Chip
        variant="outlined"
        color={colour}
        icon={<Icon fontSize="small" />}
        label={narrow ? short : full}
        component={href ? "a" : "div"}
        href={href}
        clickable={Boolean(href)}
        sx={{ "& .MuiChip-label": { fontVariantNumeric: "tabular-nums" } }}
      />
    </Tooltip>
  );
}

/** A chip with its own coloured dot, for the two states no MUI `color` reads as "quiet": recording (red dot or none) and stream health (grey/amber/red dot). */
function DotChip({ dotColour, label, href, title }: { dotColour: string; label: string; href?: string; title: string }) {
  return (
    <Tooltip title={title}>
      <Chip
        variant="outlined"
        color="default"
        icon={<FiberManualRecordIcon sx={{ fontSize: 10, "&&": { color: dotColour } }} />}
        label={label}
        component={href ? "a" : "div"}
        href={href}
        clickable={Boolean(href)}
      />
    </Tooltip>
  );
}

/** The simulated clock's speed when it is not real time; a link to the Simulation page. */
export const SimChip = ({ speed }: { speed: number | undefined }) => {
  if (speed === undefined || speed === 1) return null;
  const label = `sim ×${Number.isInteger(speed) ? speed : speed.toFixed(1).replace(/\.0$/, "")}`;
  return (
    <Tooltip title={`simulated clock runs at ${label.slice(4)} real time`}>
      <Chip variant="outlined" color="default" icon={<PAGE_ICONS.simulation fontSize="small" />} label={label} component="a" href={hashFor("simulation")} clickable />
    </Tooltip>
  );
};

export interface StatusProps {
  actuators: ActuatorSchema[];
  states: Record<string, DeviceState>;
  recording: Recording;
  programmer: Programmer;
  /** Every stream the app opens; folded into one live/reconnecting/offline chip. */
  streams: StreamStatus[];
}

/**
 * The app bar's condition summary: an always-present alarm chip, one folded
 * stream-health chip, recording, the running program, and readers only when
 * one is not running. Every healthy state is `color="default"` outlined —
 * colour is reserved for abnormal conditions (ISA-101 §0).
 */
export function Status({ recording, programmer, streams }: StatusProps) {
  const rig = useRig();
  const health = useHealth(5000);
  // Kept warm so the loops/actuators pages open with a populated cache; no chip reads it any more.
  useQuery(() => rig.loops(), [rig], { refreshMs: 5000 });

  // Alarm summary (research §6): device conditions plus channels outside their warn/alarm band —
  // `plant.toml`'s 18 amber channels must not read "0" here just because no device condition
  // fired. `/api/health.alarms` now carries this (backend, and already folds device conditions
  // into its warn/alarm counts — do not add `active.length` again on top of it); a client-side
  // fallback (from the store, device conditions added separately) covers an older daemon.
  const schema = useRigSchema();
  const sources = useSources();
  const channels = (sources.data ?? []).flatMap((s) => s.channels);
  // Periods only (not full reader runs): re-renders this chip less often — DESIGN-SPEC.md §2/B-3.
  const periods = useReaderPeriods();
  const readerOfSource = new Map<string, string>();
  for (const reader of Object.values(schema.data?.readers ?? {})) for (const src of reader.sources) readerOfSource.set(src.name, reader.name);
  const periodOf = (c: ChannelOut) => periods[readerOfSource.get(c.source) ?? ""] ?? undefined;
  // No channels (empty array) when the server already supplies `alarms`: this stops
  // `useAlarmSummary` subscribing every channel on the samples socket for a chip that would
  // then ignore it — otherwise `/ws/samples` stays open on every page, even ones with no chart.
  const clientSummary = useAlarmSummary(health.data?.alarms ? [] : channels, periodOf);
  const active = (health.data?.conditions ?? []).filter((c) => c.level >= 30);
  const amberChannels = health.data?.alarms?.warn ?? clientSummary.warn;
  const redChannels = health.data?.alarms?.alarm ?? clientSummary.alarm;
  const conditionCount = health.data?.alarms ? amberChannels + redChannels : active.length + amberChannels + redChannels;
  const worst = health.data?.alarms?.max_level ?? Math.max(0, ...active.map((c) => c.level));
  const alarmColour: Colour = redChannels > 0 || worst >= 40 ? "error" : amberChannels > 0 || worst >= 30 ? "warning" : "default";

  const offline = streams.some((s) => s === "closed");
  const reconnecting = !offline && streams.some((s) => s !== "open");
  const liveState = offline ? "offline" : reconnecting ? "reconnecting" : "live";
  const liveDot = offline ? "error.main" : reconnecting ? "warning.main" : "text.disabled";

  const readers = Object.entries(health.data?.readers ?? {});
  const running = readers.filter(([, r]) => r.running).length;
  const readerLines: Line[] = readers.map(([name, r]) => ({ name, href: hrefFor({ kind: "reader", name }), state: r.running ? "running" : "stopped" }));

  const open = recording.data;

  return (
    <>
      <StatusChip
        icon={WarnIcon}
        full={`${conditionCount} condition${conditionCount === 1 ? "" : "s"}`}
        short={`${conditionCount}`}
        colour={alarmColour}
        lines={[
          ...active.map((c) => ({ name: c.kind, state: c.message })),
          ...(amberChannels > 0 ? [{ name: "channels", state: `${amberChannels} outside their warn band` }] : []),
          ...(redChannels > 0 ? [{ name: "channels", state: `${redChannels} outside their alarm band` }] : []),
        ]}
        href={hashFor("events")}
      />
      <DotChip
        dotColour={open ? "error.main" : "text.disabled"}
        label={open ? sessionName(open) : "not recording"}
        title={open ? `recording ${sessionName(open)}` : "not recording"}
        href={hashFor("sessions")}
      />
      {(programmer.data?.running || programmer.data?.failed) && (
        <StatusChip
          icon={PAGE_ICONS.programs}
          full={
            programmer.data.running
              ? `program: ${programmer.data.command ?? "…"} step ${stepOf(programmer.data)}`
              : `program failed${programmer.data.error ? ` · ${programmer.data.error}` : ""}`
          }
          short={programmer.data.running ? stepOf(programmer.data) : "failed"}
          colour={programmer.data.failed ? "error" : "default"}
          lines={!programmer.data.running && programmer.data.error ? [{ name: "error", state: programmer.data.error }] : []}
          href={hashFor("programs")}
        />
      )}
      <DotChip dotColour={liveDot} label={liveState} title={`streams ${liveState}`} />
      {readers.length > 0 && running < readers.length && (
        <StatusChip
          icon={SourceIcon}
          full={`readers ${running}/${readers.length} running`}
          short={`${running}/${readers.length}`}
          colour="error"
          lines={readerLines}
          href={hashFor("readers")}
        />
      )}
    </>
  );
}
