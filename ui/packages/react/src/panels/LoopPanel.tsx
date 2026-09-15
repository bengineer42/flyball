import type { ReactNode } from "react";
import type { LoopOut } from "@flyball/client";
import type { LoopTrace } from "../hooks/useLoops.js";
import { Ref } from "../links.js";
import { MultiSeries } from "./MultiSeries.js";
import { ValueView } from "./ValueView.js";

export interface LoopPanelProps {
  loop: LoopOut;
  /** The loop's recent ticks; `useLoops` supplies one per loop. Omit for a readout-only panel. */
  history?: LoopTrace;
  /**
   * Unit of the actuator's demand (`ActuatorSchema.demand_unit`). When it
   * differs from the channel's unit, demand and expected are drawn against
   * a second y axis. Omit, or pass the channel's unit, for one axis.
   */
  demandUnit?: string | null;
  height?: number;
  /** Seconds of history the chart shows; it scrolls once full. */
  windowS?: number;
  /** Rendered at the end of the header: a window selector, for instance. */
  controls?: ReactNode;
}

const EMPTY: LoopTrace = { t: [], reference: [], reading: [], demand: [], expected: [] };

/**
 * One control loop: name, channel and mode in the header; reference,
 * reading, demand and expected on one chart; the current values as a row
 * of readouts; the law's gains and state as key/values. Pure; `useLoops`
 * supplies the data.
 */
export function LoopPanel({ loop, history = EMPTY, demandUnit, height, windowS, controls }: LoopPanelProps) {
  const { channel } = loop;
  const unit = channel.unit;
  const dUnit = demandUnit ?? unit;
  const precision = channel.precision ?? 2;
  const fmt = (value: number | null | undefined, u: string) =>
    value == null ? "—" : `${value.toFixed(precision)} ${u}`;
  const series = [
    { label: "reference", unit, t: history.t, v: history.reference, dash: true, precision },
    { label: "reading", unit, t: history.t, v: history.reading, precision },
    { label: "demand", unit: dUnit, t: history.t, v: history.demand, precision },
    { label: "expected", unit: dUnit, t: history.t, v: history.expected, dash: true, precision },
  ];
  const law = loop.law as Record<string, unknown>;
  const { tag, ...rest } = law;
  const readouts: Array<[string, string]> = [
    ["reference", fmt(loop.reference, unit)],
    ["reading", fmt(loop.reading?.value, unit)],
    ["correction", fmt(loop.correction, unit)],
    ["demand", fmt(loop.demand, dUnit)],
    ["expected", fmt(loop.expected, dUnit)],
    ["mode", loop.mode],
  ];
  return (
    <article className="fb-panel fb-loop">
      <header className="fb-loop-head">
        <h3><Ref kind="loop" name={loop.name} /></h3>
        <span className="fb-muted">
          <Ref kind="channel" name={channel.source} measurand={channel.measurand} />
          {loop.default && " · default"}
        </span>
        <span className={`fb-badge fb-mode fb-mode-${loop.mode}`}>{loop.mode}</span>
        {controls && <span className="fb-loop-controls">{controls}</span>}
      </header>
      <MultiSeries series={series} unit={unit} height={height} windowS={windowS} />
      <dl className="fb-loop-readouts">
        {readouts.map(([label, value]) => (
          <div key={label} className="fb-loop-readout">
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <section className="fb-loop-law">
        <h4>
          law <span className="fb-tag">{typeof tag === "string" ? tag : "?"}</span>
        </h4>
        <ValueView value={rest} />
      </section>
    </article>
  );
}
