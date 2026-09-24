/**
 * The dashboard a rig gets for nothing: made from its devices, so a fresh
 * install is not blank. One health strip, a readout per publishing signal,
 * a chart per unit, a faceplate per controller, a card per device with a
 * writable signal, then the programmer and the events. Never saved unless
 * someone saves it.
 */
import type { DashboardDocument, DashboardWidget } from "@flyball/client";
import { isHousekeeping, signalsOf, writable } from "@flyball/client";
import { groupByUnit } from "@flyball/react";
import { isNumeric } from "../valueReadout.js";
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
  // health 24×2, readout 6×5, chart 12×8, loop 8×8, device 6×4, program 8×6, events 12×6.
  row([{ id: "health", kind: "health", title: null, config: { tiles: ["rig", "recording", "devices", "controllers", "conditions"] } }], cols, 2);

  // A device's own housekeeping (its `conditions` list, anything under `last.`) is shown on its own tiles
  // elsewhere (health, device cards), never as a readout or chart signal here.
  const signals = bindings.signals.filter((s) => !isHousekeeping(s));
  // Ids carry the address with its dots turned to dashes, so an id stays a plain token.
  const slug = (address: string) => address.replace(/\./g, "-");
  row(
    signals.map((s) => ({ id: `readout-${slug(s.address)}`, kind: "readout", title: null, config: { address: s.address, sparkline: true, showDevice: true } })),
    6,
    5,
  );

  // A chart axis takes numbers only; a json/bool/str signal has no business on one.
  const units = groupByUnit(signals.filter(isNumeric));
  // A chart binds 1–8 signals (DESIGN-SPEC.md §3.3): a unit with more gets a chart per eight. Index the id: stripping
  // punctuation from the unit for readability can collide (e.g. "°C" and "C" both sanitise to "C"), so the index —
  // stable for a given rig, since signal order comes from its devices — is what actually guarantees uniqueness.
  const charts = units.flatMap(({ unit, signals: ss }, i) => {
    const parts: (typeof ss)[] = [];
    for (let k = 0; k < ss.length; k += 8) parts.push(ss.slice(k, k + 8));
    return parts.map((part, j) => ({ id: `chart-${i}${parts.length > 1 ? `-${j + 1}` : ""}-${unit.replace(/[^a-z0-9]+/gi, "")}`, kind: "chart", title: null as string | null, config: { addresses: part.map((s) => s.address), window_s: 0, every: 0, y: "page" }, n: part.length }));
  });
  // Half-width charts (12 of 24) sit two abreast; a chart with more than four signals takes the full row so its legend
  // stays on one or two lines and leaves the plot its height (measured on `plant` at 1440: 12 signals in a 12-column
  // chart made a 7-row legend and a 72px plot). Every chart of a rig shares one size so the rows stay rows.
  const wide = charts.length < 2 || charts.some((c) => c.n > 4);
  row(
    charts.map(({ n: _n, ...c }) => c),
    wide ? cols : 12,
    8,
  );

  row(
    // 8x8 is the "trends on" size (DESIGN-SPEC.md §3.4); "compact" (no trends) wants 6x5.
    bindings.controllers.map((c) => ({ id: `loop-${slug(c.name)}`, kind: "loop", title: null, config: { controller: c.name, view: "full" } })),
    8,
    8,
  );

  row(
    bindings.devices
      .filter((d) => d.kind !== "simulation" && signalsOf(d.signals).some(writable))
      .map((d) => ({ id: `device-${d.name}`, kind: "device", title: null, config: { device: d.name, commands: [], showConfig: false } })),
    6,
    4,
  );

  const programY = y;
  widgets.push({ id: "program", kind: "program", title: null, x: 0, y: programY, w: 8, h: 6, config: { events: 5, cancel: true } });
  widgets.push({ id: "events", kind: "events", title: null, x: 8, y: programY, w: 12, h: 6, config: { level: "INFO", limit: 20, subject_kind: "" } });
  y += 6;

  return { schema_version: SCHEMA_VERSION, name: GENERATED_NAME, rig, description: "Made from the rig's devices: everything it has, in the order it declares it.", grid: { ...DEFAULT_GRID }, widgets };
}
