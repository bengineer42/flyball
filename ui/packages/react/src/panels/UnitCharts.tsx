import { memo, type ReactNode } from "react";
import { describeSignal, deviceOf, type DeviceRef, deviceTitle, groupTitle, placeOf, type Place, type SignalOut, signalTitleAt, unitTitle } from "@flyball/client";
import type { Traces } from "../hooks/useTraces.js";
import { Ref } from "../links.js";
import { MultiSeries, type MultiSeriesTrace } from "./MultiSeries.js";
import { PanelFrame } from "./PanelFrame.js";
import type { YScale } from "./yscale.js";
import type { TraceRef } from "../store/hooks.js";

export interface UnitChartsProps {
  /** Which signals to draw; each lands on the chart for its unit. */
  signals: SignalOut[];
  /** The points, when the charts are fed by props; omit with `source`. */
  traces?: Traces;
  /** Draw from the telemetry store instead (`useTraceRef(addresses)`): no re-render per sample. */
  source?: TraceRef;
  /** Plot height, or `"auto"` to follow the width. */
  height?: number | "auto";
  windowS?: number;
  /** Rendered at the end of the header of the first chart (a window selector, say). */
  controls?: ReactNode;
  /**
   * Trace label: `qualified` (default) names the signal by where it sits --
   * its namespace (`Chamber humidity`), and its device when the chart spans
   * more than one (`Expected humidity · Pump blender`); `label` gives just
   * the signal's own label. The address is the legend row's hover hint.
   */
  labels?: "qualified" | "label";
  /** The devices the signals belong to, with their trees, for the labels; a device not here is named by its `name`. */
  devices?: ReadonlyArray<DeviceRef>;
  /** y axis scaling; `"range"` uses the widest declared range among the unit's signals. */
  yScale?: YScale;
  /** Draw one point in `every`. */
  every?: number;
  /** Where the store holds a chart's signals, as an export URL; the chart's download menu offers it. */
  exportHref?(signals: SignalOut[]): string | undefined;
}

/** The widest declared range among signals, for a shared axis. */
function widest(signals: SignalOut[]): [number, number] | null {
  const ranges = signals.map((s) => s.range).filter((r): r is [number, number] => !!r);
  return ranges.length ? [Math.min(...ranges.map((r) => r[0])), Math.max(...ranges.map((r) => r[1]))] : null;
}

/** Signals grouped by unit, in first-seen order. */
export function groupByUnit(signals: SignalOut[]): Array<{ unit: string; signals: SignalOut[] }> {
  const groups = new Map<string, SignalOut[]>();
  for (const s of signals) (groups.get(s.unit) ?? groups.set(s.unit, []).get(s.unit)!).push(s);
  return [...groups].map(([unit, ss]) => ({ unit, signals: ss }));
}

/**
 * One chart per unit, every signal in that unit as a trace on it — the
 * process, dry and wet humidities on one %RH axis, their temperatures on one
 * °C axis. Pure; `useTraces` supplies the traces, or `useTraceRef` a `source`.
 */
export function UnitCharts({ signals, traces, source, height = 220, windowS, controls, labels = "qualified", devices, yScale, every, exportHref }: UnitChartsProps) {
  const groups = groupByUnit(signals);
  return (
    <>
      {groups.map(({ unit, signals: ss }, i) => (
        <UnitChart key={unit} unit={unit} signals={ss} traces={traces} source={source} height={height} windowS={windowS} controls={i === 0 ? controls : undefined} labels={labels} devices={devices} yScale={yScale} every={every} exportHref={exportHref} />
      ))}
    </>
  );
}

type UnitChartProps = Omit<UnitChartsProps, "signals"> & { unit: string; signals: SignalOut[] };

const sameSignals = (a: SignalOut[], b: SignalOut[]) => a.length === b.length && a.every((s, i) => s === b[i]);

/**
 * One unit's chart. Memoised so the page above re-rendering (a poll landing)
 * does not re-render twenty charts; the signal arrays are compared by their
 * members, since `groupByUnit` builds new ones each time.
 */
const UnitChart = memo(
  function UnitChart({ unit, signals: ss, traces, source, height, windowS, controls, labels, devices, yScale, every, exportHref }: UnitChartProps) {
    // Where each signal sits; a device the caller did not describe is named by its name.
    const placeFor = (s: SignalOut): Place => {
      const place = placeOf(s.address, devices ?? []);
      return place.device ? place : { device: { name: deviceOf(s.address), label: null } };
    };
    const places = ss.map(placeFor);
    // The device qualifies a root signal only when the chart spans more than one device.
    const multiDevice = new Set(ss.map((s) => deviceOf(s.address))).size > 1;
    const series: MultiSeriesTrace[] = ss.map((s, i) => {
      const trace = traces?.[s.address];
      const place = places[i]!;
      return {
        label: labels === "label" ? describeSignal(s) : multiDevice && place.device && !place.namespace ? `${signalTitleAt(s, place)} · ${deviceTitle(place.device)}` : signalTitleAt(s, place),
        unit,
        quantity: s.quantity,
        key: s.address,
        hint: s.address,
        ...(source ? {} : { t: trace?.t ?? [], v: trace?.v ?? [] }),
        precision: s.precision ?? undefined,
      };
    });
    // The distinct places on the chart -- namespaces, else devices -- in first-seen order, each linking to its device.
    const groups = new Map<string, { device: string; address: string }>();
    places.forEach((place, i) => {
      const title = groupTitle(place);
      if (title && !groups.has(title)) groups.set(title, { device: place.device!.name, address: place.namespace?.address ?? place.device!.name });
    });
    const title = unitTitle(unit, ss);
    const heading = `${title} — ${[...groups.keys()].join(", ") || ss.map((s) => describeSignal(s)).join(", ")}`;
    return (
      <PanelFrame
        className="fb-unit-chart"
        title={<span title={heading}>{title}</span>}
        subtitle={
          <span className="fb-unit-chart-channels">
            {[...groups].map(([name, group], j) => (
              <span key={group.address}>
                {j > 0 && ", "}
                <Ref kind="device" name={group.device}>
                  {name}
                </Ref>
              </span>
            ))}
          </span>
        }
        actions={controls}
      >
        <MultiSeries series={series} source={source} id={`unit:${unit}`} unit={unit} title={heading} height={height} windowS={windowS} yScale={yScale} range={widest(ss)} every={every} exportHref={exportHref?.(ss)} />
      </PanelFrame>
    );
  },
  (a, b) =>
    a.unit === b.unit &&
    sameSignals(a.signals, b.signals) &&
    a.traces === b.traces &&
    a.source === b.source &&
    a.height === b.height &&
    a.windowS === b.windowS &&
    a.controls === b.controls &&
    a.labels === b.labels &&
    a.devices === b.devices &&
    a.yScale === b.yScale &&
    a.every === b.every &&
    a.exportHref === b.exportHref,
);
