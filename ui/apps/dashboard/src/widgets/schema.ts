/** JSON Schema pieces the config forms share: pickers bound to what the rig has, with display names as titles. */
import type { JsonSchema } from "@flyball/client";
import type { Bindings } from "../dashboard/context.js";

export const channelKeyOf = (c: { source: string; measurand: string }) => `${c.source}.${c.measurand}`;

/** One of the rig's channels, by `source.measurand`, titled `Source label · measurand (unit)`. */
export function channelSchema(bindings: Bindings, title = "Channel"): JsonSchema {
  const options = bindings.channels.map((c) => ({ const: channelKeyOf(c), title: bindings.channelLabel(channelKeyOf(c)) }));
  return { type: "string", title, ...(options.length ? { oneOf: options } : {}) };
}

/** Several channels, each once. */
export function channelsSchema(bindings: Bindings, title = "Channels"): JsonSchema {
  return { type: "array", title, items: channelSchema(bindings, ""), uniqueItems: true, default: [] };
}

export function loopSchema(bindings: Bindings): JsonSchema {
  const options = bindings.loops.map((l) => ({ const: l.name, title: l.label ? `${l.label} (${l.name})` : l.name }));
  return { type: "string", title: "Controller", ...(options.length ? { oneOf: options } : {}) };
}

export function actuatorSchema(bindings: Bindings): JsonSchema {
  const options = bindings.actuators.map((a) => ({ const: a.name, title: a.label ? `${a.label} (${a.name})` : a.name }));
  return { type: "string", title: "Actuator", ...(options.length ? { oneOf: options } : {}) };
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
export const SELECTS = { channel: { "ui:widget": "select" }, loop: { "ui:widget": "select" }, actuator: { "ui:widget": "select" } };
