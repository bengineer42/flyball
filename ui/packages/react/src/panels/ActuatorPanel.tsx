import { useMemo, type ReactNode } from "react";
import type { ActuatorSchema, DeviceState, DeviceView, JsonSchema } from "@flyball/client";
import { describeDevice, formatNumber } from "@flyball/client";
import { CommandForm, type CommandFormProps } from "./CommandForm.js";
import { ObjectView } from "./ObjectView.js";
import { PanelFrame } from "./PanelFrame.js";
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
  /**
   * Decimal places for `demand`/`output_range`, from the driving loop's
   * channel (`LoopPanel` shows the same demand at `channel.precision`) --
   * so a number does not read to a different precision on the two pages.
   * Defaults to the schema's own precision (usually 2) with no loop driving it.
   */
  precision?: number;
  /**
   * Dashboard-widget rendering (DESIGN-SPEC §3.5): no description paragraph,
   * state fields capped to `maxFields` (default 4), and commands only when
   * `commands` names some -- the widget default is none. Config/settings
   * stay gated on `view` alone (only ever fetched when a caller wants them
   * shown, e.g. the widget's own "show config" toggle), not on `compact`.
   * Default false: the full view a card on the Controllers page shows.
   */
  compact?: boolean;
  /** State fields to show at most when `compact`; default 4 (DESIGN-SPEC §3.5). */
  maxFields?: number;
  /**
   * Skip the outer card and header: for a caller that already draws its own
   * frame around this panel (`WidgetFrame`'s tile, or a loop faceplate's
   * card on the merged Controllers page, where this becomes the collapsible
   * section below the faceplate). Default false.
   */
  bare?: boolean;
  /** State fields to leave out beyond `conditions` -- e.g. `demand`/`output_range` when a loop's OP row above already shows them. */
  omitFields?: string[];
}

/**
 * `state`, with `output_range`'s unit filled in from `demandUnit` (the
 * backend stamps a unit onto `demand` since it is declared on the generic
 * `Actuator` base, but `output_range` is a bare tuple with no way to name
 * its unit generically) and both fields' precision set to `precision`.
 */
function withDemandContext(state: JsonSchema, demandUnit: string | null, precision: number | undefined): JsonSchema {
  const properties = state.properties;
  if (!properties || (!demandUnit && precision === undefined)) return state;
  const stampBranch = (b: JsonSchema): JsonSchema =>
    b.type === "number" || b.type === "array"
      ? { ...b, ...(demandUnit && b.type === "array" ? { unit: demandUnit } : {}), ...(precision !== undefined ? { precision } : {}) }
      : b;
  const stampField = (field: JsonSchema): JsonSchema => (field.anyOf ? { ...field, anyOf: field.anyOf.map(stampBranch) } : stampBranch(field));
  const patched = { ...properties };
  if (patched.output_range) patched.output_range = stampField(patched.output_range);
  if (patched.demand) patched.demand = stampField(patched.demand);
  return { ...state, properties: patched };
}

/** `schema` with `names` dropped from its top-level `properties` -- for fields a caller already shows elsewhere. */
function withoutFields(schema: JsonSchema, names: string[] | undefined): JsonSchema {
  if (!names?.length || !schema.properties) return schema;
  const properties = { ...schema.properties };
  for (const name of names) delete properties[name];
  return { ...schema, properties };
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

/** A value clamped to `range`, whichever way round its ends are given. */
function clampToRange(value: number, range: [number, number]): number {
  const lo = Math.min(range[0], range[1]);
  const hi = Math.max(range[0], range[1]);
  return Math.min(hi, Math.max(lo, value));
}

/** True when two numbers differ by more than float noise (mirrors `LoopPanel`'s `differs`). */
function differs(a: number, b: number): boolean {
  return Math.abs(a - b) > 1e-9 * Math.max(1, Math.abs(a));
}

/**
 * A section of the card, always open: state, config, settings and commands
 * used to sit behind a `<details>` disclosure each -- a user report found
 * that hid content people needed every time, for no saved space worth it.
 * Static text now, not a `<summary>`: no triangle, no click target.
 */
function SectionStatic({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="fb-section fb-section-static">
      <h4 className="fb-section-title">{title}</h4>
      {children}
    </section>
  );
}

/**
 * One actuator: state (live), config and settings (on demand), and one form
 * per command. Pure: everything comes in through props, so it renders
 * against a mock as readily as a rig.
 */
export function ActuatorPanel({ schema, state, view, commands, onRun, busy, results, form, precision, compact = false, maxFields = 4, bare = false, omitFields }: ActuatorPanelProps) {
  // Simulation-only commands (faults, disturbances) belong on the simulation page, not beside the real ones.
  // A dashboard widget's default is none at all (DESIGN-SPEC §3.5); the Controllers page's default is every command.
  const tags = commands ?? (compact ? [] : Object.keys(schema.commands).filter((t) => !schema.commands[t]?.simulation));
  // Settings shown only when a command actually rendered here can change one -- otherwise it is
  // read-only rig-file config by another name, and config itself (label/name/link/port/limits/...)
  // is dropped altogether: it duplicates the header (label, name) or belongs to the rig file, not
  // the operator (a user report on this exact card).
  const settingsFields = new Set(Object.keys(schema.settings.properties ?? {}));
  const settingsChangeable = tags.some((t) => {
    const args = schema.commands[t]?.arguments.properties;
    return args && Object.keys(args).some((k) => settingsFields.has(k));
  });
  const stateSchema = useMemo(() => {
    const s = withoutFields(withDemandContext(schema.state, schema.demand_unit, precision), omitFields);
    return compact ? firstFields(s, maxFields) : s;
  }, [schema.state, schema.demand_unit, precision, omitFields, compact, maxFields]);
  // Mirror the loop faceplate's OP row: the primary number is what the actuator can actually
  // give (the demand clamped to its achievable range), not the raw, possibly wound-up, demand
  // a law with integral windup can hand it (`state.demand` is never clamped on the wire -- there
  // is no "expected" field on an actuator's own state, unlike a loop's).
  const demandRaw = typeof state?.demand === "number" ? state.demand : null;
  const outputRange = state?.output_range ?? null;
  const clampedDemand = demandRaw != null && outputRange ? clampToRange(demandRaw, outputRange) : demandRaw;
  const atLimit = demandRaw != null && clampedDemand != null && differs(demandRaw, clampedDemand);
  // Which rail the output is pinned to, for the highlighted end of the bar below.
  const limitEdge: "hi" | "lo" | null = atLimit ? (demandRaw! > clampedDemand! ? "hi" : "lo") : null;
  const displayState = atLimit && state ? { ...state, demand: clampedDemand } : state;
  const requested = atLimit ? `${formatNumber(demandRaw!, { precision })}${schema.demand_unit ? ` ${schema.demand_unit}` : ""}` : null;
  const outputFraction = clampedDemand != null && outputRange ? Math.min(1, Math.max(0, (clampedDemand - outputRange[0]) / (outputRange[1] - outputRange[0]))) : null;
  const stateView = (
    <StateView
      schema={stateSchema}
      state={displayState}
      extra={
        atLimit
          ? {
              demand: (
                <>
                  {outputFraction !== null && (
                    <div
                      className={`fb-range fb-range-limit-${limitEdge}`}
                      title={`output at its ${limitEdge === "hi" ? "upper" : "lower"} limit; requested ${requested}`}
                    >
                      <div className="fb-range-fill" style={{ width: `${outputFraction * 100}%` }} />
                    </div>
                  )}
                  <div className="fb-muted">requested {requested}</div>
                </>
              ),
            }
          : undefined
      }
    />
  );
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
  // `view` (settings) is only ever fetched when a caller wants it shown -- the dashboard widget's
  // "show config" toggle -- so gating this on `compact` too would fight that opt-in.
  const showSettings = view && settingsChangeable;
  const body = (
    <>
      {compact ? stateView : <SectionStatic title="state">{stateView}</SectionStatic>}
      {showSettings && (compact ? <ObjectView schema={schema.settings} value={view.settings} /> : <SectionStatic title="settings"><ObjectView schema={schema.settings} value={view.settings} /></SectionStatic>)}
      {commandForms && (compact ? commandForms : <SectionStatic title="commands">{commandForms}</SectionStatic>)}
    </>
  );
  if (bare) return <div className="fb-actuator fb-actuator-bare">{body}</div>;
  return (
    <PanelFrame
      className="fb-actuator"
      title={
        <span title={schema.description || undefined}>
          <Ref kind="actuator" name={schema.name}>{schema.label ?? schema.name}</Ref>
        </span>
      }
      subtitle={
        <>
          {schema.label && `${schema.name} · `}
          {describeDevice(schema.type)}
          {schema.demand_unit && ` · demand in ${schema.demand_unit}`}
        </>
      }
    >
      {body}
    </PanelFrame>
  );
}
