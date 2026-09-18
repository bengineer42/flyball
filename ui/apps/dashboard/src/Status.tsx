import { Chip, Link, Tooltip, useMediaQuery, useTheme, type ChipProps } from "@mui/material";
import RadioButtonCheckedIcon from "@mui/icons-material/RadioButtonChecked";
import { useHealth, type StreamStatus } from "@flyball/react";
import { sessionName, stepOf, type Programmer, type Recording } from "./model.js";
import { PAGE_ICONS, WarnIcon, ErrorIcon, OkIcon, type IconComponent } from "./icons.js";
import { hashFor, hrefFor } from "./router.js";

type Colour = NonNullable<ChipProps["color"]>;
/** The visible chip label for a long message: the tooltip (`Line[]`) carries the rest. */
const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n)}…` : s);
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
        sx={{
          maxWidth: narrow ? 180 : 360,
          "& .MuiChip-label": { fontVariantNumeric: "tabular-nums", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
        }}
      />
    </Tooltip>
  );
}

/** A chip with its own coloured record symbol (a filled circle in a ring), for a state no MUI `color` reads as "quiet": recording. */
function DotChip({ dotColour, label, short, href, title }: { dotColour: string; label: string; short?: string; href?: string; title: string }) {
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
  recording: Recording;
  programmer: Programmer;
  /** Every stream the app opens; folded into one live/reconnecting/offline chip. */
  streams: StreamStatus[];
}

/**
 * The app bar's condition summary: an always-present alarm chip, one folded
 * server-connection chip, recording, the running program, and polled devices
 * only when one is not running. Every healthy state is `color="default"`
 * outlined — colour is reserved for abnormal conditions (ISA-101 §0) — except
 * the server-connection chip, deliberately `color="success"` (green) when
 * live, per Ben's explicit ask: it is the one state where "still connected"
 * is worth a positive, not just quiet, signal.
 */
export function Status({ recording, programmer, streams }: StatusProps) {
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

  const offline = streams.some((s) => s === "closed");
  const reconnecting = !offline && streams.some((s) => s !== "open");
  const liveState = offline ? "offline" : reconnecting ? "reconnecting" : "live";
  const liveIcon = offline ? ErrorIcon : reconnecting ? WarnIcon : OkIcon;
  const liveColour: Colour = offline ? "error" : reconnecting ? "warning" : "success";

  const devices = Object.entries(h?.devices ?? {});
  const running = devices.filter(([, d]) => d.running).length;
  const deviceLines: Line[] = devices.map(([name, d]) => ({ name, href: hrefFor({ kind: "device", name }), state: d.running ? "running" : "stopped" }));

  const open = recording.data;

  return (
    <>
      <StatusChip
        icon={WarnIcon}
        full={`${conditionCount} condition${conditionCount === 1 ? "" : "s"}`}
        short={`${conditionCount}`}
        colour={alarmColour}
        lines={[
          ...active.map((c) => ({ name: `${c.device} ${c.kind}`, href: hrefFor({ kind: "device", name: c.device }), state: c.message })),
          ...(amber > 0 ? [{ name: "signals", state: `${amber} outside their warn band` }] : []),
          ...(red > 0 ? [{ name: "signals", state: `${red} outside their alarm band` }] : []),
        ]}
        href={hashFor("events")}
      />
      <DotChip
        dotColour={open ? "error.main" : "text.disabled"}
        label={open ? sessionName(open) : "not recording"}
        short={open ? `#${open.id}` : ""}
        title={open ? `recording ${sessionName(open)}` : "not recording"}
        href={hashFor("sessions")}
      />
      {(programmer.data?.running || programmer.data?.failed) && (
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
        />
      )}
      <StatusChip
        icon={liveIcon}
        full={`server ${liveState}`}
        short={liveState === "live" ? "" : liveState}
        colour={liveColour}
        lines={[{ name: "server", state: liveState === "live" ? "connected" : liveState === "reconnecting" ? "reconnecting…" : "not responding" }]}
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
