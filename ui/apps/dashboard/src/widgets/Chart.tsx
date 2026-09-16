import { memo, useMemo, useRef } from "react";
import { MultiSeries, useTraceRef, type MultiSeriesTrace, type YScale } from "@flyball/react";
import type { ChannelOut } from "@flyball/client";
import { useBindings, useRigData } from "../dashboard/context.js";
import { useWidgetChrome } from "../dashboard/chrome.js";
import { Missing } from "./Missing.js";
import { channelKeyOf, channelsSchema, EVERY_OPTIONS, pageOr, WINDOW_OPTIONS, Y_OPTIONS } from "./schema.js";
import { useChartHeight } from "./size.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

/** The widest declared range among channels, for a shared axis. */
function widest(channels: ChannelOut[]): [number, number] | null {
  const ranges = channels.map((c) => c.range).filter((r): r is [number, number] => !!r);
  return ranges.length ? [Math.min(...ranges.map((r) => r[0])), Math.max(...ranges.map((r) => r[1]))] : null;
}

/**
 * Channels over time, drawn straight from the telemetry store: the chart
 * subscribes to its channels itself (`source`), redraws at most ten times a
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
  const keys = Array.isArray(config.channels) ? (config.channels as unknown[]).map(String) : [];
  const channels = keys.map((k) => bindings.channels.find((c) => channelKeyOf(c) === k)).filter((c): c is ChannelOut => !!c);
  const missing = keys.filter((k) => !bindings.channels.some((c) => channelKeyOf(c) === k));
  const source = useTraceRef(channels);
  const series = useMemo<MultiSeriesTrace[]>(
    () => channels.map((c) => ({ label: `${bindings.sourceLabel(c.source)}.${c.label || c.measurand}`, unit: c.unit, key: channelKeyOf(c), precision: c.precision ?? undefined })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [bindings, source],
  );
  const windowS = Number(config.window_s) || charts.windowS;
  // Density is the chart's own business now (it thins to its width); `every` is only ever what someone asked for.
  const every = Number(config.every) || (charts.every > 1 ? charts.every : 1);
  const y: YScale = config.y === "auto" || config.y === "range" ? config.y : charts.yScale;
  const unit = channels[0]?.unit;
  // Title row: the unit is the title (`titleFor`); the channels' sources are the subtitle, as the spec's "°C  zone1 · zone2 · zone3" (§3.3).
  const subtitle = useMemo(() => (channels.length ? channels.map((c) => bindings.sourceLabel(c.source)).join(" · ") : undefined), [bindings, source]); // eslint-disable-line react-hooks/exhaustive-deps
  useWidgetChrome(channels.length ? { subtitle } : null);
  if (!keys.length) return <Missing what="channels" name="" hint="Configure the widget to pick channels." />;
  if (!channels.length) return <Missing what="channels" name={missing.join(", ")} />;
  return (
    <div ref={host} className="fb-fill fb-chart-host">
      {missing.length > 0 && <div className="fb-muted fb-chart-title">missing: {missing.join(", ")}</div>}
      <MultiSeries series={series} source={source} id={widget.id} unit={unit} title={widget.title ?? unit} height={height} windowS={windowS} yScale={y} range={widest(channels)} every={every} exportHref={exports.channels(channels)} />
    </div>
  );
});

export const chart: WidgetKind = {
  kind: "chart",
  label: "Chart",
  description: "Channels over time on one axis; a channel in another unit gets its own axis on the right.",
  category: "readings",
  // 12×8: a 222px body gives a 190px plot over a one-line legend; 8×5 is the floor at which axes and legend still read (DESIGN-SPEC.md §10).
  defaultSize: { w: 12, h: 8 },
  minSize: { w: 8, h: 5 },
  cost: "chart",
  configSchema: (bindings) => ({
    type: "object",
    properties: {
      channels: channelsSchema(bindings),
      window_s: pageOr("Window", WINDOW_OPTIONS, "Seconds of history shown; the trace scrolls once it is full."),
      every: pageOr("Sample", EVERY_OPTIONS, "Draw one point in n. Left to the page, a dense trace thins itself to twice the chart's width."),
      y: pageOr("Y axis", Y_OPTIONS),
    },
    required: ["channels"],
  }),
  uiSchema: { window_s: { "ui:widget": "select" }, every: { "ui:widget": "select" }, y: { "ui:widget": "select" } },
  defaultConfig: (bindings) => {
    const first = bindings.channels[0];
    const same = first ? bindings.channels.filter((c) => c.unit === first.unit) : [];
    return { channels: same.map(channelKeyOf), window_s: 0, every: 0, y: "page" };
  },
  titleFor: (config, bindings) => {
    const keys = Array.isArray(config.channels) ? (config.channels as unknown[]).map(String) : [];
    const units = new Set(keys.map((k) => bindings.channels.find((c) => channelKeyOf(c) === k)?.unit).filter(Boolean));
    return units.size === 1 ? [...units][0] : keys.length ? `${keys.length} channels` : "chart";
  },
  Component: ChartWidget,
};
