import { memo, useMemo, useRef } from "react";
import { MultiSeries, useTraceRef, type MultiSeriesTrace, type YScale } from "@flyball/react";
import { deviceOf, signalTitle, type SignalOut } from "@flyball/client";
import { useBindings, useRigData } from "../dashboard/context.js";
import { useWidgetChrome } from "../dashboard/chrome.js";
import { isNumeric } from "../valueReadout.js";
import { Missing } from "./Missing.js";
import { signalsSchema, pageOr, WINDOW_OPTIONS, Y_OPTIONS } from "./schema.js";
import { useChartHeight } from "./size.js";
import type { WidgetType, WidgetComponentProps } from "./types.js";

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
  // A chart axis never takes a non-number: split what's configured into what actually charts, what the rig
  // doesn't have at all, and what it has but can't be a number (a json/bool/str signal, say) -- those two read
  // differently to a person ("not on this rig" vs "not a number and cannot be charted").
  const signals = addresses.map((a) => bindings.signalAt(a)).filter((s): s is SignalOut => !!s && isNumeric(s));
  const absent = addresses.filter((a) => !bindings.signalAt(a));
  const nonNumeric = addresses.map((a) => bindings.signalAt(a)).filter((s): s is SignalOut => !!s && !isNumeric(s));
  const nonNumericTitles = nonNumeric.map((s) => signalTitle(s, bindings.devices));
  const source = useTraceRef(useMemo(() => signals.map((s) => s.address), [addresses.join("\n"), bindings])); // eslint-disable-line react-hooks/exhaustive-deps
  const series = useMemo<MultiSeriesTrace[]>(
    () => {
      // A trace is the signal's title; the device joins it only when two traces would otherwise read the same.
      const titles = signals.map((s) => signalTitle(s, bindings.devices));
      return signals.map((s, i) => ({
        label: titles.filter((t) => t === titles[i]).length > 1 ? `${titles[i]} · ${bindings.deviceLabel(deviceOf(s.address))}` : titles[i]!,
        unit: s.unit,
        quantity: s.quantity,
        key: s.address,
        precision: s.precision ?? undefined,
        hint: `${bindings.deviceLabel(deviceOf(s.address))} · ${s.address}`,
      }));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [bindings, source],
  );
  const windowS = Number(config.window_s) || charts.windowS;
  const y: YScale = config.y === "auto" || config.y === "range" ? config.y : charts.yScale;
  const unit = signals[0]?.unit;
  // Title row: the unit is the title (`labelFor`); the signals' devices are the subtitle, as the spec's "°C  zone1 · zone2 · zone3" (§3.3).
  const subtitle = useMemo(() => (signals.length ? [...new Set(signals.map((s) => bindings.deviceLabel(deviceOf(s.address))))].join(" · ") : undefined), [bindings, source]); // eslint-disable-line react-hooks/exhaustive-deps
  useWidgetChrome(signals.length ? { subtitle } : null);
  if (!addresses.length) return <Missing what="signals" name="" hint="Configure the widget to pick signals." />;
  if (!signals.length) {
    // Nothing to chart: say once why, distinguishing "not on this rig" from "on this rig but not a number".
    if (absent.length && !nonNumeric.length) return <Missing what="signals" name={absent.join(", ")} />;
    if (nonNumeric.length && !absent.length)
      return <Missing what="signals" name={nonNumericTitles.join(", ")} reason={nonNumericTitles.length > 1 ? "are not numbers and cannot be charted" : "is not a number and cannot be charted"} />;
    return <Missing what="signals" name={[...absent, ...nonNumericTitles].join(", ")} reason="cannot be charted: some are not on this rig, some are not numbers" />;
  }
  return (
    <div ref={host} className="fb-fill fb-chart-host">
      {(absent.length > 0 || nonNumeric.length > 0) && (
        <div className="fb-muted fb-chart-title">
          {absent.length > 0 && `${absent.join(", ")} not on this rig`}
          {absent.length > 0 && nonNumeric.length > 0 && "; "}
          {nonNumeric.length > 0 && `${nonNumericTitles.join(", ")} not a number`}
        </div>
      )}
      <MultiSeries series={series} source={source} id={widget.id} unit={unit} title={widget.label ?? unit} height={height} windowS={windowS} yScale={y} range={widest(signals)} exportHref={exports.signals(signals)} />
    </div>
  );
});

export const chart: WidgetType = {
  type: "chart",
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
      addresses: signalsSchema(bindings, "Signals", true),
      window_s: pageOr("Window", WINDOW_OPTIONS, "Seconds of history shown; the trace scrolls once it is full."),
      y: pageOr("Y axis", Y_OPTIONS),
    },
    required: ["addresses"],
  }),
  uiSchema: { window_s: { "ui:widget": "select" }, y: { "ui:widget": "select" } },
  defaultConfig: (bindings) => {
    const first = bindings.signals.find(isNumeric);
    const same = first ? bindings.signals.filter((s) => isNumeric(s) && s.unit === first.unit) : [];
    return { addresses: same.map((s) => s.address), window_s: 0, y: "page" };
  },
  labelFor: (config, bindings) => {
    const addresses = Array.isArray(config.addresses) ? (config.addresses as unknown[]).map(String) : [];
    const units = new Set(addresses.map((a) => bindings.signalAt(a)?.unit).filter(Boolean));
    return units.size === 1 ? [...units][0] : addresses.length ? `${addresses.length} signals` : "chart";
  },
  Component: ChartWidget,
};
