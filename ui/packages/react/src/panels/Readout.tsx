import { alarmLevel, type ChannelOut } from "@flyball/client";
import { TimeSeries } from "./TimeSeries.js";
import { Ref } from "../links.js";

export interface ReadoutProps {
  channel: ChannelOut;
  /** Recent trace; the last point is the value shown. */
  t: number[];
  v: number[];
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
}

/** One channel as a tile: label, current value with unit, position in range, sparkline. */
export function Readout({ channel, t, v, sparkline = true, showSource = true, windowS, every, exportHref }: ReadoutProps) {
  const last = v.length ? v[v.length - 1] : undefined;
  const range = channel.range;
  const precision = channel.precision ?? 2;
  // Reserve the width of the widest value the range allows, so the number and
  // its unit stay put as digits come and go under noise.
  const widest = range ? Math.max(Math.abs(range[0]), Math.abs(range[1])) : 9999;
  const width = String(Math.floor(widest)).length + (range && range[0] < 0 ? 1 : 0) + (precision ? precision + 1 : 0);
  const fraction =
    last !== undefined && range ? Math.min(1, Math.max(0, (last - range[0]) / (range[1] - range[0]))) : null;
  const level = alarmLevel(last, channel);
  // Band edges that fall inside the range, as ticks on the bar.
  const ticks = range
    ? (["warn", "alarm"] as const).flatMap((band) =>
        (channel[band] ?? [])
          .filter((edge) => edge > range[0] && edge < range[1])
          .map((edge) => ({ band, left: ((edge - range[0]) / (range[1] - range[0])) * 100 })),
      )
    : [];
  return (
    <div className={`fb-readout fb-alarm-${level}`}>
      <div className="fb-readout-label">
        <Ref kind="channel" name={channel.source} measurand={channel.measurand}>{channel.label}</Ref>
        {showSource && <Ref kind="source" name={channel.source} className="fb-muted" />}
      </div>
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
      {sparkline && <TimeSeries channel={channel} t={t} v={v} height={44} compact windowS={windowS} every={every} exportHref={exportHref} />}
    </div>
  );
}
