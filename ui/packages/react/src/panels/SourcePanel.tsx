import type { ChannelOut, SourceOut } from "@flyball/client";
import { channelKey, type Trace, type Traces } from "../hooks/useSources.js";
import { TimeSeries } from "./TimeSeries.js";
import type { YScale } from "./yscale.js";
import { Ref } from "../links.js";
import { useLatest, type TraceRef } from "../store/hooks.js";

export interface SourcePanelProps {
  source: SourceOut;
  /** The points, when the charts are fed by props; omit with `live`. */
  traces?: Traces;
  /** Draw from the telemetry store instead (`useTraceRef(source.channels)`): no re-render per sample. */
  live?: TraceRef;
  /** Plot height per channel, or `"auto"` to follow the width. */
  height?: number | "auto";
  /** Seconds of history each chart shows; charts scroll once it is full. */
  windowS?: number;
  /** Rendered at the end of the header: a window selector, for instance. */
  controls?: React.ReactNode;
  /** y axis scaling for every chart in the panel. */
  yScale?: YScale;
  /** Draw one point in `every`. */
  every?: number;
  /** Where the store holds a channel, as an export URL; each chart's download menu offers it. */
  exportHref?(channel: SourceOut["channels"][number]): string | undefined;
}

/** A channel's newest value, from the store (re-rendering only this element) or from the props. */
function Latest({ channel, trace, live }: { channel: ChannelOut; trace: Trace | undefined; live: TraceRef | undefined }) {
  const point = useLatest(live ? channelKey(channel) : undefined);
  const last = live ? point?.v : trace && trace.v.length ? trace.v[trace.v.length - 1] : undefined;
  return <span className="fb-latest">{last === undefined ? "—" : `${last.toFixed(channel.precision ?? 2)} ${channel.unit}`}</span>;
}

function LastSample({ channel, trace, live }: { channel: ChannelOut | undefined; trace: Trace | undefined; live: TraceRef | undefined }) {
  const point = useLatest(live && channel ? channelKey(channel) : undefined);
  const lastT = live ? point?.t : trace && trace.t.length ? trace.t[trace.t.length - 1] : undefined;
  return lastT === undefined ? null : <> · last sample {new Date(lastT * 1000).toLocaleTimeString()}</>;
}

/** One source: a chart per channel, latest value in the heading. Pure; `useSamples` supplies the traces, or `useTraceRef` a `live` handle. */
export function SourcePanel({ source, traces, live, height, windowS, controls, yScale, every, exportHref }: SourcePanelProps) {
  const first = source.channels[0] && traces?.[channelKey(source.channels[0])];
  return (
    <article className="fb-panel fb-source">
      <header className="fb-source-head">
        <h3><Ref kind="source" name={source.name}>{source.label ?? source.name}</Ref></h3>
        <span className="fb-muted">
          {source.label && `${source.name} · `}
          {source.channels.length} channel{source.channels.length === 1 ? "" : "s"}
          <LastSample channel={source.channels[0]} trace={first} live={live} />
        </span>
        {controls && <span className="fb-source-controls">{controls}</span>}
      </header>
      {source.channels.map((c) => {
        const trace = traces?.[channelKey(c)];
        return (
          <section key={c.measurand} className="fb-channel">
            <h4>
              <Ref kind="channel" name={c.source} measurand={c.measurand}>{c.label}</Ref>
              <Latest channel={c} trace={trace} live={live} />
            </h4>
            <TimeSeries channel={c} source={live} {...(live ? {} : { t: trace?.t ?? [], v: trace?.v ?? [] })} height={height} windowS={windowS} yScale={yScale} every={every} exportHref={exportHref?.(c)} />
          </section>
        );
      })}
    </article>
  );
}
