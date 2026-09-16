/**
 * The dashboard a rig gets for nothing: made from its schema, so a fresh
 * install is not blank. One health strip, a readout per channel, a chart per
 * unit, a faceplate per loop, a card per actuator, then the programmer and
 * the events. Never saved unless someone saves it.
 */
import type { DashboardDocument, DashboardWidget } from "@flyball/client";
import { groupByUnit } from "@flyball/react";
import type { Bindings } from "./context.js";
import { DEFAULT_GRID, SCHEMA_VERSION } from "./document.js";

export const GENERATED_NAME = "Overview (generated)";

/** How many of `n` tiles to put abreast, from `choices` (each dividing the columns): the fewest left over on the last row, larger tiles on a tie. */
const abreast = (n: number, choices: number[]) => {
  if (n === 0) return choices[0]!;
  const leftover = (c: number) => (n % c === 0 ? 0 : c - (n % c));
  let best = choices[0]!;
  for (const c of choices) if (leftover(c) < leftover(best)) best = c;
  return Math.min(best, n);
};

export function generateOverview(bindings: Bindings, rig: string): DashboardDocument {
  const cols = DEFAULT_GRID.cols;
  const widgets: DashboardWidget[] = [];
  let y = 0;
  /** Lay `items` out `perRow` abreast, each `w` × `h`, rows growing down. */
  const row = (items: Array<Omit<DashboardWidget, "x" | "y" | "w" | "h">>, w: number, h: number) => {
    const perRow = Math.max(1, Math.floor(cols / w));
    items.forEach((item, i) => widgets.push({ ...item, x: (i % perRow) * w, y: y + Math.floor(i / perRow) * h, w, h }));
    if (items.length) y += Math.ceil(items.length / perRow) * h;
  };

  row([{ id: "health", kind: "health", title: null, config: { tiles: ["rig", "recording", "readers", "loops", "conditions"] } }], cols, 2);

  const channels = bindings.channels;
  row(
    channels.map((c) => ({ id: `readout-${c.source}-${c.measurand}`, kind: "readout", title: null, config: { channel: `${c.source}.${c.measurand}`, sparkline: true, showSource: true } })),
    channels.length >= 4 ? 3 : Math.max(3, Math.floor(cols / Math.max(1, channels.length))),
    4,
  );

  const units = groupByUnit(channels);
  row(
    units.map(({ unit, channels: cs }) => ({ id: `chart-${unit.replace(/[^a-z0-9]+/gi, "")}`, kind: "chart", title: null, config: { channels: cs.map((c) => `${c.source}.${c.measurand}`), window_s: 0, every: 0, y: "page" } })),
    units.length >= 2 ? 6 : cols,
    7,
  );

  row(
    bindings.loops.map((l) => ({ id: `loop-${l.name}`, kind: "loop", title: null, config: { loop: l.name, view: "compact" } })),
    cols / abreast(bindings.loops.length, [2, 3, 4]),
    8,
  );

  row(
    bindings.actuators.map((a) => ({ id: `actuator-${a.name}`, kind: "actuator", title: null, config: { actuator: a.name, commands: [], showConfig: false } })),
    cols / abreast(bindings.actuators.length, [3, 4, 2, 6]),
    7,
  );

  row(
    [
      { id: "program", kind: "program", title: null, config: { events: 5, interrupt: true } },
      { id: "events", kind: "events", title: null, config: { level: "INFO", limit: 20, scope: "" } },
    ],
    6,
    5,
  );

  return { schema_version: SCHEMA_VERSION, name: GENERATED_NAME, rig, description: "Made from the rig's schema: everything it has, in the order it declares it.", grid: { ...DEFAULT_GRID }, widgets };
}
