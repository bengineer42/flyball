import type { ReactNode } from "react";
import type { LoopOut } from "@flyball/client";
import { humanise, setpointOf } from "@flyball/client";
import type { LoopTrace } from "../hooks/useLoops.js";
import { Ref } from "../links.js";
import { MultiSeries } from "./MultiSeries.js";
import type { YScale } from "./yscale.js";
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
  /** y axis scaling. */
  yScale?: YScale;
  /** Draw one point in `every`. */
  every?: number;
}

const EMPTY: LoopTrace = { t: [], reference: [], reading: [], demand: [], expected: [] };

/**
 * One control loop: name, channel and mode in the header; two charts -- the
 * *process* (what the loop reads against what it aims at) and the *drive*
 * (what it asks the actuator for, what the actuator says it can give, and
 * the law's correction, all in the actuator's demand unit); the current
 * values as readouts; the law's gains and state as key/values. Pure;
 * `useLoops` supplies the data.
 *
 * The two are drawn apart on purpose: a reading and a demand are different
 * things even when the rig gives them the same unit (a temperature the
 * heater is told to hold is not the temperature measured), and an actuator
 * with no unit of its own has a demand in the channel's unit by convention.
 */
export function LoopPanel({ loop, history = EMPTY, demandUnit, height, windowS, controls, yScale, every }: LoopPanelProps) {
  const { channel } = loop;
  const unit = channel.unit;
  const dUnit = demandUnit ?? unit;
  const precision = channel.precision ?? 2;
  const fmt = (value: number | string | null | undefined, u: string) =>
    value == null ? "—" : typeof value === "number" ? `${value.toFixed(precision)} ${u}` : value;
  const process = [
    { label: `reading (${channel.source}.${channel.measurand})`, unit, t: history.t, v: history.reading, precision },
    { label: "reference (where it aims)", unit, t: history.t, v: history.reference, dash: true, precision },
  ];
  const correction = history.demand.map((d, i) => {
    const r = history.reference[i];
    return d == null || r == null ? null : d - r;
  });
  const drive = [
    { label: `demand (asked of ${loop.name})`, unit: dUnit, t: history.t, v: history.demand, precision },
    { label: "expected (what it can give)", unit: dUnit, t: history.t, v: history.expected, dash: true, precision },
    { label: "correction (law's share of the demand)", unit: dUnit, t: history.t, v: correction, width: 1, precision },
  ];
  const law = loop.law as Record<string, unknown>;
  const { tag, ...rest } = law;
  const readouts: Array<[string, string]> = [
    [
      "reference",
      typeof loop.reference === "string"
        ? `${fmt(setpointOf(loop), unit)} (${humanise(loop.reference)})`
        : fmt(loop.reference, unit),
    ],
    ["reading", fmt(loop.reading?.value, unit)],
    ["correction", fmt(loop.correction, dUnit)], // the law adds to the demand, so it is in the demand's unit
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
      <div className="fb-loop-charts">
        <div>
          <h4 className="fb-loop-chart-title">
            Process <span className="fb-muted">reading vs reference · {unit}</span>
          </h4>
          <MultiSeries series={process} unit={unit} height={height} windowS={windowS} yScale={yScale} range={channel.range} every={every} />
        </div>
        <div>
          <h4 className="fb-loop-chart-title">
            Drive <span className="fb-muted">what the loop asks of {loop.name} · {dUnit}</span>
          </h4>
          <MultiSeries series={drive} unit={dUnit} height={height ? Math.round(height * 0.75) : 140} windowS={windowS} every={every} />
        </div>
      </div>
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
