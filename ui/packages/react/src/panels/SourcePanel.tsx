import type { SourceOut } from "@flyball/client";
import { channelKey, type Traces } from "../hooks/useSources.js";
import { TimeSeries } from "./TimeSeries.js";
import type { YScale } from "./yscale.js";
import { Ref } from "../links.js";

export interface SourcePanelProps {
  source: SourceOut;
  traces: Traces;
  height?: number;
  /** Seconds of history each chart shows; charts scroll once it is full. */
  windowS?: number;
  /** Rendered at the end of the header: a window selector, for instance. */
  controls?: React.ReactNode;
  /** y axis scaling for every chart in the panel. */
  yScale?: YScale;
  /** Draw one point in `every`. */
  every?: number;
}

/** One source: a chart per channel, latest value in the heading. Pure; `useSamples` supplies the traces. */
export function SourcePanel({ source, traces, height, windowS, controls, yScale, every }: SourcePanelProps) {
  const first = source.channels[0] && traces[channelKey(source.channels[0])];
  const lastT = first && first.t.length ? first.t[first.t.length - 1]! : undefined;
  return (
    <article className="fb-panel fb-source">
      <header className="fb-source-head">
        <h3><Ref kind="source" name={source.name} /></h3>
        <span className="fb-muted">
          {source.channels.length} channel{source.channels.length === 1 ? "" : "s"}
          {lastT !== undefined && ` · last sample ${new Date(lastT * 1000).toLocaleTimeString()}`}
        </span>
        {controls && <span className="fb-source-controls">{controls}</span>}
      </header>
      {source.channels.map((c) => {
        const trace = traces[channelKey(c)];
        const last = trace && trace.v.length ? trace.v[trace.v.length - 1] : undefined;
        return (
          <section key={c.measurand} className="fb-channel">
            <h4>
              <Ref kind="channel" name={c.source} measurand={c.measurand}>{c.label}</Ref>
              <span className="fb-latest">
                {last === undefined ? "—" : `${last.toFixed(c.precision ?? 2)} ${c.unit}`}
              </span>
            </h4>
            <TimeSeries channel={c} t={trace?.t ?? []} v={trace?.v ?? []} height={height} windowS={windowS} yScale={yScale} every={every} />
          </section>
        );
      })}
    </article>
  );
}
