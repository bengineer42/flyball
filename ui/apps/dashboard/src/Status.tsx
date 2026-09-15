import { Chip, Link, Tooltip, useMediaQuery, useTheme, type ChipProps } from "@mui/material";
import FiberManualRecordIcon from "@mui/icons-material/FiberManualRecord";
import { useHealth, useQuery, useRig, type StreamStatus } from "@flyball/react";
import type { ActuatorSchema, DeviceState } from "@flyball/client";
import { sessionName, stepOf, type Programmer, type Recording } from "./model.js";
import { PAGE_ICONS, SourceIcon, type IconComponent } from "./icons.js";
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

/** A stream's state: green dot open, red dot otherwise. */
export const StreamChip = ({ status, label }: { status: StreamStatus | string; label: string }) => {
  const ok = status === "open";
  return (
    <Tooltip title={`${label} stream ${status}`}>
      <Chip
        variant="outlined"
        label={label}
        icon={<FiberManualRecordIcon sx={{ fontSize: 10, "&&": { color: ok ? "success.main" : "error.main" } }} />}
        sx={{ display: { xs: "none", md: "inline-flex" } }}
      />
    </Tooltip>
  );
};

/** The simulated clock's speed when it is not real time; a link to the Simulation page. */
export const SimChip = ({ speed }: { speed: number | undefined }) => {
  if (speed === undefined || speed === 1) return null;
  const label = `sim ×${Number.isInteger(speed) ? speed : speed.toFixed(1).replace(/\.0$/, "")}`;
  return (
    <Tooltip title={`simulated clock runs at ${label.slice(4)} real time`}>
      <Chip variant="outlined" color="info" icon={<PAGE_ICONS.simulation fontSize="small" />} label={label} component="a" href={hashFor("simulation")} clickable />
    </Tooltip>
  );
};

export interface StatusProps {
  actuators: ActuatorSchema[];
  states: Record<string, DeviceState>;
  recording: Recording;
  programmer: Programmer;
}

/** What is running, at a glance: actuators, readers, recording, loops. All from the library's hooks. */
export function Status({ actuators, states, recording, programmer }: StatusProps) {
  const rig = useRig();
  const health = useHealth(5000);
  const loops = useQuery(() => rig.loops(), [rig], { refreshMs: 5000 });

  // Actuators: n of m without an ERROR-level condition; amber on any WARNING, red on any ERROR.
  const levels = actuators.map((a) => ({ name: a.name, level: Math.max(0, ...(states[a.name]?.conditions ?? []).map((c) => c.level)) }));
  const okCount = levels.filter((l) => l.level < 40).length;
  const worst = Math.max(0, ...levels.map((l) => l.level));
  const actuatorColour: Colour = worst >= 40 ? "error" : worst >= 30 ? "warning" : okCount === actuators.length ? "success" : "default";
  const actuatorLines: Line[] = levels.map((l) => ({
    name: l.name,
    href: hrefFor({ kind: "actuator", name: l.name }),
    state: l.level >= 40 ? "error" : l.level >= 30 ? "warning" : states[l.name] ? "ok" : "no state yet",
  }));

  const readers = Object.entries(health.data?.readers ?? {});
  const running = readers.filter(([, r]) => r.running).length;
  const readerLines: Line[] = readers.map(([name, r]) => ({ name, href: hrefFor({ kind: "reader", name }), state: r.running ? "running" : "stopped" }));

  const regulating = loops.data?.filter((l) => l.mode === "regulating").length ?? 0;
  const loopLines: Line[] = loops.data?.map((l) => ({ name: l.name, href: hrefFor({ kind: "loop", name: l.name }), state: l.mode })) ?? [];

  const open = recording.data;

  return (
    <>
      <StatusChip icon={PAGE_ICONS.actuators} full={`actuators ${okCount}/${actuators.length}`} short={`${okCount}/${actuators.length}`} colour={actuatorColour} lines={actuatorLines} href={hashFor("actuators")} />
      <StatusChip
        icon={SourceIcon}
        full={`readers ${running}/${readers.length} running`}
        short={`${running}/${readers.length}`}
        colour={health.data ? (running === readers.length ? "success" : "error") : "default"}
        lines={readerLines}
        href={hashFor("readers")}
      />
      <StatusChip
        icon={PAGE_ICONS.sessions}
        full={open ? `recording ${sessionName(open)}` : "not recording"}
        short={open ? "rec" : "—"}
        colour={open ? "success" : "default"}
        lines={open ? [{ name: sessionName(open), href: hrefFor({ kind: "session", name: String(open.id) }), state: `since ${new Date(open.start_ns / 1e6).toLocaleTimeString()}` }] : []}
        href={hashFor("sessions")}
      />
      {programmer.data?.running && (
        <StatusChip
          icon={PAGE_ICONS.programs}
          full={`program: ${programmer.data.command ?? "…"} step ${stepOf(programmer.data)}`}
          short={stepOf(programmer.data)}
          colour="success"
          lines={[]}
          href={hashFor("programs")}
        />
      )}
      <StatusChip
        icon={PAGE_ICONS.loops}
        full={`loops ${regulating} regulating`}
        short={`${regulating}`}
        colour={regulating > 0 ? "success" : "default"}
        lines={loopLines.length ? loopLines : [{ name: "no loops", state: loops.data ? "none attached" : "…" }]}
        href={hashFor("loops")}
      />
    </>
  );
}
