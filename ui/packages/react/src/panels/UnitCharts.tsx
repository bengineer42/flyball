import { memo, type ReactNode } from "react";
import type { ChannelOut } from "@flyball/client";
import type { Traces } from "../hooks/useSources.js";
import { channelKey } from "../hooks/useSources.js";
import { Ref } from "../links.js";
import { MultiSeries, type MultiSeriesTrace } from "./MultiSeries.js";
import { PanelFrame } from "./PanelFrame.js";
import type { YScale } from "./yscale.js";
import type { TraceRef } from "../store/hooks.js";

export interface UnitChartsProps {
  /** Which channels to draw; each lands on the chart for its unit. */
  channels: ChannelOut[];
  /** The points, when the charts are fed by props; omit with `source`. */
  traces?: Traces;
  /** Draw from the telemetry store instead (`useTraceRef(channels)`): no re-render per sample. */
  source?: TraceRef;
  /** Plot height, or `"auto"` to follow the width. */
  height?: number | "auto";
  windowS?: number;
  /** Rendered at the end of the header of the first chart (a window selector, say). */
  controls?: ReactNode;
  /** Trace label: `source.measurand` by default (the source's label when `sources` gives one); `label` gives just the measurand's label. */
  labels?: "qualified" | "label";
  /** The sources the channels belong to, for their labels; a source not here is named by its `name`. */
  sources?: ReadonlyArray<{ name: string; label?: string | null }>;
  /** y axis scaling; `"range"` uses the widest declared range among the unit's channels. */
  yScale?: YScale;
  /** Draw one point in `every`. */
  every?: number;
  /** Where the store holds a chart's channels, as an export URL; the chart's download menu offers it. */
  exportHref?(channels: ChannelOut[]): string | undefined;
}

/** The widest declared range among channels, for a shared axis. */
function widest(channels: ChannelOut[]): [number, number] | null {
  const ranges = channels.map((c) => c.range).filter((r): r is [number, number] => !!r);
  return ranges.length ? [Math.min(...ranges.map((r) => r[0])), Math.max(...ranges.map((r) => r[1]))] : null;
}

/** Channels grouped by unit, in first-seen order. */
export function groupByUnit(channels: ChannelOut[]): Array<{ unit: string; channels: ChannelOut[] }> {
  const groups = new Map<string, ChannelOut[]>();
  for (const c of channels) (groups.get(c.unit) ?? groups.set(c.unit, []).get(c.unit)!).push(c);
  return [...groups].map(([unit, cs]) => ({ unit, channels: cs }));
}

/**
 * One chart per unit, every channel in that unit as a trace on it — the
 * process, dry and wet humidities on one %RH axis, their temperatures on one
 * °C axis. Pure; `useSamples` supplies the traces, or `useTraceRef` a `source`.
 */
export function UnitCharts({ channels, traces, source, height = 220, windowS, controls, labels = "qualified", sources, yScale, every, exportHref }: UnitChartsProps) {
  const groups = groupByUnit(channels);
  return (
    <>
      {groups.map(({ unit, channels: cs }, i) => (
        <UnitChart key={unit} unit={unit} channels={cs} traces={traces} source={source} height={height} windowS={windowS} controls={i === 0 ? controls : undefined} labels={labels} sources={sources} yScale={yScale} every={every} exportHref={exportHref} />
      ))}
    </>
  );
}

type UnitChartProps = Omit<UnitChartsProps, "channels"> & { unit: string; channels: ChannelOut[] };

const sameChannels = (a: ChannelOut[], b: ChannelOut[]) => a.length === b.length && a.every((c, i) => c === b[i]);

/**
 * One unit's chart. Memoised so the page above re-rendering (a poll landing)
 * does not re-render twenty charts; the channel arrays are compared by their
 * members, since `groupByUnit` builds new ones each time.
 */
const UnitChart = memo(
  function UnitChart({ unit, channels: cs, traces, source, height, windowS, controls, labels, sources, yScale, every, exportHref }: UnitChartProps) {
    const sourceLabel = (name: string) => sources?.find((s) => s.name === name)?.label ?? name;
    const series: MultiSeriesTrace[] = cs.map((c) => {
      const trace = traces?.[channelKey(c)];
      return {
        label: labels === "label" ? c.label : `${sourceLabel(c.source)}.${c.label || c.measurand}`,
        unit,
        key: channelKey(c),
        ...(source ? {} : { t: trace?.t ?? [], v: trace?.v ?? [] }),
        precision: c.precision ?? undefined,
      };
    });
    return (
      <PanelFrame
        className="fb-unit-chart"
        title={unit}
        subtitle={
          <span className="fb-unit-chart-channels">
            {cs.map((c, j) => (
              <span key={channelKey(c)}>
                {j > 0 && ", "}
                <Ref kind="channel" name={c.source} measurand={c.measurand} />
              </span>
            ))}
          </span>
        }
        actions={controls}
      >
        <MultiSeries series={series} source={source} id={`unit:${unit}`} unit={unit} title={unit} height={height} windowS={windowS} yScale={yScale} range={widest(cs)} every={every} exportHref={exportHref?.(cs)} />
      </PanelFrame>
    );
  },
  (a, b) =>
    a.unit === b.unit &&
    sameChannels(a.channels, b.channels) &&
    a.traces === b.traces &&
    a.source === b.source &&
    a.height === b.height &&
    a.windowS === b.windowS &&
    a.controls === b.controls &&
    a.labels === b.labels &&
    a.sources === b.sources &&
    a.yScale === b.yScale &&
    a.every === b.every &&
    a.exportHref === b.exportHref,
);
