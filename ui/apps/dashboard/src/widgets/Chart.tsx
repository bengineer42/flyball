import { memo, useMemo, useRef } from "react";
import { MultiSeries, useTraceRef, type MultiSeriesTrace, type YScale } from "@flyball/react";
import { describeSignal, deviceOf, type SignalOut } from "@flyball/client";
import { useBindings, useRigData } from "../dashboard/context.js";
import { useWidgetChrome } from "../dashboard/chrome.js";
import { Missing } from "./Missing.js";
import { signalsSchema, EVERY_OPTIONS, pageOr, WINDOW_OPTIONS, Y_OPTIONS } from "./schema.js";
import { useChartHeight } from "./size.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

/** The widest declared range among signals, for a shared axis. */
function widest(signals: SignalOut[]): [number, number] | null {
  const ranges = signals.map((s) => s.range).filter((r): r is [number, number] => !!r);
  return ranges.length ? [Math.min(...ranges.map((r) => r[0])), Math.max(...ranges.map((r) => r[1]))] : null;
}

/**
 * Signals over time, drawn straight from the telemetry store: the chart
 * subscribes to its signals itself (`source`), redraws at most ten times a
 * second while on screen and in a shown tab, and thins to twice its width
 * in points. This component renders on configuration and size changes
 * only, never on samples. `window.__fb.chartsById[widget.id]` counts its
 * redraws for the performance budget (DESIGN-SPEC.md §6).
 */
const ChartWidget = memo(function ChartWidget({ config, widget }: WidgetComponentProps) {
  const bindings = useBindings();
  const { charts, exports } = useRigData();
  // The canvas: the body as measured, less the legend under it (a legend that wraps to two rows takes it from the plot, never from the frame).
  const host = useRef<HTMLDivElement>(null);
  const height = useChartHeight(host);
  const addresses = Array.isArray(config.addresses) ? (config.addresses as unknown[]).map(String) : [];
  const signals = addresses.map((a) => bindings.signalAt(a)).filter((s): s is SignalOut => !!s);
  const missing = addresses.filter((a) => !bindings.signalAt(a));
  const source = useTraceRef(useMemo(() => signals.map((s) => s.address), [addresses.join("\n"), bindings])); // eslint-disable-line react-hooks/exhaustive-deps
  const series = useMemo<MultiSeriesTrace[]>(
    () => signals.map((s) => ({ label: `${bindings.deviceLabel(deviceOf(s.address))}.${describeSignal(s)}`, unit: s.unit, key: s.address, precision: s.precision ?? undefined })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [bindings, source],
  );
  const windowS = Number(config.window_s) || charts.windowS;
  // Density is the chart's own business now (it thins to its width); `every` is only ever what someone asked for.
  const every = Number(config.every) || (charts.every > 1 ? charts.every : 1);
  const y: YScale = config.y === "auto" || config.y === "range" ? config.y : charts.yScale;
  const unit = signals[0]?.unit;
  // Title row: the unit is the title (`titleFor`); the signals' devices are the subtitle, as the spec's "°C  zone1 · zone2 · zone3" (§3.3).
  const subtitle = useMemo(() => (signals.length ? [...new Set(signals.map((s) => bindings.deviceLabel(deviceOf(s.address))))].join(" · ") : undefined), [bindings, source]); // eslint-disable-line react-hooks/exhaustive-deps
  useWidgetChrome(signals.length ? { subtitle } : null);
  if (!addresses.length) return <Missing what="signals" name="" hint="Configure the widget to pick signals." />;
  if (!signals.length) return <Missing what="signals" name={missing.join(", ")} />;
  return (
    <div ref={host} className="fb-fill fb-chart-host">
      {missing.length > 0 && <div className="fb-muted fb-chart-title">missing: {missing.join(", ")}</div>}
      <MultiSeries series={series} source={source} id={widget.id} unit={unit} title={widget.title ?? unit} height={height} windowS={windowS} yScale={y} range={widest(signals)} every={every} exportHref={exports.signals(signals)} />
    </div>
  );
});

export const chart: WidgetKind = {
  kind: "chart",
  label: "Chart",
  description: "Signals over time on one axis; a signal in another unit gets its own axis on the right.",
  category: "readings",
  // 12×8: a 222px body gives a 190px plot over a one-line legend; 8×5 is the floor at which axes and legend still read (DESIGN-SPEC.md §10).
  defaultSize: { w: 12, h: 8 },
  minSize: { w: 8, h: 5 },
  cost: "chart",
  configSchema: (bindings) => ({
    type: "object",
    properties: {
      addresses: signalsSchema(bindings),
      window_s: pageOr("Window", WINDOW_OPTIONS, "Seconds of history shown; the trace scrolls once it is full."),
      every: pageOr("Sample", EVERY_OPTIONS, "Draw one point in n. Left to the page, a dense trace thins itself to twice the chart's width."),
      y: pageOr("Y axis", Y_OPTIONS),
    },
    required: ["addresses"],
  }),
  uiSchema: { window_s: { "ui:widget": "select" }, every: { "ui:widget": "select" }, y: { "ui:widget": "select" } },
  defaultConfig: (bindings) => {
    const first = bindings.signals[0];
    const same = first ? bindings.signals.filter((s) => s.unit === first.unit) : [];
    return { addresses: same.map((s) => s.address), window_s: 0, every: 0, y: "page" };
  },
  titleFor: (config, bindings) => {
    const addresses = Array.isArray(config.addresses) ? (config.addresses as unknown[]).map(String) : [];
    const units = new Set(addresses.map((a) => bindings.signalAt(a)?.unit).filter(Boolean));
    return units.size === 1 ? [...units][0] : addresses.length ? `${addresses.length} signals` : "chart";
  },
  Component: ChartWidget,
};
