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

  // Sizes below follow the widget catalogue's defaults (DESIGN-SPEC.md §3) on a 24-column grid:
  // health 24×2, readout 6×5, chart 12×8, loop 8×8, actuator 6×5, program 8×6, events 12×6.
  row([{ id: "health", kind: "health", title: null, config: { tiles: ["rig", "recording", "readers", "loops", "conditions"] } }], cols, 2);

  const channels = bindings.channels;
  row(
    channels.map((c) => ({ id: `readout-${c.source}-${c.measurand}`, kind: "readout", title: null, config: { channel: `${c.source}.${c.measurand}`, sparkline: true, showSource: true } })),
    6,
    5,
  );

  const units = groupByUnit(channels);
  // A chart binds 1–8 channels (DESIGN-SPEC.md §3.3): a unit with more gets a chart per eight. Index the id: stripping
  // punctuation from the unit for readability can collide (e.g. "°C" and "C" both sanitise to "C"), so the index —
  // stable for a given rig, since channel order comes from its schema — is what actually guarantees uniqueness.
  const charts = units.flatMap(({ unit, channels: cs }, i) => {
    const parts: (typeof cs)[] = [];
    for (let k = 0; k < cs.length; k += 8) parts.push(cs.slice(k, k + 8));
    return parts.map((part, j) => ({ id: `chart-${i}${parts.length > 1 ? `-${j + 1}` : ""}-${unit.replace(/[^a-z0-9]+/gi, "")}`, kind: "chart", title: null as string | null, config: { channels: part.map((c) => `${c.source}.${c.measurand}`), window_s: 0, every: 0, y: "page" }, n: part.length }));
  });
  // Half-width charts (12 of 24) sit two abreast; a chart with more than four channels takes the full row so its legend
  // stays on one or two lines and leaves the plot its height (measured on `plant.toml` at 1440: 12 channels in a 12-column
  // chart made a 7-row legend and a 72px plot). Every chart of a rig shares one size so the rows stay rows.
  const wide = charts.length < 2 || charts.some((c) => c.n > 4);
  row(
    charts.map(({ n: _n, ...c }) => c),
    wide ? cols : 12,
    8,
  );

  row(
    // 8x8 is the "trends on" size (DESIGN-SPEC.md §3.4); "compact" (no trends) wants 6x5.
    bindings.loops.map((l) => ({ id: `loop-${l.name}`, kind: "loop", title: null, config: { loop: l.name, view: "full" } })),
    8,
    8,
  );

  row(
    bindings.actuators.map((a) => ({ id: `actuator-${a.name}`, kind: "actuator", title: null, config: { actuator: a.name, commands: [], showConfig: false } })),
    6,
    5,
  );

  const programY = y;
  widgets.push({ id: "program", kind: "program", title: null, x: 0, y: programY, w: 8, h: 6, config: { events: 5, interrupt: true } });
  widgets.push({ id: "events", kind: "events", title: null, x: 8, y: programY, w: 12, h: 6, config: { level: "INFO", limit: 20, scope: "" } });
  y += 6;

  return { schema_version: SCHEMA_VERSION, name: GENERATED_NAME, rig, description: "Made from the rig's schema: everything it has, in the order it declares it.", grid: { ...DEFAULT_GRID }, widgets };
}
