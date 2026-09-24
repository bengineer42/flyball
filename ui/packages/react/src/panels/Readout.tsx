import { alarmLevel, captionUnder, describeUnit, deviceOf, signalTitleAt, withUnit, type Place, type SignalOut, fixed } from "@flyball/client";
import { TimeSeries } from "./TimeSeries.js";
import { Ref } from "../links.js";
import { useBandLevel, useReading, type TraceRef } from "../store/hooks.js";
import type { SignalReading } from "../store/telemetry.js";
import { PanelFrame } from "./PanelFrame.js";
import { CaveatMark, QualityBadge, noValue } from "./quality.js";

export interface ReadoutProps {
  signal: SignalOut;
  /** Recent trace; the last point is the value shown (`NaN`/null: none). Omit with `source`. */
  t?: number[];
  v?: (number | null)[];
  /**
   * Read the signal from the telemetry store instead (`useTraceRef`): the
   * value re-renders this tile alone, at most four times a second, and the
   * sparkline draws itself twice a second while on screen.
   */
  source?: TraceRef;
  /** Show a sparkline of the trace under the value. */
  sparkline?: boolean;
  /** Show where the signal sits under the label; off when tiles are already grouped by device. */
  showDevice?: boolean;
  /**
   * The signal's device and namespace, for the caption (`Chamber · Humidity
   * sensors`, linked to the device). Without it the caption is the device's
   * name, linked.
   */
  place?: Place;
  /** Seconds the sparkline spans; scrolls once full. */
  windowS?: number;
  /** Draw one point in `every` on the sparkline. */
  every?: number;
  /** The same signal in the store, as an export URL; offered by the download menu of the chart the sparkline opens as. */
  exportHref?: string;
  /**
   * Body only -- value, range bar, sparkline -- with no `PanelFrame` of its
   * own: for a dashboard widget, whose frame is drawn once by `WidgetFrame`
   * (the one-frame rule, DESIGN-SPEC.md §10). The caller shows the severity
   * and the stale footer in that frame; `readoutLevel` computes them.
   */
  bare?: boolean;
}

/**
 * The severity a `Readout` would show, for a caller that draws the frame itself (`bare`), from the
 * signal's newest reading (a number alone for a caller with no rig feed): `stale` when the rig
 * pushed a `stale` reading, else the rig's band on it (`band`), else the value against the bands.
 * `label` is the dot's hover: why there is no value, and the last usable one.
 */
export function readoutLevel(signal: Pick<SignalOut, "warning" | "alarm" | "unit" | "precision">, reading: SignalReading | number | null | undefined, band?: "ok" | "warn" | "alarm" | "unknown") {
  const r = typeof reading === "number" || reading === null || reading === undefined ? undefined : reading;
  const value = r ? (typeof r.value === "number" ? r.value : null) : (reading as number | null | undefined);
  const level = alarmLevel(value, signal, band, r?.quality);
  const none = noValue(r, (v) => withUnit(fixed(v, signal.precision ?? 2), signal.unit));
  return {
    level,
    label: none ? `${none.label} · ${none.hint}` : level === "unknown" ? "band unknown: no value because of a fault" : undefined,
    footer: undefined as string | undefined,
  };
}

/** One signal as a tile: label, current value with unit, position in range, sparkline. */
export function Readout({ signal, t, v, source, sparkline = true, showDevice = true, place, windowS, every, exportHref, bare = false }: ReadoutProps) {
  const reading = useReading(source ? signal.address : undefined);
  const fromProps = v && v.length ? v[v.length - 1] : undefined;
  // Never the value before a reading with none: that one is shown as "—" and why.
  const last = source ? (typeof reading?.value === "number" ? reading.value : undefined) : typeof fromProps === "number" && !Number.isNaN(fromProps) ? fromProps : undefined;
  // No reading yet on a signal the rig has never read: "…", pending.
  const shown = reading ?? (signal.quality === "pending" ? { value: null, quality: "pending" as const } : undefined);
  const none = noValue(source ? shown : fromProps === null || (typeof fromProps === "number" && Number.isNaN(fromProps)) ? { value: null } : undefined, (x) => withUnit(fixed(x, signal.precision ?? 2), signal.unit));
  // Store-fed: the level is the rig's band alarm on this signal, not a check of the value here.
  const band = useBandLevel(source !== undefined ? signal.address : undefined);
  const range = signal.range;
  const precision = signal.precision ?? 2;
  const title = signalTitleAt(signal, place ?? {});
  // Reserve the width of the widest value the range allows, so the number and
  // its unit stay put as digits come and go under noise.
  const widest = range ? Math.max(Math.abs(range[0]), Math.abs(range[1])) : 9999;
  const width = String(Math.floor(widest)).length + (range && range[0] < 0 ? 1 : 0) + (precision ? precision + 1 : 0);
  const fraction =
    last !== undefined && range ? Math.min(1, Math.max(0, (last - range[0]) / (range[1] - range[0]))) : null;
  const { level, label, footer } = readoutLevel(signal, source ? reading : last, band);
  // Band edges that fall inside the range, as ticks on the bar.
  const ticks = range
    ? ([["warning", "warn"], ["alarm", "alarm"]] as const).flatMap(([key, band]) =>
        (signal[key] ?? [])
          .filter((edge) => edge > range[0] && edge < range[1])
          .map((edge) => ({ band, left: ((edge - range[0]) / (range[1] - range[0])) * 100 })),
      )
    : [];
  const body = (
    <>
      <div className="fb-readout-value">
        {!none && <CaveatMark caveats={reading?.caveats} />}
        <span className="fb-readout-number" style={{ minWidth: `${width}ch` }} title={none?.hint}>
          {none ? none.glyph : fixed(last, precision)}
        </span>
        {none ? <QualityBadge state={none} /> : <span className="fb-readout-unit">{describeUnit(signal.unit)}</span>}
      </div>
      {/* The bar is the range's, not the value's: with no value yet (or none at a paused moment) it stays, empty, so the tile keeps its height. */}
      {range && (
        <div className="fb-range" title={withUnit(`${range[0]} – ${range[1]}`, signal.unit)}>
          <div className="fb-range-fill" style={{ width: `${(fraction ?? 0) * 100}%` }} />
          {ticks.map((t) => (
            <div key={`${t.band}${t.left}`} className={`fb-range-tick fb-range-tick-${t.band}`} style={{ left: `${t.left}%` }} />
          ))}
        </div>
      )}
      {sparkline && <TimeSeries signal={signal} source={source} {...(source ? {} : { t: t ?? [], v: v ?? [] })} height={44} compact windowS={windowS} every={every} exportHref={exportHref} />}
    </>
  );
  if (bare) return <div className={`fb-readout fb-readout-bare fb-alarm-${level}`}>{body}</div>;
  return (
    <PanelFrame
      className="fb-readout"
      severity={level}
      severityLabel={label}
      title={<Ref kind="signal" name={signal.address}>{title}</Ref>}
      subtitle={!showDevice ? undefined : place && captionUnder(title, signal, place) ? <Ref kind="device" name={place.device?.name ?? deviceOf(signal.address)}>{captionUnder(title, signal, place)}</Ref> : <Ref kind="device" name={deviceOf(signal.address)} />}
      // Always a footer line, blank when there is nothing to add: one that came and went resized the tile (as `Gauge` already reserves its own).
      footer={footer ?? "\u00a0"}
    >
      {body}
    </PanelFrame>
  );
}
