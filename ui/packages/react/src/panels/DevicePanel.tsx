import { useState, type ReactNode } from "react";
import { describeDevice, type DeviceOut, type DeviceSchema, type DeviceView, type JsonSchema } from "@flyball/client";
import { Ref } from "../links.js";
import { useDeviceRun } from "../store/hooks.js";
import { CommandForm, type CommandFormProps } from "./CommandForm.js";
import { ObjectView } from "./ObjectView.js";
import { StateView } from "./StateView.js";

export interface DevicePanelProps {
  /** The device as `GET /api/devices/{name}` gave it: state, conditions, commands, run. */
  device: DeviceOut;
  /** Its schema (`GET /api/devices/{name}/schema`): how the state is labelled, what each command takes. */
  schema: DeviceSchema;
  /** Config and settings values (the simulation device's `GET /api/sim/device`); the two tiers are shown only when given. */
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
  /** Rendered between the header and the state: a control the panel does not know about. */
  children?: ReactNode;
  /** Open the settings section at first render; default closed. */
  openSettings?: boolean;
  /**
   * Body only -- run, conditions, state and the chosen commands, no card,
   * header or description: for a caller that already draws its own frame
   * (a dashboard widget under `WidgetFrame`, the one-frame rule of
   * DESIGN-SPEC.md §10). Default false.
   */
  bare?: boolean;
  /**
   * Dashboard-widget rendering (DESIGN-SPEC.md §3.5): the state's first
   * `maxFields` fields only, no description, no config/settings tiers, and
   * commands inline with no section headings -- and only those `commands`
   * names (the widget default is none). Default false.
   */
  compact?: boolean;
  /** State fields to show at most when `compact`; default 4. */
  maxFields?: number;
}

/** `schema` kept to its first `max` properties (schema order), for a compact widget body that must not scroll. */
function firstFields(schema: JsonSchema, max: number): JsonSchema {
  const all = schema.properties;
  if (!all) return schema;
  const keys = Object.keys(all).slice(0, max);
  if (keys.length === Object.keys(all).length) return schema;
  const properties: Record<string, JsonSchema> = {};
  for (const k of keys) properties[k] = all[k]!;
  return { ...schema, properties };
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

/**
 * Any device below its signals: the run (period, last read, restart), the
 * conditions, the state as the schema labels it, config and settings when
 * a caller has them (the simulation device's), and one form per command.
 * The state and the run follow `/ws/devices`; everything else comes in
 * through props.
 */
export function DevicePanel({ device, schema, view, commands, onRun, busy, results, form, onRestart, title, subtitle, children, openSettings = false, bare = false, compact = false, maxFields = 4 }: DevicePanelProps) {
  // Simulation-only commands (faults, disturbances) belong on the simulation page, not beside the real ones.
  // A dashboard widget's default is none at all (DESIGN-SPEC.md §3.5); a page's default is every command.
  const tags = commands ?? (compact ? [] : Object.keys(schema.commands).filter((t) => !schema.commands[t]?.simulation));
  const stateSchema = compact ? firstFields(schema.state, maxFields) : schema.state;
  const live = useDeviceRun(device.name);
  const run = live ?? device.run;
  const state = live?.state ?? device.state;
  // The runtime's conditions on polling it come with the run; the device's own are in its state (StateView shows those).
  const conditions = live?.conditions ?? device.conditions.filter((c) => !device.state.conditions?.some((own) => own.kind === c.kind));
  const [restarting, setRestarting] = useState(false);
  const restart = async () => {
    setRestarting(true);
    try {
      await onRestart?.();
    } finally {
      setRestarting(false);
    }
  };
  const runLine = (run || conditions.length > 0) && (
    <div className="fb-device-run">
      {run && (
        <span className="fb-muted" title={run.period_s !== null ? `Polled every ${run.period_s} s` : "Not polled on a period"}>
          {run.running ? "polling" : "stopped"}
          {run.period_s !== null && ` · every ${run.period_s} s`}
          {run.last_read_ns !== null && ` · last read ${clock(run.last_read_ns)}`}
        </span>
      )}
      {conditions.length > 0 && (
        <span className="fb-conditions">
          {conditions.map((c) => (
            <span key={c.kind} className={`fb-condition fb-level-${c.level}`} title={c.message}>
              {c.kind}
            </span>
          ))}
        </span>
      )}
      {onRestart && run && !run.running && (
        <button type="button" className="fb-tb" disabled={restarting} onClick={() => void restart()} title="Poll the device again on its period">
          Restart
        </button>
      )}
    </div>
  );
  const stateView = <StateView schema={stateSchema} state={state} />;
  const commandForms = tags.length > 0 && (
    <div className="fb-commands">
      {tags.map((tag) => {
        const command = schema.commands[tag];
        if (!command) return null;
        return (
          <CommandForm
            key={tag}
            tag={tag}
            command={command}
            onRun={(args) => onRun(tag, args)}
            busy={busy === tag}
            result={results?.[tag]}
            form={form}
          />
        );
      })}
    </div>
  );
  const body = compact ? (
    <>
      {runLine}
      {stateView}
      {commandForms}
    </>
  ) : (
    <>
      {runLine}
      <Section title="state" open>
        {stateView}
      </Section>
      {view && (
        <Section title="config">
          <ObjectView schema={schema.config} value={view.config} live={view} />
        </Section>
      )}
      {view && (
        <Section title="settings" open={openSettings}>
          <ObjectView schema={schema.settings} value={view.settings} />
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
          {describeDevice(device.driver ?? device.type)}
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
