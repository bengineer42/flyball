import { alarmLevel, staleAfterS, type ChannelOut, type Freshness } from "@flyball/client";
import { TimeSeries } from "./TimeSeries.js";
import { Ref } from "../links.js";
import { useFreshness, useLatest, type TraceRef } from "../store/hooks.js";
import { channelKey } from "../store/telemetry.js";
import { PanelFrame } from "./PanelFrame.js";

export interface ReadoutProps {
  channel: ChannelOut;
  /** Recent trace; the last point is the value shown. Omit with `source`. */
  t?: number[];
  v?: number[];
  /**
   * Read the channel from the telemetry store instead (`useTraceRef`): the
   * value re-renders this tile alone, at most four times a second, and the
   * sparkline draws itself twice a second while on screen.
   */
  source?: TraceRef;
  /** Show a sparkline of the trace under the value. */
  sparkline?: boolean;
  /** Show the source name beside the label; off when tiles are already grouped by source. */
  showSource?: boolean;
  /** Seconds the sparkline spans; scrolls once full. */
  windowS?: number;
  /** Draw one point in `every` on the sparkline. */
  every?: number;
  /** The same channel in the store, as an export URL; offered by the download menu of the chart the sparkline opens as. */
  exportHref?: string;
  /**
   * Rig-time freshness for stale detection (DESIGN-SPEC.md §2/B-3): the
   * channel's reader period, its last sample time and the rig's current
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

/** The severity and stale age a `Readout` would show, for a caller that draws the frame itself (`bare`). */
export function readoutLevel(channel: ChannelOut, last: number | undefined, fresh: Freshness | undefined) {
  const level = alarmLevel(last, channel, fresh);
  const ageS = fresh?.lastSampleS != null && fresh?.nowS != null ? Math.round(fresh.nowS - fresh.lastSampleS) : null;
  const stale = level === "stale";
  return {
    level,
    ageS,
    label: stale ? `stale — last sample ${ageS} s ago (over ${staleAfterS(fresh?.periodS)} s)` : undefined,
    footer: stale ? `last sample ${ageS} s ago` : undefined,
  };
}

/** One channel as a tile: label, current value with unit, position in range, sparkline. */
export function Readout({ channel, t, v, source, sparkline = true, showSource = true, windowS, every, exportHref, fresh, bare = false }: ReadoutProps) {
  const point = useLatest(source ? channelKey(channel) : undefined);
  const last = source ? point?.v : v && v.length ? v[v.length - 1] : undefined;
  // In source mode the caller passes the reader period only; the sample times come from the store.
  const own = source !== undefined && fresh !== undefined && (fresh.lastSampleS == null || fresh.nowS == null);
  const freshness = useFreshness(own ? channelKey(channel) : undefined, fresh?.periodS);
  if (own) fresh = freshness;
  const range = channel.range;
  const precision = channel.precision ?? 2;
  // Reserve the width of the widest value the range allows, so the number and
  // its unit stay put as digits come and go under noise.
  const widest = range ? Math.max(Math.abs(range[0]), Math.abs(range[1])) : 9999;
  const width = String(Math.floor(widest)).length + (range && range[0] < 0 ? 1 : 0) + (precision ? precision + 1 : 0);
  const fraction =
    last !== undefined && range ? Math.min(1, Math.max(0, (last - range[0]) / (range[1] - range[0]))) : null;
  const { level, label, footer } = readoutLevel(channel, last, fresh);
  // Band edges that fall inside the range, as ticks on the bar.
  const ticks = range
    ? (["warn", "alarm"] as const).flatMap((band) =>
        (channel[band] ?? [])
          .filter((edge) => edge > range[0] && edge < range[1])
          .map((edge) => ({ band, left: ((edge - range[0]) / (range[1] - range[0])) * 100 })),
      )
    : [];
  const body = (
    <>
      <div className="fb-readout-value">
        <span className="fb-readout-number" style={{ minWidth: `${width}ch` }}>
          {last === undefined ? "—" : last.toFixed(precision)}
        </span>
        <span className="fb-readout-unit">{channel.unit}</span>
      </div>
      {fraction !== null && (
        <div className="fb-range" title={`${range![0]} – ${range![1]} ${channel.unit}`}>
          <div className="fb-range-fill" style={{ width: `${fraction * 100}%` }} />
          {ticks.map((t) => (
            <div key={`${t.band}${t.left}`} className={`fb-range-tick fb-range-tick-${t.band}`} style={{ left: `${t.left}%` }} />
          ))}
        </div>
      )}
      {sparkline && <TimeSeries channel={channel} source={source} {...(source ? {} : { t: t ?? [], v: v ?? [] })} height={44} compact windowS={windowS} every={every} exportHref={exportHref} />}
    </>
  );
  if (bare) return <div className={`fb-readout fb-readout-bare fb-alarm-${level}`}>{body}</div>;
  return (
    <PanelFrame
      className="fb-readout"
      severity={level}
      severityLabel={label}
      title={<Ref kind="channel" name={channel.source} measurand={channel.measurand}>{channel.label}</Ref>}
      subtitle={showSource ? <Ref kind="source" name={channel.source} /> : undefined}
      footer={footer}
    >
      {body}
    </PanelFrame>
  );
}
