import { memo } from "react";
import { Form as MuiForm } from "@rjsf/mui";
import { ActuatorPanel, useCommands, useDeviceView } from "@flyball/react";
import type { ActuatorSchema } from "@flyball/client";
import { useBindings, useStates } from "../dashboard/context.js";
import { Missing } from "./Missing.js";
import { actuatorSchema, SELECTS } from "./schema.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

/** The panel with its hooks: commands run through `useCommands`; `view` (config and settings) is fetched by the caller only when wanted. */
function Wired({ schema, commands, view }: { schema: ActuatorSchema; commands: string[] | undefined; view?: ReturnType<typeof useDeviceView> }) {
  const states = useStates();
  const runner = useCommands("actuators", schema.name);
  return (
    <ActuatorPanel
      schema={schema}
      state={states[schema.name]}
      view={view?.data}
      commands={commands}
      form={MuiForm}
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
  defaultSize: { w: 4, h: 7 },
  minSize: { w: 3, h: 4 },
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
  header: false,
  Component: ActuatorWidget,
};
