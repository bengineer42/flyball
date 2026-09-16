import { Fragment, useMemo } from "react";
import { Alert, Link, Paper, Typography } from "@mui/material";
import { Form as MuiForm } from "@rjsf/mui";
import { DevicePanel, DeviceSignals, useCommands, useControllers, useDeviceRuns, useDeviceSchema, useRig } from "@flyball/react";
import { describeController, deviceOf, humanise, type DeviceOut, type InputOut } from "@flyball/client";
import { DeviceSummaryCard, SectionHead, StateBlock } from "../cards.js";
import { PAGE_ICONS } from "../icons.js";
import { hashFor, hrefFor } from "../router.js";
import { useRecordingExports } from "../model.js";
import { Crumbs } from "./Inputs.js";
import { PageBar } from "../PageBar.js";
import type { ChartSettings } from "../YScaleSelect.js";

/** Every device the rig has, as cards: `#/devices` without a name. The simulation device (if any) stays on its own page. */
export function Devices({ devices }: { devices: DeviceOut[] }) {
  const runs = useDeviceRuns();
  const shown = devices.filter((d) => d.kind !== "simulation");
  return (
    <>
      <SectionHead icon={PAGE_ICONS.devices} title="Devices" count={shown.length} />
      {shown.length === 0 ? (
        <StateBlock state="empty" message="No devices declared. Add one to the rig file to see it here." />
      ) : (
        <div className="grid">
          {shown.map((d) => (
            <DeviceSummaryCard key={d.name} className="c3" device={d} run={runs[d.name]} />
          ))}
        </div>
      )}
    </>
  );
}

/** What the device follows, by role: "dry ← hum_sensors.dry.humidity", one per bound (or unbound) input. */
function InputsLine({ inputs }: { inputs: Record<string, InputOut> }) {
  const entries = Object.entries(inputs);
  if (entries.length === 0) return null;
  return (
    <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
      {entries.map(([role, input], i) => (
        <Fragment key={role}>
          {i > 0 && "  ·  "}
          {input.label || humanise(input.name)} ←{" "}
          {input.bound ? (
            <Link href={hrefFor({ kind: "signal", name: input.bound })} underline="hover" title={input.bound}>
              {input.bound}
            </Link>
          ) : (
            <span className="fb-muted">not bound</span>
          )}
        </Fragment>
      ))}
    </Typography>
  );
}

/**
 * One device (`#/devices/<name>`): the panel with its state, conditions,
 * run and commands (the simulation-only ones stay on the Simulation page),
 * then the signal tree with live values and write states, then the
 * controllers that drive or regulate its signals.
 */
export function DevicePage({ devices, name, windowS, every }: { devices: DeviceOut[]; name: string } & ChartSettings) {
  const rig = useRig();
  const device = devices.find((d) => d.name === name);
  const schema = useDeviceSchema(name);
  const commands = useCommands(name);
  const stored = useRecordingExports();
  const { controllers } = useControllers();
  const tags = useMemo(() => Object.entries(schema.data?.commands ?? {}).filter(([, c]) => !c.simulation).map(([tag]) => tag), [schema.data]);
  if (!device) return <Alert severity="warning">No device named {name}.</Alert>;
  const drives = Object.values(controllers).filter((c) => deviceOf(c.target) === name);
  const regulates = Object.values(controllers).filter((c) => deviceOf(c.source) === name && deviceOf(c.target) !== name);
  return (
    <>
      <PageBar>
        <Crumbs items={[{ label: "overview", href: hashFor("overview") }, { label: "devices", href: hashFor("devices") }, { label: device.label ?? name }]} />
      </PageBar>
      {schema.error && <Alert severity="error">{schema.error.message}</Alert>}
      <div className="grid">
        <div className="c12 xl6">
          {schema.data ? (
            <DevicePanel
              device={device}
              schema={schema.data}
              commands={tags}
              form={MuiForm}
              onRun={(tag, args) => commands.run(tag, args).catch(() => undefined)}
              onRestart={() => rig.restartDevice(name)}
              busy={commands.busy}
              results={commands.results}
            >
              <InputsLine inputs={device.inputs} />
            </DevicePanel>
          ) : (
            <Typography color="text.secondary">loading…</Typography>
          )}
        </div>
        <div className="c12 xl6">
          <DeviceSignals device={device} windowS={windowS} every={every} exportHref={(s) => stored.series(s.address)} />
        </div>
        <Paper className="c12" sx={{ p: 3 }}>
          <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 0.75 }}>
            Controllers
          </Typography>
          {drives.length + regulates.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              no controller drives or regulates a signal of this device
            </Typography>
          ) : (
            [...drives, ...regulates].map((c) => (
              <Typography key={c.name}>
                <Link href={hrefFor({ kind: "controller", name: c.name })} underline="hover">
                  {describeController(c)}
                </Link>{" "}
                <Typography component="span" variant="body2" color="text.secondary">
                  {c.label && `${c.name} · `}
                  {deviceOf(c.target) === name ? `drives ${c.target} from ${c.source}` : `regulates ${c.source} through ${c.target}`} · {c.mode}
                </Typography>
              </Typography>
            ))
          )}
        </Paper>
      </div>
    </>
  );
}
