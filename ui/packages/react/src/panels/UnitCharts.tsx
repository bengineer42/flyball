import type { ReactNode } from "react";
import type { ChannelOut } from "@flyball/client";
import type { Traces } from "../hooks/useSources.js";
import { channelKey } from "../hooks/useSources.js";
import { Ref } from "../links.js";
import { MultiSeries, type MultiSeriesTrace } from "./MultiSeries.js";
import type { YScale } from "./yscale.js";

export interface UnitChartsProps {
  /** Which channels to draw; each lands on the chart for its unit. */
  channels: ChannelOut[];
  traces: Traces;
  height?: number;
  windowS?: number;
  /** Rendered at the end of the header of the first chart (a window selector, say). */
  controls?: ReactNode;
  /** Trace label: `source.measurand` by default; `label` gives just the measurand's label. */
  labels?: "qualified" | "label";
  /** y axis scaling; `"range"` uses the widest declared range among the unit's channels. */
  yScale?: YScale;
  /** Draw one point in `every`. */
  every?: number;
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
 * °C axis. Pure; `useSamples` supplies the traces.
 */
export function UnitCharts({ channels, traces, height = 220, windowS, controls, labels = "qualified", yScale, every }: UnitChartsProps) {
  const groups = groupByUnit(channels);
  return (
    <>
      {groups.map(({ unit, channels: cs }, i) => {
        const series: MultiSeriesTrace[] = cs.map((c) => {
          const trace = traces[channelKey(c)];
          return {
            label: labels === "label" ? c.label : `${c.source}.${c.measurand}`,
            unit,
            t: trace?.t ?? [],
            v: trace?.v ?? [],
            precision: c.precision ?? undefined,
          };
        });
        return (
          <section key={unit} className="fb-unit-chart">
            <header className="fb-source-head">
              <h4>{unit}</h4>
              <span className="fb-muted fb-unit-chart-channels">
                {cs.map((c, j) => (
                  <span key={channelKey(c)}>
                    {j > 0 && ", "}
                    <Ref kind="channel" name={c.source} measurand={c.measurand} />
                  </span>
                ))}
              </span>
              {i === 0 && controls && <span className="fb-source-controls">{controls}</span>}
            </header>
            <MultiSeries series={series} unit={unit} height={height} windowS={windowS} yScale={yScale} range={widest(cs)} every={every} />
          </section>
        );
      })}
    </>
  );
}
