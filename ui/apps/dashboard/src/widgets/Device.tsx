import { memo, useMemo } from "react";
import { Form as MuiForm } from "@rjsf/mui";
import { DevicePanel, Ref, useCommands, useDeviceRun, useDeviceSchema, useRig, type PanelSeverity } from "@flyball/react";
import { atLeast, describeDevice, type DeviceOut, type Severity } from "@flyball/client";
import { useBindings, useCanWrite } from "../dashboard/context.js";
import { useWidgetChrome } from "../dashboard/chrome.js";
import { Missing } from "./Missing.js";
import { deviceSchema, SELECTS } from "./schema.js";
import type { WidgetType, WidgetComponentProps } from "./types.js";

/** The worst device condition, as the frame's severity dot (DESIGN-SPEC §2); `undefined` (no dot) when none is at warn/alarm. */
function severityOf(conditions: ReadonlyArray<{ severity: Severity }>): PanelSeverity | undefined {
  return conditions.some((c) => atLeast(c.severity, "error")) ? "alarm" : conditions.some((c) => atLeast(c.severity, "warning")) ? "warn" : undefined;
}

/** The panel with its hooks: the schema fetched once, commands run through `useCommands`, the run and conditions live from the store. */
function Wired({ device, commands }: { device: DeviceOut; commands: string[] | undefined }) {
  const rig = useRig();
  const canWrite = useCanWrite();
  const schema = useDeviceSchema(device.name);
  const run = useDeviceRun(device.name); // from the store: this widget alone re-renders on its device
  const runner = useCommands(device.name);
  const title = useMemo(() => <Ref kind="device" name={device.name}>{device.label ?? device.name}</Ref>, [device.name, device.label]);
  useWidgetChrome({ title, subtitle: describeDevice(device.driver ?? device.class_name), severity: severityOf(run?.conditions ?? device.conditions) });
  if (schema.error) return <Missing what="device schema" name={schema.error.message} failed />;
  if (!schema.data) return null;
  return (
    <DevicePanel
      device={device}
      schema={schema.data}
      commands={commands}
      form={MuiForm}
      // One frame only: `WidgetFrame` draws the title row (chrome above); state fields only, no
      // description, and only the chosen commands inline (DESIGN-SPEC §3.5).
      bare
      compact
      onRun={(command, args) => runner.run(command, args).catch(() => undefined)}
      onRestart={() => rig.restartDevice(device.name)}
      busy={runner.busy}
      results={runner.results}
      // Commands and Restart greyed out, still shown, below operate or on a read-only dashboard.
      canOperate={canWrite}
    />
  );
}

const DeviceWidget = memo(function DeviceWidget({ config }: WidgetComponentProps) {
  const bindings = useBindings();
  const name = String(config.device ?? "");
  const device = bindings.devices.find((d) => d.name === name);
  if (!device) return <Missing what="device" name={name} />;
  const known = new Set(device.commands.filter((c) => !c.simulation).map((c) => c.name));
  const chosen = Array.isArray(config.commands) ? (config.commands as unknown[]).map(String).filter((t) => known.has(t)) : [];
  // None ticked: every command that is not simulation-only (those stay on the Simulation page).
  const commands = chosen.length ? chosen : [...known];
  return <Wired device={device} commands={commands} />;
});

export const device: WidgetType = {
  type: "device",
  label: "Device",
  description: "A device's state, conditions and run, with a form per command; pick which commands, or show them all.",
  category: "control",
  defaultSize: { w: 6, h: 4 },
  minSize: { w: 4, h: 3 },
  cost: "cheap",
  configSchema: (bindings, config) => {
    const chosen = bindings.devices.find((d) => d.name === String(config.device ?? ""));
    const tags = (chosen?.commands ?? []).filter((c) => !c.simulation).map((c) => ({ const: c.name, title: c.description ? `${c.name} — ${c.description.split(/\n\s*\n/)[0]}` : c.name }));
    return {
      type: "object",
      properties: {
        device: deviceSchema(bindings),
        commands: {
          type: "array",
          title: "Commands",
          description: chosen ? "None ticked: every command." : "Pick the device first.",
          items: { type: "string", ...(tags.length ? { oneOf: tags } : {}) },
          uniqueItems: true,
          default: [],
        },
      },
      required: ["device"],
    };
  },
  uiSchema: SELECTS,
  defaultConfig: (bindings) => ({ device: bindings.devices.find((d) => d.kind !== "simulation")?.name ?? "", commands: [] }),
  // Fallback title before `useWidgetChrome`'s richer one (a `Ref` link) lands, and while editing.
  labelFor: (config, bindings) => {
    const name = String(config.device ?? "");
    return name ? bindings.deviceLabel(name) : undefined;
  },
  Component: DeviceWidget,
};
