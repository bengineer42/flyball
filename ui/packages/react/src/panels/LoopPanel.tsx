import type { ReactNode } from "react";
import type { FeedforwardConfig, LoopOut } from "@flyball/client";
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
   * Unit the drive chart and its readouts are in: `loop.demand_unit` (the
   * actuator's, or the channel's when it has none) unless overridden.
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
  /** The loop's ticks in the store, as an export URL; both charts' download menus offer it. */
  exportHref?: string;
  /** A faceplate for a small tile: the process chart and the readouts in a row; no drive chart, no law section. */
  compact?: boolean;
}

const EMPTY: LoopTrace = { t: [], reference: [], reading: [], demand: [], expected: [], correction: [] };

/** True when the actuator's achievable differs from what was asked, beyond float noise. */
const differs = (demand: number | null, expected: number) => demand == null || Math.abs(expected - demand) > 1e-9 * Math.max(1, Math.abs(demand));

/** The trace column's last value: the current one, or null when the latest tick had none (never an older one, which would be stale). */
const latest = (column: (number | null)[]): number | null => (column.length ? column[column.length - 1]! : null);

/** A feedforward's arguments as `ValueView` shows them: a table as a compact `n points, x→y … x→y` line. */
function feedforwardArgs(feedforward: FeedforwardConfig | null | undefined): unknown {
  if (!feedforward) return {};
  const { tag, ...args } = feedforward;
  if (tag === "table") {
    const points = (args as { points?: Array<[number, number]> }).points ?? [];
    const pair = (p: [number, number]) => `${p[0]}→${p[1]}`;
    const shown = points.length <= 4 ? points.map(pair).join(", ") : `${points.slice(0, 2).map(pair).join(", ")} … ${points.slice(-2).map(pair).join(", ")}`;
    return { points: `${points.length} point${points.length === 1 ? "" : "s"}${shown ? `, ${shown}` : ""}` };
  }
  if (tag === "setpoint") return "demand = setpoint; the law corrects about it";
  if (tag === "none") return "0; the law does all the work";
  return args;
}

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
export function LoopPanel({ loop, history = EMPTY, demandUnit, height, windowS, controls, yScale, every, exportHref, compact = false }: LoopPanelProps) {
  const { channel } = loop;
  const unit = channel.unit;
  const dUnit = demandUnit ?? loop.demand_unit ?? unit;
  const precision = channel.precision ?? 2;
  const fmt = (value: number | string | null | undefined, u: string) =>
    value == null ? "—" : typeof value === "number" ? `${value.toFixed(precision)} ${u}` : value;
  const process = [
    {
      label: "reading",
      hint: `What ${channel.source}.${channel.measurand} measures: the value the loop is trying to control.`,
      unit,
      t: history.t,
      v: history.reading,
      precision,
    },
    {
      label: "setpoint",
      hint: "Where the loop is aiming right now; a ramp moves it over time.",
      unit,
      t: history.t,
      v: history.reference,
      dash: true,
      precision,
    },
  ];
  const feedforward = loop.feedforward as FeedforwardConfig | undefined;
  const ffTag = typeof feedforward?.tag === "string" ? feedforward.tag : null;
  // Under no feedforward the correction *is* the demand; drawing it twice says nothing.
  const showCorrection = ffTag !== "none";
  const clampedTrace = history.expected.map((e, i) => (e != null && differs(history.demand[i] ?? null, e) ? e : null));
  const drive = [
    {
      label: "demand",
      hint: showCorrection ? `What the loop asks of ${loop.name}: feedforward(setpoint) plus the law's correction.` : `What the loop asks of ${loop.name}: the law's correction alone (no feedforward).`,
      unit: dUnit,
      t: history.t,
      v: history.demand,
      precision,
    },
    // Achievable is drawn only where it differs from the demand (the actuator clamped); elsewhere the two coincide.
    ...(clampedTrace.some((v) => v != null)
      ? [
          {
            label: "achievable (clamped)",
            hint: `What ${loop.name} could actually give while the demand was beyond its limit.`,
            unit: dUnit,
            t: history.t,
            v: clampedTrace,
            dash: true,
            precision,
          },
        ]
      : []),
    ...(showCorrection
      ? [
          {
            label: "correction",
            hint: "The law's share of the demand: what it adds to feedforward(setpoint) to close the error.",
            unit: dUnit,
            t: history.t,
            v: history.correction,
            width: 1,
            precision,
          },
        ]
      : []),
  ];
  const law = loop.law as Record<string, unknown>;
  const { tag, ...rest } = law;
  const following = typeof loop.reference === "string" ? humanise(loop.reference) : null;
  // A ramp names its generator; the setpoint is then recovered through the feedforward, or failing that read off the
  // latest tick -- only when that tick carries one (a stored tick does; a live one under a `none` feedforward does not).
  const setpoint = setpointOf(loop) ?? (following ? latest(history.reference) : null);
  // The actuator clamped: it will give `expected`, not what was asked.
  const clamped = loop.expected != null && differs(loop.demand, loop.expected);
  const readouts: Array<{ label: string; value: string; hint: string; sub?: string; alarm?: boolean }> = [
    {
      label: "setpoint",
      value: fmt(setpoint, unit),
      hint: following ? `Where the loop is aiming; following a ${following.toLowerCase()}.` : "Where the loop is aiming.",
    },
    { label: "reading", value: fmt(loop.reading?.value, unit), hint: `The last value read on ${channel.source}.${channel.measurand}.` },
    {
      label: "demand",
      value: fmt(loop.demand, dUnit),
      hint: clamped
        ? `Asked ${fmt(loop.demand, dUnit)}, ${loop.name} can give ${fmt(loop.expected, dUnit)}: it is at a limit.`
        : `What the loop asked of ${loop.name}: feedforward(setpoint) plus correction; ${loop.name} can give all of it.`,
      ...(clamped ? { sub: `achievable ${fmt(loop.expected, dUnit)}`, alarm: true } : {}),
    },
    ...(showCorrection ? [{ label: "correction", value: fmt(loop.correction, dUnit), hint: "The law's share of the demand, in the actuator's unit." }] : []),
    { label: "mode", value: loop.mode, hint: "manual: holds its last demand · open: follows the setpoint without a law · regulating: the law drives" },
  ];
  return (
    <article className={`fb-panel fb-loop${compact ? " fb-loop-compact" : ""}`}>
      <header className="fb-loop-head">
        <h3><Ref kind="loop" name={loop.name}>{loop.label ?? loop.name}</Ref></h3>
        <span className="fb-muted">
          {loop.label && `${loop.name} · `}
          <Ref kind="channel" name={channel.source} measurand={channel.measurand} />
          {loop.default && " · default"}
        </span>
        <span className={`fb-badge fb-mode fb-mode-${loop.mode}`}>{loop.mode}</span>
        {controls && <span className="fb-loop-controls">{controls}</span>}
      </header>
      <div className="fb-loop-charts">
        <div>
          <h4 className="fb-loop-chart-title" title="What the loop measures against where it is aiming">
            Process <span className="fb-muted">{unit}</span>
          </h4>
          <MultiSeries series={process} unit={unit} title={`${loop.name} process`} height={height} windowS={windowS} yScale={yScale} range={channel.range} every={every} exportHref={exportHref} />
        </div>
        {!compact && (
          <div>
            <h4 className="fb-loop-chart-title" title={`What the loop asks of ${loop.name}, and what it can give back`}>
              Drive <span className="fb-muted">{loop.name} · {dUnit}</span>
            </h4>
            <MultiSeries series={drive} unit={dUnit} title={`${loop.name} drive`} height={height} windowS={windowS} every={every} exportHref={exportHref} />
          </div>
        )}
      </div>
      <dl className="fb-loop-readouts">
        {readouts.map(({ label, value, hint, sub, alarm }) => (
          <div key={label} className={`fb-loop-readout${alarm ? " fb-clamped" : ""}`} title={hint}>
            <dt>
              {label}
              {label === "setpoint" && following && <span className="fb-muted"> · {following.toLowerCase()}</span>}
            </dt>
            <dd>
              {value}
              {/* Always in the flow, so a tile does not grow when the actuator hits a limit. */}
              <span className="fb-loop-sub">{sub ?? "\u00a0"}</span>
            </dd>
          </div>
        ))}
      </dl>
      {!compact && <div className="fb-loop-law">
        <section>
          <h4>
            law <span className="fb-tag">{typeof tag === "string" ? tag : "?"}</span>
          </h4>
          <ValueView value={rest} />
        </section>
        {ffTag && (
          <section className="fb-loop-feedforward" title="What the loop asks for before the law corrects: the setpoint mapped into the actuator's unit">
            <h4>
              feedforward <span className="fb-tag">{ffTag}</span>
              <span className="fb-muted">{unit === dUnit ? dUnit : `${unit} → ${dUnit}`}</span>
            </h4>
            <ValueView value={feedforwardArgs(feedforward)} />
          </section>
        )}
      </div>}
    </article>
  );
}
