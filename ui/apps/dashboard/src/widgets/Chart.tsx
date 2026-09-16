import { memo, useMemo, useRef } from "react";
import { MultiSeries, useVisible, type MultiSeriesTrace, type Trace, type YScale } from "@flyball/react";
import type { ChannelOut } from "@flyball/client";
import { useBindings, useRigData, useTraces } from "../dashboard/context.js";
import { Missing } from "./Missing.js";
import { channelKeyOf, channelsSchema, EVERY_OPTIONS, pageOr, WINDOW_OPTIONS, Y_OPTIONS } from "./schema.js";
import { bodyPx, useFrozen } from "./size.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

/** Past this many points held, the chart thins to one in `every` on its own unless told otherwise. */
const DENSE = 2000;

const EMPTY: Trace = { channel: { source: "", measurand: "", unit: "", label: "", range: null, precision: null }, t: [], v: [] };

/** The widest declared range among channels, for a shared axis. */
function widest(channels: ChannelOut[]): [number, number] | null {
  const ranges = channels.map((c) => c.range).filter((r): r is [number, number] => !!r);
  return ranges.length ? [Math.min(...ranges.map((r) => r[0])), Math.max(...ranges.map((r) => r[1]))] : null;
}

/** The series prop, rebuilt only when one of *these* channels' traces changes -- not on every other source's sample. */
function useSeries(channels: ChannelOut[], traces: Record<string, Trace>, label: (c: ChannelOut) => string): MultiSeriesTrace[] {
  const picked = channels.map((c) => traces[channelKeyOf(c)] ?? EMPTY);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => channels.map((c, i) => ({ label: label(c), unit: c.unit, t: picked[i]!.t, v: picked[i]!.v, precision: c.precision ?? undefined })), picked);
}

const ChartWidget = memo(function ChartWidget({ config, widget }: WidgetComponentProps) {
  const bindings = useBindings();
  const { charts, exports, rowHeight } = useRigData();
  const traces = useTraces();
  const host = useRef<HTMLDivElement>(null);
  const visible = useVisible(host);
  // The canvas: the tile's body less the legend row (one line; a wrapped legend eats into the plot).
  const height = Math.max(72, bodyPx(widget.h, rowHeight, Boolean(widget.title ?? true)) - 30);
  const keys = Array.isArray(config.channels) ? (config.channels as unknown[]).map(String) : [];
  const channels = keys.map((k) => bindings.channels.find((c) => channelKeyOf(c) === k)).filter((c): c is ChannelOut => !!c);
  const missing = keys.filter((k) => !bindings.channels.some((c) => channelKeyOf(c) === k));
  const live = useSeries(channels, traces, (c) => `${bindings.sourceLabel(c.source)}.${c.label || c.measurand}`);
  // Off screen or in a hidden tab the chart keeps its last data: no `setData` per sample until it is seen again.
  const series = useFrozen(live, visible);
  const windowS = Number(config.window_s) || charts.windowS;
  const held = Math.max(0, ...series.map((s) => s.t.length));
  const every = Number(config.every) || (charts.every > 1 ? charts.every : held > DENSE ? Math.ceil(held / DENSE) : 1);
  const y: YScale = config.y === "auto" || config.y === "range" ? config.y : charts.yScale;
  const unit = channels[0]?.unit;
  if (!keys.length) return <Missing what="channels" name="" hint="Configure the widget to pick channels." />;
  if (!channels.length) return <Missing what="channels" name={missing.join(", ")} />;
  return (
    <div ref={host} className="fb-fill fb-chart-host">
      {missing.length > 0 && <div className="fb-muted fb-chart-title">missing: {missing.join(", ")}</div>}
      <MultiSeries series={series} unit={unit} title={widget.title ?? unit} height={height} windowS={windowS} yScale={y} range={widest(channels)} every={every} exportHref={exports.channels(channels)} />
    </div>
  );
});

export const chart: WidgetKind = {
  kind: "chart",
  label: "Chart",
  description: "Channels over time on one axis; a channel in another unit gets its own axis on the right.",
  category: "readings",
  defaultSize: { w: 6, h: 7 },
  minSize: { w: 3, h: 4 },
  cost: "chart",
  configSchema: (bindings) => ({
    type: "object",
    properties: {
      channels: channelsSchema(bindings),
      window_s: pageOr("Window", WINDOW_OPTIONS, "Seconds of history shown; the trace scrolls once it is full."),
      every: pageOr("Sample", EVERY_OPTIONS, "Draw one point in n. Left to the page, a dense trace (over 2000 points) thins itself."),
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
