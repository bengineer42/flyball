import { alarmLevel, captionUnder, describeUnit, deviceOf, signalTitleAt, staleAfterS, withUnit, type Freshness, type Place, type SignalOut, fixed } from "@flyball/client";
import { TimeSeries } from "./TimeSeries.js";
import { Ref } from "../links.js";
import { useFreshness, useSignal, type TraceRef } from "../store/hooks.js";
import { PanelFrame } from "./PanelFrame.js";

export interface ReadoutProps {
  signal: SignalOut;
  /** Recent trace; the last point is the value shown. Omit with `source`. */
  t?: number[];
  v?: number[];
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
   * Rig-time freshness for stale detection (DESIGN-SPEC.md §2/B-3): the
   * device's poll period, its last sample time and the rig's current
   * time, all in rig seconds. Omitted, or with `nowS`/`lastSampleS` unset,
   * the readout is never stale.
   */
  fresh?: Freshness;
  /**
   * Body only -- value, range bar, sparkline -- with no `PanelFrame` of its
   * own: for a dashboard widget, whose frame is drawn once by `WidgetFrame`
   * (the one-frame rule, DESIGN-SPEC.md §10). The caller shows the severity
   * and the stale footer in that frame; `readoutLevel` computes them.
   */
  bare?: boolean;
}

/**
 * The severity and stale age a `Readout` would show, for a caller that draws the frame itself
 * (`bare`). The "over Ns" in the label uses the same threshold `alarmLevel` judged the level
 * against -- the signal's own `poll_s` when it has one, `fresh.periodS` (the device's poll
 * period) otherwise -- so the two never disagree (a signal polled slower than its device would
 * otherwise show a threshold shorter than the one that actually decided "stale").
 */
export function readoutLevel(signal: Pick<SignalOut, "warn" | "alarm" | "poll_s">, last: number | undefined, fresh: Freshness | undefined) {
  const level = alarmLevel(last, signal, fresh);
  const ageS = fresh?.lastSampleS != null && fresh?.nowS != null ? Math.round(fresh.nowS - fresh.lastSampleS) : null;
  const stale = level === "stale";
  return {
    level,
    ageS,
    label: stale ? `stale — last sample ${ageS} s ago (over ${staleAfterS(signal.poll_s ?? fresh?.periodS)} s)` : undefined,
    footer: stale ? `last sample ${ageS} s ago` : undefined,
  };
}

/** One signal as a tile: label, current value with unit, position in range, sparkline. */
export function Readout({ signal, t, v, source, sparkline = true, showDevice = true, place, windowS, every, exportHref, fresh, bare = false }: ReadoutProps) {
  const point = useSignal(source ? signal.address : undefined);
  const last = source ? point?.v : v && v.length ? v[v.length - 1] : undefined;
  // Store-fed with no freshness given, or a period only: the sample times and the device's period come from the store.
  const own = source !== undefined && (fresh === undefined || fresh.lastSampleS == null || fresh.nowS == null);
  const freshness = useFreshness(own ? signal.address : undefined, fresh?.periodS);
  if (own) fresh = freshness;
  const range = signal.range;
  const precision = signal.precision ?? 2;
  const title = signalTitleAt(signal, place ?? {});
  // Reserve the width of the widest value the range allows, so the number and
  // its unit stay put as digits come and go under noise.
  const widest = range ? Math.max(Math.abs(range[0]), Math.abs(range[1])) : 9999;
  const width = String(Math.floor(widest)).length + (range && range[0] < 0 ? 1 : 0) + (precision ? precision + 1 : 0);
  const fraction =
    last !== undefined && range ? Math.min(1, Math.max(0, (last - range[0]) / (range[1] - range[0]))) : null;
  const { level, label, footer } = readoutLevel(signal, last, fresh);
  // Band edges that fall inside the range, as ticks on the bar.
  const ticks = range
    ? (["warn", "alarm"] as const).flatMap((band) =>
        (signal[band] ?? [])
          .filter((edge) => edge > range[0] && edge < range[1])
          .map((edge) => ({ band, left: ((edge - range[0]) / (range[1] - range[0])) * 100 })),
      )
    : [];
  const body = (
    <>
      <div className="fb-readout-value">
        <span className="fb-readout-number" style={{ minWidth: `${width}ch` }}>
          {fixed(last, precision)}
        </span>
        <span className="fb-readout-unit">{describeUnit(signal.unit)}</span>
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
      // Always a footer line, blank while fresh: one that came and went with staleness resized the tile (as `Gauge` already reserves its own).
      footer={footer ?? "\u00a0"}
    >
      {body}
    </PanelFrame>
  );
}
