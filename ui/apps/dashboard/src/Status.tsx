import { Badge, Chip, Link, Tooltip, useMediaQuery, useTheme, type ChipProps } from "@mui/material";
import RadioButtonCheckedIcon from "@mui/icons-material/RadioButtonChecked";
import PauseIcon from "@mui/icons-material/Pause";
import { useHealth, type PlaybackHook, type StreamStatus, type SocketStream } from "@flyball/react";
import { sessionName, stepOf, type Programmer, type Recording } from "./model.js";
import { PAGE_ICONS, WarnIcon, ErrorIcon, OkIcon, type IconComponent } from "./icons.js";
import { hashFor, hrefFor } from "./router.js";
import { hms } from "./PlaybackBar.js";

type Colour = NonNullable<ChipProps["color"]>;
/** The visible chip label for a long message: the tooltip (`Line[]`) carries the rest. */
const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n)}…` : s);
/** One tooltip line: a name (a link when the app has a page for it) and its state. */
interface Line {
  name: string;
  href?: string;
  state: string;
}

/** A status chip: icon, a short label (count only on narrow screens), a tooltip listing the names.
 * Exported as the app bar's one chip shape: a later chip (a rig-health status, a stop latch) takes
 * the same props rather than a new component.
 * `minWidth`: when a chip's own label text varies by state (not by open-ended data like a name),
 * pass the widest of its own possible labels so it holds one size across its states and doesn't
 * reflow its neighbours every time it changes -- chips should be a single size
 * "within reason" (a genuinely unbounded value, like a session name, is out of scope for this). */
export function StatusChip({ icon: Icon, full, short, colour, lines, href, minWidth, testId }: { icon: IconComponent; full: string; short: string; colour: Colour; lines: Line[]; href?: string; minWidth?: string; testId?: string }) {
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
        data-testid={testId}
        sx={{
          maxWidth: narrow ? 180 : 360,
          minWidth: narrow ? undefined : minWidth,
          justifyContent: minWidth ? "flex-start" : undefined,
          "& .MuiChip-label": { fontVariantNumeric: "tabular-nums", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
        }}
      />
    </Tooltip>
  );
}

/** A chip with its own coloured record symbol (a filled circle in a ring), for a state no MUI `color` reads as "quiet": recording. */
function DotChip({ dotColour, label, short, href, title, testId }: { dotColour: string; label: string; short?: string; href?: string; title: string; testId?: string }) {
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down("sm"));
  return (
    <Tooltip title={title}>
      <Chip
        variant="outlined"
        color="default"
        icon={<RadioButtonCheckedIcon sx={{ fontSize: 14, "&&": { color: dotColour } }} />}
        label={narrow && short !== undefined ? short : label}
        sx={narrow && short === "" ? { "& .MuiChip-label": { display: "none" }, "& .MuiChip-icon": { m: 0 } } : undefined}
        component={href ? "a" : "div"}
        href={href}
        clickable={Boolean(href)}
        data-testid={testId}
      />
    </Tooltip>
  );
}

/**
 * The rig is simulated: always shown then, since it is the way to the Simulation page (there is no
 * sidebar). Says the clock's speed when it is not real time.
 */
export const SimChip = ({ speed }: { speed: number | undefined }) => {
  const scaled = speed !== undefined && speed !== 1;
  const label = scaled ? `sim ×${Number.isInteger(speed) ? speed : speed.toFixed(1).replace(/\.0$/, "")}` : "sim";
  return (
    <Tooltip title={scaled ? `simulated clock runs at ${label.slice(4)} real time` : "simulated rig: the Simulation page"}>
      <Chip variant="outlined" color="default" icon={<PAGE_ICONS.simulation fontSize="small" />} label={label} component="a" href={hashFor("simulation")} clickable data-testid="sim-chip" />
    </Tooltip>
  );
};

/**
 * Playback is paused and the transport is out of sight (it lives on the
 * Simulation page, the pause reaches every page): the moment being shown, in
 * the bar's amber, and a click to go live. Nothing while live -- conditional
 * like the program and devices chips beside it, in the same reserved row.
 */
export const PausedChip = ({ playback }: { playback: PlaybackHook }) => {
  if (!playback.paused) return null;
  return (
    <Tooltip title="playback paused — click to go live">
      <Chip
        variant="outlined"
        color="warning"
        icon={<PauseIcon fontSize="small" />}
        label={hms(playback.atS)}
        onClick={() => playback.resume()}
        clickable
        data-testid="paused-chip"
        sx={{ "& .MuiChip-label": { fontVariantNumeric: "tabular-nums" } }}
      />
    </Tooltip>
  );
};

export interface StatusProps {
  recording: Recording;
  programmer: Programmer;
  /** Every stream the app opens; folded into one live/offline chip. */
  streams: StreamStatus[];
  /** The same streams, by name, so the chip's tooltip can say which one is the problem. */
  byStream: Readonly<Record<SocketStream, StreamStatus | "idle">>;
  /** Unread WARNING+ events (`useUnreadEvents`): a badge on the conditions chip, which leads to Events. */
  eventsUnread?: number;
}

const STREAM_LABEL: Record<SocketStream, string> = { samples: "readings", controllers: "controllers", activities: "activities", events: "events" };

/**
 * The app bar's condition summary, and with no sidebar also its navigation (D-053): an
 * always-present alarm chip (→ Events, carrying the unread badge), recording (→ Sessions),
 * the program (→ Programs, grey "no program" when idle), one folded server-connection chip,
 * and polled devices only when one is not running. Every healthy state is `color="default"`
 * outlined — colour is reserved for abnormal conditions (ISA-101 §0) — except
 * the server-connection chip, deliberately `color="success"` (green) when
 * live: it is the one state where "still connected" is worth a positive,
 * not just quiet, signal.
 */
export function Status({ recording, programmer, streams, byStream, eventsUnread = 0 }: StatusProps) {
  const health = useHealth(5000);
  const h = health.data;

  // Alarm summary (research §6): `/api/health.alarms` counts the signals outside their warn/alarm
  // band plus the device conditions at WARNING/ERROR, so the chip reads the same as the Overview tile.
  const active = (h?.conditions ?? []).filter((c) => c.level >= 30);
  const amber = h?.alarms.warn ?? 0;
  const red = h?.alarms.alarm ?? 0;
  const conditionCount = amber + red;
  const worst = h?.alarms.max_level ?? 0;
  const alarmColour: Colour = red > 0 || worst >= 40 ? "error" : amber > 0 || worst >= 30 ? "warning" : "default";

  // Two states, not three: a mid-outage "reconnecting" reads as less serious than it is.
  // Anything not fully open is red -- no amber middle ground.
  const live = streams.length > 0 && streams.every((s) => s === "open");
  const liveIcon = live ? OkIcon : ErrorIcon;
  const liveColour: Colour = live ? "success" : "error";
  const down = (Object.entries(byStream) as [SocketStream, StreamStatus | "idle"][]).filter(([, s]) => s !== "open" && s !== "idle");

  const devices = Object.entries(h?.devices ?? {});
  const running = devices.filter(([, d]) => d.running).length;
  const deviceLines: Line[] = devices.map(([name, d]) => ({ name, href: hrefFor({ kind: "device", name }), state: d.running ? "running" : "stopped" }));

  const open = recording.data;
  const conditions = (
    <StatusChip
      icon={WarnIcon}
      full={`${conditionCount} condition${conditionCount === 1 ? "" : "s"}`}
      short={`${conditionCount}`}
      colour={alarmColour}
      minWidth="7rem"
      lines={[
        ...active.map((c) => ({ name: `${c.device} ${c.kind}`, href: hrefFor({ kind: "device", name: c.device }), state: c.message })),
        ...(amber > 0 ? [{ name: "signals", state: `${amber} outside their warn band` }] : []),
        ...(red > 0 ? [{ name: "signals", state: `${red} outside their alarm band` }] : []),
      ]}
      href={hashFor("events")}
      testId="conditions-chip"
    />
  );

  return (
    <>
      {eventsUnread > 0 ? (
        /* Pulled in onto the chip's corner: the chip row scrolls sideways, and a scroller clips whatever pokes out above it. */
        <Badge badgeContent={eventsUnread} max={99} color="warning" slotProps={{ badge: { "data-testid": "events-unread-badge" } as object }} sx={{ "& .MuiBadge-badge": { top: 6, right: 6 } }}>
          {conditions}
        </Badge>
      ) : (
        conditions
      )}
      <DotChip
        dotColour={open ? "error.main" : "text.disabled"}
        label={open ? sessionName(open) : "not recording"}
        short={open ? `#${open.id}` : ""}
        title={open ? `recording ${sessionName(open)}` : "not recording"}
        href={hashFor("sessions")}
        testId="recording-chip"
      />
      {programmer.data?.running || programmer.data?.failed ? (
        <StatusChip
          icon={PAGE_ICONS.programs}
          full={
            programmer.data.running
              ? `program: ${programmer.data.command ?? "…"} step ${stepOf(programmer.data)}`
              : `program failed${programmer.data.error ? ` · ${truncate(programmer.data.error, 60)}` : ""}`
          }
          short={programmer.data.running ? stepOf(programmer.data) : "failed"}
          colour={programmer.data.failed ? "error" : "default"}
          lines={!programmer.data.running && programmer.data.error ? [{ name: "error", state: programmer.data.error }] : []}
          href={hashFor("programs")}
          testId="program-chip"
        />
      ) : (
        <StatusChip icon={PAGE_ICONS.programs} full="no program" short="idle" colour="default" lines={[]} href={hashFor("programs")} testId="program-chip" />
      )}
      <StatusChip
        icon={liveIcon}
        full="server"
        short="server"
        colour={liveColour}
        lines={
          live
            ? [{ name: "server", state: "connected" }]
            : down.length > 0
              ? down.map(([stream, status]) => ({ name: STREAM_LABEL[stream], state: status === "idle" ? "not subscribed" : status === "open" ? "connected" : "disconnected, reconnecting…" }))
              : [{ name: "server", state: "disconnected" }]
        }
      />
      {devices.length > 0 && running < devices.length && (
        <StatusChip
          icon={PAGE_ICONS.devices}
          full={`devices ${running}/${devices.length} running`}
          short={`${running}/${devices.length}`}
          colour="error"
          lines={deviceLines}
          href={hashFor("devices")}
        />
      )}
    </>
  );
}
