import { useState, type ReactNode } from "react";
import type { DeviceSchema, DeviceState, DeviceView } from "@flyball/client";
import { CommandForm, type CommandFormProps } from "./CommandForm.js";
import { ObjectView } from "./ObjectView.js";
import { StateView } from "./StateView.js";

export interface DevicePanelProps {
  schema: DeviceSchema;
  /** Live state; `undefined` until the first message. */
  state: DeviceState | undefined;
  /** Config and settings, fetched once; shown in collapsible sections when given. */
  view?: DeviceView;
  /** Which commands to show; default all, in schema order. */
  commands?: string[];
  onRun(command: string, args: Record<string, unknown>): Promise<unknown>;
  busy?: string | null;
  results?: Record<string, CommandFormProps["result"]>;
  /** The RJSF form to render commands with (a theme's `Form`); default `@rjsf/core`. */
  form?: CommandFormProps["form"];
  /** The heading; default the device's name. */
  title?: ReactNode;
  /** After the type in the header: a unit, a route, whatever tells the device apart. */
  subtitle?: ReactNode;
  /** Rendered between the header and the state: a control the panel does not know about. */
  children?: ReactNode;
  /** Open the settings section at first render; default closed. */
  openSettings?: boolean;
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

/**
 * Any device: state, config and settings (on demand), and one form per
 * command. `ActuatorPanel` is this with an actuator's header; this one takes
 * a bare `DeviceSchema`, for a device that is neither an actuator nor a
 * reader -- a simulation's knobs, say. Pure: everything comes in through
 * props.
 */
export function DevicePanel({ schema, state, view, commands, onRun, busy, results, form, title, subtitle, children, openSettings = false }: DevicePanelProps) {
  // Simulation-only commands (faults, disturbances) belong on the simulation page, not beside the real ones.
  const tags = commands ?? Object.keys(schema.commands).filter((t) => !schema.commands[t]?.simulation);
  return (
    <article className="fb-panel fb-device">
      <header>
        <h3>{title ?? schema.name}</h3>
        <span className="fb-muted">
          {schema.type}
          {subtitle && <> · {subtitle}</>}
        </span>
        {schema.description && <p>{schema.description}</p>}
      </header>
      {children}
      <Section title="state" open>
        <StateView schema={schema.state} state={state} />
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
      <Section title="commands" open>
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
      </Section>
    </article>
  );
}
