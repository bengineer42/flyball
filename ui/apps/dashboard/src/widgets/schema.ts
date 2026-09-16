/**
 * JSON Schema pieces the config forms share: pickers bound to what the rig has, with display names as
 * titles. Each carries `x-binding` naming what it picks, so a catalogue made with no rig
 * (`scripts/widgets-json.ts`) still says which fields bind to the rig.
 */
import type { JsonSchema, SignalOut } from "@flyball/client";
import type { Bindings } from "../dashboard/context.js";
import { isNumeric } from "../valueReadout.js";

/**
 * One of the rig's publishing signals, by address, titled `Device label ·
 * Signal label (unit)`. `numeric` narrows to float/int signals only: a
 * gauge or a chart axis never takes a non-number.
 */
export function signalSchema(bindings: Bindings, title = "Signal", numeric = false): JsonSchema {
  const options = bindings.signals.filter((s: SignalOut) => !numeric || isNumeric(s)).map((s) => ({ const: s.address, title: bindings.signalLabel(s.address) }));
  return { type: "string", title, "x-binding": "signal", ...(options.length ? { oneOf: options } : {}) };
}

/** Several signals, each once. */
export function signalsSchema(bindings: Bindings, title = "Signals", numeric = false): JsonSchema {
  return { type: "array", title, items: signalSchema(bindings, "", numeric), uniqueItems: true, default: [] };
}

/** One of the rig's controllers, by name (the address of the signal it drives). */
export function controllerSchema(bindings: Bindings): JsonSchema {
  const options = bindings.controllers.map((c) => ({ const: c.name, title: c.label ? `${c.label} (${c.name})` : c.name }));
  return { type: "string", title: "Controller", "x-binding": "controller", ...(options.length ? { oneOf: options } : {}) };
}

/** One of the rig's devices, by name. */
export function deviceSchema(bindings: Bindings): JsonSchema {
  const options = bindings.devices.filter((d) => d.kind !== "simulation").map((d) => ({ const: d.name, title: d.label ? `${d.label} (${d.name})` : d.name }));
  return { type: "string", title: "Device", "x-binding": "device", ...(options.length ? { oneOf: options } : {}) };
}

/** A choice with a "follow the page" entry first: the chart controls in the page bar apply unless a widget says otherwise. */
export const pageOr = (title: string, options: Array<{ const: number | string; title: string }>, description?: string): JsonSchema => ({
  type: typeof options[0]?.const === "number" ? "number" : "string",
  title,
  ...(description ? { description } : {}),
  default: options[0]?.const,
  oneOf: options,
});

export const WINDOW_OPTIONS = [
  { const: 0, title: "page setting" },
  { const: 60, title: "1 min" },
  { const: 300, title: "5 min" },
  { const: 900, title: "15 min" },
  { const: 3600, title: "1 h" },
];
export const EVERY_OPTIONS = [
  { const: 0, title: "page setting" },
  { const: 1, title: "every point" },
  { const: 2, title: "1 in 2" },
  { const: 5, title: "1 in 5" },
  { const: 10, title: "1 in 10" },
  { const: 20, title: "1 in 20" },
  { const: 50, title: "1 in 50" },
];
export const Y_OPTIONS = [
  { const: "page", title: "page setting" },
  { const: "auto", title: "fit the data" },
  { const: "range", title: "declared range" },
];

/** Selects for every picker: their titles are long, and a segmented row of four would not fit a dialog. */
export const SELECTS = { address: { "ui:widget": "select" }, controller: { "ui:widget": "select" }, device: { "ui:widget": "select" } };
