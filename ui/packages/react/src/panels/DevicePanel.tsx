import { useState, type ReactNode } from "react";
import { describeDevice, describeSignal, isNamespace, type DeviceOut, type DeviceSchema, type DeviceView, type SignalOut } from "@flyball/client";
import { Ref } from "../links.js";
import { useDeviceRun, useLatestValue } from "../store/hooks.js";
import { CommandForm, type CommandFormProps } from "./CommandForm.js";
import { ObjectView } from "./ObjectView.js";

export interface DevicePanelProps {
  /** The device as `GET /api/devices/{name}` gave it: its tree, conditions, commands, run. */
  device: DeviceOut;
  /** Its schema (`GET /api/devices/{name}/schema`): config, signals, inputs, each command's request as JSON Schema. */
  schema: DeviceSchema;
  /** Config values (the simulation device's `GET /api/sim/device`); the config section is shown only when given. */
  view?: DeviceView;
  /** Which commands to show; default all but the simulation-only ones, in schema order. */
  commands?: string[];
  onRun(command: string, args: Record<string, unknown>): Promise<unknown>;
  busy?: string | null;
  results?: Record<string, CommandFormProps["result"]>;
  /** The RJSF form to render commands with (a theme's `Form`); default `@rjsf/core`. */
  form?: CommandFormProps["form"];
  /** Poll an offline device again (`POST /api/devices/{name}/restart`); the button shows only when given and the run has stopped. */
  onRestart?(): Promise<unknown>;
  /** The heading; default the device's label, else its name. */
  title?: ReactNode;
  /** After the driver in the header: a link's name, whatever tells the device apart. */
  subtitle?: ReactNode;
  /** Rendered between the header and the body: a control the panel does not know about. */
  children?: ReactNode;
  /** Open the config section at first render; default closed. */
  openSettings?: boolean;
  /**
   * Body only -- run, mode, conditions and the chosen commands, no card,
   * header or description: for a caller that already draws its own frame
   * (a dashboard widget under `WidgetFrame`, the one-frame rule of
   * DESIGN-SPEC.md §10). Default false.
   */
  bare?: boolean;
  /**
   * Dashboard-widget rendering (DESIGN-SPEC.md §3.5): no config tier, and
   * commands inline with no section headings -- and only those `commands`
   * names (the widget default is none). Default false.
   */
  compact?: boolean;
  /**
   * This browser may run commands and restart the device; default true. False disables each
   * command's form and button and the Restart button -- greyed out, still shown, the rest of the
   * panel (run, mode, conditions) untouched.
   */
  canOperate?: boolean;
  /** Unused now that there is no schema-described `state` to truncate; kept so existing callers still compile. */
  maxFields?: number;
}

function Section({ title, open: initial = false, children }: { title: string; open?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(initial);
  return (
    <details className="fb-section" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>{title}</summary>
      {children}
    </details>
  );
}

/** Time of day of a rig timestamp. */
const clock = (ns: number) => new Date(ns / 1e6).toLocaleTimeString();

/** The device's `mode` output, if its driver declared one: a top-level signal named `mode`. */
function modeSignalOf(device: DeviceOut): SignalOut | undefined {
  return device.signals.find((n): n is SignalOut => !isNamespace(n) && n.name === "mode");
}

/**
 * Any device below its signal tree: the run (period, last read, restart),
 * its `mode` as a prominent chip, its conditions, config when a caller has
 * it (the simulation device's), and one form per command -- the form
 * highlighted when its `mode` is the device's current one. The run and the
 * live values follow `/ws/devices` and the store; everything else comes in
 * through props.
 */
export function DevicePanel({ device, schema, view, commands, onRun, busy, results, form, onRestart, title, subtitle, children, openSettings = false, bare = false, compact = false, canOperate = true, maxFields: _maxFields = 4 }: DevicePanelProps) {
  // Simulation-only commands (faults, disturbances) belong on the simulation page, not beside the real ones.
  // A dashboard widget's default is none at all (DESIGN-SPEC.md §3.5); a page's default is every command.
  const names = commands ?? (compact ? [] : Object.keys(schema.commands).filter((t) => !schema.commands[t]?.simulation));
  const live = useDeviceRun(device.name);
  const run = live ?? device.run;
  // The live run carries everything the rig holds on the device (the runtime's and its driver's);
  // an unpolled device has no run, so what `GET /api/devices` said stands.
  const conditions = live?.conditions ?? device.conditions;
  const modeSignal = modeSignalOf(device);
  const liveMode = useLatestValue(modeSignal?.address);
  // A live reading with no value is no mode now, not the mode before it.
  const currentMode = liveMode ? liveMode.value : (modeSignal?.latest?.value ?? modeSignal?.initial);
  const [restarting, setRestarting] = useState(false);
  const restart = async () => {
    setRestarting(true);
    try {
      await onRestart?.();
    } finally {
      setRestarting(false);
    }
  };
  // Offline and backing off: the rig still polls, but between retries (`next_retry_ns` set only then).
  const retrying = run?.next_retry_ns != null;
  const runLine = (run || conditions.length > 0 || modeSignal) && (
    <div className="fb-device-run">
      {run && (
        <span className="fb-muted" title={run.period_s !== null ? `Polled every ${run.period_s} s` : "Not polled on a period"}>
          {!run.running ? "stopped" : retrying ? `retrying · ${run.consecutive_failures} failed` : "polling"}
          {run.period_s !== null && ` · every ${run.period_s} s`}
          {run.last_read_ns !== null && ` · last read ${clock(run.last_read_ns)}`}
          {retrying && ` · next try ${clock(run.next_retry_ns!)}`}
        </span>
      )}
      {modeSignal && currentMode !== null && currentMode !== undefined && (
        <span className="fb-badge fb-mode" title={`${describeSignal(modeSignal)} · ${modeSignal.address}`}>
          {String(currentMode)}
        </span>
      )}
      {conditions.length > 0 && (
        <span className="fb-conditions">
          {conditions.map((c) => (
            <span key={c.code} className={`fb-condition fb-severity-${c.severity}`} title={c.message}>
              {c.code}
            </span>
          ))}
        </span>
      )}
      {onRestart && run && (!run.running || retrying) && (
        <button type="button" className="fb-tb" disabled={restarting || !canOperate} onClick={() => void restart()} title={retrying ? "Try a read now instead of waiting for the next retry" : "Poll the device again on its period"} data-testid="device-restart">
          {retrying ? "Retry now" : "Restart"}
        </button>
      )}
    </div>
  );
  const commandForms = names.length > 0 && (
    <div className="fb-commands">
      {names.map((name) => {
        const command = schema.commands[name];
        if (!command) return null;
        return (
          <CommandForm
            key={name}
            name={name}
            command={command}
            device={device.name}
            currentMode={currentMode}
            signals={schema.signals}
            onRun={(args) => onRun(name, args)}
            busy={busy === name}
            canOperate={canOperate}
            result={results?.[name]}
            form={form}
          />
        );
      })}
    </div>
  );
  const body = compact ? (
    <>
      {runLine}
      {commandForms}
    </>
  ) : (
    <>
      {runLine}
      {view && (
        <Section title="config" open={openSettings}>
          <ObjectView schema={schema.config} value={view.config} live={view} />
        </Section>
      )}
      {commandForms && (
        <Section title="commands" open>
          {commandForms}
        </Section>
      )}
    </>
  );
  if (bare) {
    return (
      <div className="fb-device fb-device-bare">
        {children}
        {body}
      </div>
    );
  }
  return (
    <article className="fb-panel fb-device">
      <header>
        <h3>{title ?? <Ref kind="device" name={device.name}>{device.label ?? device.name}</Ref>}</h3>
        <span className="fb-muted">
          {title === undefined && device.label && `${device.name} · `}
          {describeDevice(device.driver ?? device.class_name)}
          {device.link && ` · on ${device.link}`}
          {subtitle && <> · {subtitle}</>}
        </span>
        {schema.description && !compact && <p>{schema.description}</p>}
      </header>
      {children}
      {body}
    </article>
  );
}
