import { useEffect, useState, type FormEvent } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  LinearProgress,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { Form as MuiForm } from "@rjsf/mui";
import { CommandForm, DevicePanel, useCommands, useRigSchema, useSimulation, type SimulationHook } from "@flyball/react";
import type { DeviceOut, DeviceSchema, SimulationPlant } from "@flyball/client";
import { describeDevice, describeSimParam, fixed } from "@flyball/client";
import { useNow } from "../time.js";
import { StateBlock } from "../cards.js";

/** The speeds on the buttons; anything else goes in the box beside them. */
const SPEEDS = [0.5, 1, 2, 5, 10, 50];
const times = (speed: number | null | undefined) => (typeof speed !== "number" ? "—" : `×${Number.isInteger(speed) ? speed : speed.toFixed(1).replace(/\.0$/, "")}`);

/**
 * Where the rig's time is: `now_ns` as last fetched, run forward at `speed`
 * since, so the readout ticks between polls without asking the server.
 */
function SimClock({ nowNs, speed, fetchedAt }: { nowNs: number; speed: number; fetchedAt: number }) {
  const now = useNow(250);
  const simMs = nowNs / 1e6 + (now - fetchedAt) * speed;
  return (
    <Typography component="span" sx={{ fontVariantNumeric: "tabular-nums" }}>
      {new Date(simMs).toLocaleTimeString()}
    </Typography>
  );
}

/** The time-scale control: one button per common speed, a box for any other, and the readout. */
function SpeedControl({ sim }: { sim: SimulationHook }) {
  const simulation = sim.simulation!;
  const { speed, stepped } = simulation.clock;
  const [fetchedAt, setFetchedAt] = useState(Date.now);
  const [custom, setCustom] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<number | null>(null);
  // Re-anchor the ticking clock when a fresh `/api/sim` arrives. Keyed on the
  // reported instant, not the object: the hook hands out a new object every
  // render, and setting state on every render is an update loop.
  useEffect(() => setFetchedAt(Date.now()), [simulation.clock.now_ns]);

  const apply = async (next: number) => {
    setError(null);
    setPending(next);
    try {
      await sim.setSpeed(next);
      setCustom("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPending(null);
    }
  };
  const submitCustom = (e: FormEvent) => {
    e.preventDefault();
    const value = Number(custom);
    if (Number.isFinite(value) && value > 0) void apply(value);
    else setError("a speed is a positive number");
  };

  return (
    <Paper sx={{ p: 3, mb: "16px" }} data-testid="speed-control">
      <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 1.125 }}>
        Time scale
      </Typography>
      {stepped ? (
        <Alert severity="info" sx={{ mb: 1.5 }}>
          The clock is stepped: time moves only when a program or a test advances it, so it has no speed.
        </Alert>
      ) : null}
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ xs: "flex-start", sm: "center" }} useFlexGap flexWrap="wrap">
        <ToggleButtonGroup
          exclusive
          size="small"
          color="primary"
          value={SPEEDS.includes(speed) ? speed : null}
          disabled={stepped || pending !== null}
          onChange={(_, next: number | null) => {
            if (next !== null) void apply(next);
          }}
          aria-label="time scale"
        >
          {SPEEDS.map((s) => (
            <ToggleButton key={s} value={s} sx={{ px: 2.25, fontWeight: s === speed ? 700 : 400 }} aria-label={times(s)}>
              {times(s)}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        <Box component="form" onSubmit={submitCustom} sx={{ display: "flex", gap: 1.125, alignItems: "center" }}>
          <TextField
            size="small"
            label="other"
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            inputProps={{ inputMode: "decimal", "aria-label": "other speed", style: { width: "5em" } }}
            disabled={stepped || pending !== null}
          />
          <Button type="submit" size="small" variant="outlined" disabled={stepped || pending !== null || custom === ""}>
            set
          </Button>
        </Box>
        <Typography color="text.secondary" sx={{ ml: { sm: "auto" } }} data-testid="speed-readout">
          sim time: <SimClock nowNs={simulation.clock.now_ns} speed={stepped ? 0 : speed} fetchedAt={fetchedAt} /> · running{" "}
          <Typography component="span" color="text.primary" fontWeight={600}>
            {times(speed)}
          </Typography>{" "}
          real time
          {simulation.clock.measured != null && Math.abs(simulation.clock.measured - speed) > 0.05 * speed && (
            <Typography component="span" variant="body2" color="warning.main">
              {" "}
              (measured {times(Math.round(simulation.clock.measured * 10) / 10)})
            </Typography>
          )}
        </Typography>
      </Stack>
      {error && (
        <Alert severity="error" sx={{ mt: 1.5 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
    </Paper>
  );
}

/** One number per port: `{"": input}` for a single-port plant, the ports for a many-port one. */
const ports = (one: number | null | undefined, many: Record<string, number | null | undefined> | null | undefined): Array<[string, number | null]> =>
  many ? Object.entries(many).map(([k, v]) => [k, typeof v === "number" ? v : null]) : typeof one === "number" ? [["", one]] : [];

/** A port's value; "—" when the simulation has none for it (a plant not yet stepped, a missing field). */
const PortList = ({ values, digits }: { values: Array<[string, number | null | undefined]>; digits: number }) => (
  <Stack component="span" sx={{ fontVariantNumeric: "tabular-nums", alignItems: "flex-end" }}>
    {values.length === 0 ? (
      <Typography component="span" color="text.disabled">
        —
      </Typography>
    ) : (
      values.map(([port, v]) => (
        <span key={port}>
          {port && (
            <Typography component="span" variant="body2" color="text.secondary" sx={{ mr: 0.75 }}>
              {port}
            </Typography>
          )}
          {typeof v === "number" && Number.isFinite(v) ? fixed(v, digits) : "—"}
        </span>
      ))
    )}
  </Stack>
);

/** The `sim_*` plants a rig file declared, as `/api/sim` reports them: config, and where each port is now. */
function Plants({ plants }: { plants: Record<string, SimulationPlant> }) {
  const names = Object.keys(plants);
  if (names.length === 0) return null;
  return (
    <Paper sx={{ p: 3, mb: "16px" }}>
      <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 0.75 }}>
        Plants
      </Typography>
      {/* Wide on purpose (one column per port); scrolls inside the card rather than the page on a phone.
          `fb-scroll-shadow-x` (styles.css) fades in a right-edge shadow while columns remain off-screen
          and clears once scrolled to the end, so a phone width has a cue that the row continues. */}
      <Box className="fb-scroll-shadow-x" sx={{ overflowX: "auto" }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>plant</TableCell>
            <TableCell>kind</TableCell>
            <TableCell>parameters</TableCell>
            <TableCell align="right">input</TableCell>
            <TableCell align="right">output</TableCell>
            <TableCell align="right">read (by signal)</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {names.map((name) => {
            const plant = plants[name]!;
            const { kind, tag, model, ...rest } = plant.config;
            const live = plant.links ?? {};
            const readings = Object.entries(plant.readings ?? {});
            return (
              <TableRow key={name}>
                <TableCell>
                  <code>{name}</code>
                </TableCell>
                <TableCell>
                  {kind || tag ? describeDevice(String(kind ?? tag)) : ""}
                  {typeof model === "string" && (
                    <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 0.75 }}>
                      {model}
                    </Typography>
                  )}
                </TableCell>
                <TableCell sx={{ color: "text.secondary", fontSize: "0.85em" }}>
                  <Stack direction="row" flexWrap="wrap" useFlexGap spacing={1}>
                    {Object.entries(rest)
                      .filter(([k, v]) => !(k in live) && v !== null && v !== undefined && typeof v !== "object")
                      .map(([k, v]) => {
                        const { label, unit, hint } = describeSimParam(k);
                        return (
                          <span key={k} title={hint}>
                            {label} {String(v)}
                            {unit && <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 0.25 }}>{unit}</Typography>}
                          </span>
                        );
                      })}
                  </Stack>
                </TableCell>
                <TableCell align="right">
                  <PortList values={ports(plant.input, plant.inputs)} digits={3} />
                </TableCell>
                <TableCell align="right">
                  <PortList values={ports(plant.output, plant.outputs)} digits={2} />
                </TableCell>
                <TableCell align="right">
                  {/* By signal address: what a `sim_daq` last delivered off this plant, noise and all. */}
                  <PortList values={readings.map(([address, r]) => [readings.length === 1 ? "" : address, r?.value ?? null])} digits={2} />
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      </Box>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1.125 }}>
        Retune with <code>flyball sim plant &lt;name&gt; key=value</code> or <code>PUT /api/sim/plants/&lt;name&gt;</code>.
      </Typography>
    </Paper>
  );
}

/** One device's simulation-only commands (faults, disturbances) as forms, run through its own route. */
function DeviceFaults({ schema }: { schema: DeviceSchema }) {
  const commands = useCommands(schema.name);
  const tags = Object.keys(schema.commands).filter((t) => schema.commands[t]?.simulation);
  if (!tags.length) return null;
  return (
    <Box sx={{ mb: 2.25 }}>
      <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
        {schema.label ?? schema.name}{" "}
        <Typography component="span" variant="body2" color="text.secondary">
          {schema.label && `${schema.name} · `}
          {describeDevice(schema.type)}
        </Typography>
      </Typography>
      <Box className="fb-commands">
        {tags.map((tag) => (
          <CommandForm
            key={tag}
            tag={tag}
            command={schema.commands[tag]!}
            form={MuiForm}
            onRun={(args) => commands.run(tag, args).catch(() => undefined)}
            busy={commands.busy === tag}
            result={commands.results[tag]}
          />
        ))}
      </Box>
    </Box>
  );
}

/** Every device's simulation-only commands, grouped by device: what a real rig could never do. */
function Faults() {
  const schema = useRigSchema();
  if (!schema.data) return null;
  const devices = Object.values(schema.data.devices).filter((d) => Object.values(d.commands).some((c) => c.simulation));
  if (!devices.length) return null;
  return (
    <Paper variant="outlined" sx={{ p: 3, mb: "16px" }}>
      <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 1.5 }}>
        Faults and disturbances
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
        Things a real rig could never do: script a failure, kick a plant, shrink a heater. Kept here, off the devices' own pages.
      </Typography>
      {devices.map((d) => (
        <DeviceFaults key={d.name} schema={d} />
      ))}
    </Paper>
  );
}

/**
 * The simulation page: speed control from `/api/sim`, the rig file's plants,
 * every device's simulation-only commands, and the application's own device
 * (`/api/sim/device`, which `GET /api/devices` lists with `kind:
 * "simulation"`) when it has one. The app wires the route and the tab (hide
 * the tab when `useSimulation().attached` is false).
 */
export function Simulation({ devices }: { devices: DeviceOut[] }) {
  const sim = useSimulation();
  const own = devices.find((d) => d.kind === "simulation");
  if (sim.loading && !sim.simulation) return <LinearProgress />;
  if (!sim.attached) {
    return (
      <StateBlock
        state="empty"
        message={`This rig is not simulated: its time is the wall clock, and there is nothing here to change.${sim.error ? ` (${sim.error.message})` : ""}`}
      />
    );
  }
  const simulation = sim.simulation!;
  return (
    <>
      <Stack direction="row" spacing={1} alignItems="baseline" flexWrap="wrap" useFlexGap sx={{ mb: "16px" }}>
        <Typography variant="h1" component="h1">
          Simulation
        </Typography>
        {simulation.name && <Chip size="small" label={simulation.name} />}
        {simulation.path && (
          <Typography variant="body2" color="text.secondary" sx={{ overflowWrap: "anywhere", minWidth: 0 }}>
            from <code>{simulation.path}</code>
            {simulation.changed.length > 0 && ` · unsaved: ${simulation.changed.join(", ")}`}
          </Typography>
        )}
      </Stack>
      <SpeedControl sim={sim} />
      <Plants plants={simulation.plants} />
      <Faults />
      {sim.schema && own ? (
        <DevicePanel
          device={own}
          schema={sim.schema}
          view={sim.view}
          form={MuiForm}
          subtitle={<code>/api/sim/device</code>}
          openSettings
          onRun={(tag, args) => sim.run(tag, args).catch(() => undefined)}
          busy={sim.busy}
          results={sim.results}
        />
      ) : (
        <Typography variant="body2" color="text.secondary">
          This simulation has no device of its own: nothing beyond the clock{Object.keys(simulation.plants).length ? " and the plants" : ""} to set.
        </Typography>
      )}
    </>
  );
}
