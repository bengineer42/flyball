import { memo, useMemo } from "react";
import { Form as MuiForm } from "@rjsf/mui";
import { ActuatorPanel, Ref, useActuatorState, useCommands, useDeviceView, type PanelSeverity } from "@flyball/react";
import type { ActuatorSchema, DeviceState } from "@flyball/client";
import { describeDevice } from "@flyball/client";
import { useBindings, useLoopsData } from "../dashboard/context.js";
import { useWidgetChrome } from "../dashboard/chrome.js";
import { Missing } from "./Missing.js";
import { actuatorSchema, SELECTS } from "./schema.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

/** The worst device condition, as the frame's severity dot (DESIGN-SPEC §2); `undefined` (no dot) when none is at warn/alarm. */
function severityOf(state: DeviceState | undefined): PanelSeverity | undefined {
  const level = Math.max(0, ...(state?.conditions ?? []).map((c) => c.level));
  return level >= 40 ? "alarm" : level >= 30 ? "warn" : undefined;
}

/** The panel with its hooks: commands run through `useCommands`; `view` (config and settings) is fetched by the caller only when wanted. */
function Wired({ schema, commands, view }: { schema: ActuatorSchema; commands: string[] | undefined; view?: ReturnType<typeof useDeviceView> }) {
  const state = useActuatorState(schema.name); // from the store: this widget alone re-renders on its actuator
  const runner = useCommands("actuators", schema.name);
  // A loop is named for the actuator it drives; borrow its channel's precision so the demand
  // and output range read the same number of decimals here as on the Controllers page's faceplate.
  const precision = useLoopsData().loops[schema.name]?.channel.precision ?? undefined;
  const title = useMemo(() => <Ref kind="actuator" name={schema.name}>{schema.label ?? schema.name}</Ref>, [schema.name, schema.label]);
  useWidgetChrome({ title, subtitle: describeDevice(schema.type), severity: severityOf(state) });
  return (
    <ActuatorPanel
      schema={schema}
      state={state}
      view={view?.data}
      commands={commands}
      precision={precision}
      form={MuiForm}
      // One frame only: `WidgetFrame` draws the title row (chrome above); state fields only, no
      // description, and commands only when explicitly chosen (DESIGN-SPEC §3.5).
      compact
      bare
      onRun={(tag, args) =>
        runner
          .run(tag, args)
          .then((r) => {
            view?.refresh(); // settings may have changed
            return r;
          })
          .catch(() => undefined)
      }
      busy={runner.busy}
      results={runner.results}
    />
  );
}

/** The same with config and settings fetched once (and again after a command). */
function WiredWithView({ schema, commands }: { schema: ActuatorSchema; commands: string[] | undefined }) {
  const view = useDeviceView("actuators", schema.name);
  return <Wired schema={schema} commands={commands} view={view} />;
}

const ActuatorWidget = memo(function ActuatorWidget({ config }: WidgetComponentProps) {
  const bindings = useBindings();
  const name = String(config.actuator ?? "");
  const schema = bindings.schema.actuators[name];
  if (!schema) return <Missing what="actuator" name={name} />;
  const chosen = Array.isArray(config.commands) ? (config.commands as unknown[]).map(String).filter((t) => t in schema.commands) : [];
  const commands = chosen.length ? chosen : undefined;
  return config.showConfig === true ? <WiredWithView schema={schema} commands={commands} /> : <Wired schema={schema} commands={commands} />;
});

export const actuator: WidgetKind = {
  kind: "actuator",
  label: "Actuator",
  description: "An actuator's live state and a form per command; pick which commands, or show them all.",
  category: "control",
  defaultSize: { w: 6, h: 5 },
  minSize: { w: 4, h: 3 },
  cost: "cheap",
  configSchema: (bindings, config) => {
    const chosen = bindings.schema.actuators[String(config.actuator ?? "")];
    const tags = Object.entries(chosen?.commands ?? {})
      .filter(([, c]) => !c.simulation)
      .map(([tag, c]) => ({ const: tag, title: c.description ? `${tag} — ${c.description}` : tag }));
    return {
      type: "object",
      properties: {
        actuator: actuatorSchema(bindings),
        commands: {
          type: "array",
          title: "Commands",
          description: chosen ? "None ticked: every command." : "Pick the actuator first.",
          items: { type: "string", ...(tags.length ? { oneOf: tags } : {}) },
          uniqueItems: true,
          default: [],
        },
        showConfig: { type: "boolean", title: "Config and settings", default: false, description: "Fetch and show the actuator's config and settings sections." },
      },
      required: ["actuator"],
    };
  },
  uiSchema: SELECTS,
  defaultConfig: (bindings) => ({ actuator: bindings.actuators[0]?.name ?? "", commands: [], showConfig: false }),
  // Fallback title before `useWidgetChrome`'s richer one (a `Ref` link) lands, and while editing.
  titleFor: (config, bindings) => {
    const name = String(config.actuator ?? "");
    return name ? bindings.schema.actuators[name]?.label || name : undefined;
  },
  Component: ActuatorWidget,
};
