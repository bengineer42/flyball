import { useState } from "react";
import type { ActuatorSchema, DeviceState, DeviceView } from "@flyball/client";
import { CommandForm, type CommandFormProps } from "./CommandForm.js";
import { ObjectView } from "./ObjectView.js";
import { StateView } from "./StateView.js";
import { Ref } from "../links.js";

export interface ActuatorPanelProps {
  schema: ActuatorSchema;
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
}

function Section({ title, open: initial = false, children }: { title: string; open?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(initial);
  return (
    <details className="fb-section" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>{title}</summary>
      {children}
    </details>
  );
}

/**
 * One actuator: state (live), config and settings (on demand), and one form
 * per command. Pure: everything comes in through props, so it renders
 * against a mock as readily as a rig.
 */
export function ActuatorPanel({ schema, state, view, commands, onRun, busy, results, form }: ActuatorPanelProps) {
  // Simulation-only commands (faults, disturbances) belong on the simulation page, not beside the real ones.
  const tags = commands ?? Object.keys(schema.commands).filter((t) => !schema.commands[t]?.simulation);
  return (
    <article className="fb-panel fb-actuator">
      <header>
        <h3><Ref kind="actuator" name={schema.name}>{schema.label ?? schema.name}</Ref></h3>
        <span className="fb-muted">
          {schema.label && `${schema.name} · `}
          {schema.type}
          {schema.demand_unit && ` · demand in ${schema.demand_unit}`}
        </span>
        {schema.description && <p>{schema.description}</p>}
      </header>
      <Section title="state" open>
        <StateView schema={schema.state} state={state} />
      </Section>
      {view && (
        <Section title="config">
          <ObjectView schema={schema.config} value={view.config} live={view} />
        </Section>
      )}
      {view && (
        <Section title="settings">
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
