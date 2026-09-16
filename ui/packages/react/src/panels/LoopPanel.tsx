import { useEffect, useRef, type ReactNode } from "react";
import uPlot from "uplot";
import type { ChannelOut, FeedforwardConfig, LoopOut } from "@flyball/client";
import { alarmLevel, describeStateKey, humanise, setpointOf } from "@flyball/client";
import type { LoopTrace } from "../hooks/useLoops.js";
import { Ref } from "../links.js";
import { thin } from "./thin.js";
import { yRange, type YScale } from "./yscale.js";

/** Same fallback order `MultiSeries` cycles through for a trace with no explicit colour. */
const SERIES_FALLBACK = ["#2a78d6", "#c2410c", "#15803d", "#7e22ce", "#b45309", "#0e7490"];

interface MiniTrace {
  t: number[];
  v: (number | null)[];
  color?: string;
  dash?: boolean;
  width?: number;
  precision?: number;
}

/**
 * A faceplate trend: a few traces on one legend-free, toolbar-free uPlot
 * instance (DESIGN-SPEC §3.4's "optional trends" are a glance at shape, not
 * a chart to read exact values off -- the rows above already carry the
 * numbers) -- but with a minimal axis pair (a user report found a totally
 * bare chart unreadable): 3-4 y ticks at the series' precision, 2-3 sparse
 * time labels, no axis title, no grid on x. Deliberately not `MultiSeries`:
 * that component's toolbar and full axes are the right call for a real
 * chart, but at this widget's ~140px height they would leave little plot
 * area. A small, purpose-built instance keeps most of the height for the line.
 */
/** Population standard deviation, ignoring nulls; 0 with fewer than two points. */
function stdDev(values: (number | null)[]): number {
  const xs = values.filter((v): v is number => v != null);
  if (xs.length < 2) return 0;
  const mean = xs.reduce((a, b) => a + b, 0) / xs.length;
  return Math.sqrt(xs.reduce((a, b) => a + (b - mean) ** 2, 0) / xs.length);
}

/**
 * A band around `center` (the setpoint) for the process mini trend under
 * "auto": uPlot's plain fit-to-data zooms into pure sensor noise once a loop
 * settles onto a flat setpoint, and a hold with a millikelvin of noise reads
 * as a storm. The floor is whichever is widest of the warn band, 2% of the
 * channel's declared range, or 5x the reading's own noise; widened further
 * to cover the reading itself, so a real excursion (an actuator at limit,
 * say) still shows instead of being clipped to the floor.
 */
function settledBand(center: number, warn: [number, number] | null | undefined, range: [number, number] | null | undefined, reading: (number | null)[]): [number, number] | null {
  const xs = reading.filter((v): v is number => v != null);
  const halves = [5 * stdDev(reading)];
  if (warn) halves.push(Math.max(Math.abs(center - warn[0]), Math.abs(warn[1] - center)));
  if (range) halves.push(0.02 * Math.abs(range[1] - range[0]));
  const half = Math.max(...halves);
  if (half <= 0 && !xs.length) return null;
  return [Math.min(center - half, ...xs), Math.max(center + half, ...xs)];
}

function MiniTrend({ series, height, every, yScale, range, windowS, settledBand: settled }: { series: MiniTrace[]; height: number; every?: number; yScale?: YScale; range?: [number, number] | null; windowS?: number; settledBand?: [number, number] | null }) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<uPlot | null>(null);
  const shape = JSON.stringify(series.map((s) => [s.color ?? null, s.dash ?? null, s.width ?? null]));
  // A caller-computed settled band stands in for a plain data-fit under "auto"; "range" and fixed bounds still win outright.
  const y = yScale && yScale !== "auto" ? yRange(yScale, range ?? null) : settled ? () => settled : undefined;
  const yKey = y ? y().join(":") : "auto";
  const precision = series[0]?.precision ?? 1;

  useEffect(() => {
    if (!host.current) return;
    const el = host.current;
    const palette = SERIES_FALLBACK.map((fallback, i) => getComputedStyle(el).getPropertyValue(`--fb-series-${i + 1}`).trim() || fallback);
    const axisColour = getComputedStyle(el).getPropertyValue("--fb-fg-3").trim() || "#888";
    const gridColour = getComputedStyle(el).getPropertyValue("--fb-grid").trim() || "rgba(128,128,128,0.15)";
    const options: uPlot.Options = {
      width: el.clientWidth || 200,
      height,
      series: [
        {},
        ...series.map((s, i) => ({
          stroke: s.color ?? palette[i % palette.length],
          width: s.width ?? 1.5,
          dash: s.dash ? [4, 3] : undefined,
          points: { show: false },
          spanGaps: true,
        })),
      ],
      scales: {
        // Scrolls once the window is full, the newest point staying at the right edge -- the same
        // "follow live" behaviour `TimeSeries`/`MultiSeries` get from `navigation.ts`'s `xRange`. A
        // mini trend has no pan/zoom (no toolbar, no drag), so there is no "held" state to honour:
        // it always tracks `windowS` seconds ending at the newest point it has.
        x: { time: true, range: windowS ? (_u, min, max) => (max - min < windowS ? [min, min + windowS] : [max - windowS, max]) : undefined },
        y: y ? { range: y } : {},
      },
      // A minimal axis pair, not a bare chart: 2-3 sparse time labels (no title), 3-4 y ticks at
      // the series' own precision -- a totally axis-free trend read as broken, not "at a glance".
      axes: [
        { show: true, stroke: axisColour, font: "11px system-ui", size: 18, gap: 2, space: 70, grid: { show: false }, ticks: { show: false } },
        { show: true, stroke: axisColour, font: "11px system-ui", size: 40, gap: 4, space: 34, grid: { stroke: gridColour, width: 1 }, ticks: { show: false }, values: (_u, vals) => vals.map((v) => v.toFixed(precision)) },
      ],
      legend: { show: false },
      cursor: { show: false },
      padding: [4, 4, 0, 0],
    };
    chart.current = new uPlot(options, [[], ...series.map(() => [])] as uPlot.AlignedData, el);
    const resize = new ResizeObserver(([entry]) => {
      const width = Math.round(entry?.contentRect.width ?? el.clientWidth);
      const u = chart.current;
      if (!u || width <= 0 || width === u.width) return;
      u.setSize({ width, height });
    });
    resize.observe(el);
    return () => {
      resize.disconnect();
      chart.current?.destroy();
      chart.current = null;
    };
    // `shape`/`yKey`/`precision` stand in for `series`/`y`: only their structure rebuilds the chart.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape, height, yKey, precision, windowS]);

  useEffect(() => {
    const t = series[0] ? thin(series[0].t, every) : [];
    chart.current?.setData([t, ...series.map((s) => thin(s.v, every))] as uPlot.AlignedData);
  }, [series, every]);

  return <div ref={host} className="fb-chart fb-loop-mini" />;
}

export interface LoopPanelProps {
  loop: LoopOut;
  /** The loop's recent ticks; `useLoops` supplies one per loop. Omit for a readout-only panel. */
  history?: LoopTrace;
  /**
   * Unit the drive chart and its readouts are in: `loop.demand_unit` (the
   * actuator's, or the channel's when it has none) unless overridden.
   */
  demandUnit?: string | null;
  /** The actuator's achievable demand range, in `demandUnit` (`DeviceState.output_range`); draws the OP bar as a % of it when known. */
  outputRange?: [number, number] | null;
  /** Seconds of history the chart shows; it scrolls once full. */
  windowS?: number;
  /** Rendered inline in the SP row: the setpoint entry and Move/Regulate button. */
  controls?: ReactNode;
  /** Rendered in the header, after the setpoint controls in tab order even though it sits at the top: Stop (and Remove). */
  headerControls?: ReactNode;
  /** y axis scaling for the process trend. */
  yScale?: YScale;
  /** Draw one point in `every`. */
  every?: number;
  /** The loop's ticks in the store, as an export URL; both trends' download menus offer it. */
  exportHref?: string;
  /** Process and Drive mini trends, stacked at the right. Off shows the three rows alone. */
  trends?: boolean;
  /** Each trend's height in px; default 150 (the Controllers page's own room). A dashboard tile computes and passes what it actually has. */
  trendHeight?: number;
  /** Law and feedforward state: a compact, always-open section (not a `<details>` any more -- a user report found the disclosure hid content people needed every time). Only the Controllers page opens this; a dashboard widget never does. */
  detail?: boolean;
  /** The reader behind this channel has gone quiet: shown in the banner ahead of "actuator at limit" / "open loop". */
  readerOffline?: boolean;
  /**
   * No outer card and no header (name, channel, mode badge): for a caller that already draws its
   * own frame -- `WidgetFrame`'s tile, fed the title/subtitle/mode badge through `useWidgetChrome`.
   * Default false: the full card the Controllers page shows.
   */
  bare?: boolean;
  /** Extra content in the card, below the law/feedforward details -- the merged Controllers page's collapsible actuator state/config/settings/commands. Ignored when `bare`. */
  extra?: ReactNode;
}

const EMPTY: LoopTrace = { t: [], reference: [], reading: [], demand: [], expected: [], correction: [] };
/** Default trend height on the Controllers page, which has room; a dashboard tile passes its own (`trendHeight`), computed from what it actually has. */
const TREND_HEIGHT = 150;

/** True when the actuator's achievable differs from what was asked, beyond float noise. */
const differs = (demand: number | null, expected: number) => demand == null || Math.abs(expected - demand) > 1e-9 * Math.max(1, Math.abs(demand));

/** The trace column's last value: the current one, or null when the latest tick had none (never an older one, which would be stale). */
const latest = (column: (number | null)[]): number | null => (column.length ? column[column.length - 1]! : null);

/** A value's fraction of `range`, clamped to [0, 1]; null with no value or no range. */
const fractionOf = (value: number | null, range: [number, number] | null | undefined): number | null =>
  value == null || !range ? null : Math.min(1, Math.max(0, (value - range[0]) / (range[1] - range[0])));

/** Warn/alarm band edges that fall inside `range`, as left-offset percentages for ticks on a bar (mirrors `Readout`). */
function bandTicks(channel: ChannelOut, range: [number, number]): Array<{ band: "warn" | "alarm"; left: number }> {
  return (["warn", "alarm"] as const).flatMap((band) =>
    (channel[band] ?? [])
      .filter((edge) => edge > range[0] && edge < range[1])
      .map((edge) => ({ band, left: ((edge - range[0]) / (range[1] - range[0])) * 100 })),
  );
}

/** `value` to at most 4 significant figures, an integer as itself -- for a one-line summary, not a table. */
const shortNumber = (value: number) => (Number.isInteger(value) ? value : Number(value.toPrecision(4)));

/** Every plain field of `value` as `{key, text, title}` for a one-line, hover-hinted summary (law/feedforward state): the full name from `describeStateKey`, its unit from `units` when given, and the hint (falling back to the label) as the hover text. */
function fieldsLine(value: Record<string, unknown>, units: Record<string, string> = {}): Array<{ key: string; text: string; title: string }> {
  return Object.entries(value)
    .filter(([, v]) => v !== null && v !== undefined && typeof v !== "object")
    .map(([k, v]) => {
      const described = describeStateKey(k);
      const unit = units[k];
      return { key: k, text: `${described.label} ${typeof v === "number" ? shortNumber(v) : String(v)}${unit ? ` ${unit}` : ""}`, title: described.hint ?? described.label };
    });
}

/** A feedforward's arguments as the one-line law summary shows them: a table as a compact `n points, x→y … x→y` line. */
function feedforwardArgs(feedforward: FeedforwardConfig | null | undefined): unknown {
  if (!feedforward) return {};
  const { tag, ...args } = feedforward;
  if (tag === "table") {
    const points = (args as { points?: Array<[number, number]> }).points ?? [];
    const pair = (p: [number, number]) => `${p[0]} → ${p[1]}`;
    const shown = points.length <= 4 ? points.map(pair).join(", ") : `${points.slice(0, 2).map(pair).join(", ")} … ${points.slice(-2).map(pair).join(", ")}`;
    return { points: `${points.length} point${points.length === 1 ? "" : "s"}${shown ? `, ${shown}` : ""}` };
  }
  if (tag === "setpoint") return "demand = setpoint; the law corrects about it";
  if (tag === "none") return "0; the law does all the work";
  return args;
}

/**
 * One control loop as a process-control faceplate: mode badge and a banner
 * for the top condition in the header, then three aligned rows -- PV (what
 * it reads), SP (where it aims, with the entry and Move controls), OP (what
 * it drives the actuator with, after limits) -- each with a bar, and the
 * Process/Drive trends stacked beside them. Pure; `useLoops` supplies the
 * data and the caller supplies the controls.
 *
 * The trends are drawn apart from the OP bar on purpose: a reading and a
 * demand are different things even when the rig gives them the same unit (a
 * temperature the heater is told to hold is not the temperature measured),
 * and an actuator with no unit of its own has a demand in the channel's
 * unit by convention.
 */
export function LoopPanel({
  loop,
  history = EMPTY,
  demandUnit,
  outputRange,
  windowS,
  controls,
  headerControls,
  yScale,
  every,
  exportHref,
  trends = true,
  trendHeight = TREND_HEIGHT,
  detail = false,
  readerOffline = false,
  bare = false,
  extra,
}: LoopPanelProps) {
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
      color: "var(--fb-series-setpoint)",
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
            color: "var(--fb-series-setpoint)",
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
  const law = loop.law as Record<string, unknown> | null;
  const { tag, ...rest } = law ?? {};
  const following = typeof loop.reference === "string" ? humanise(loop.reference) : null;
  // A ramp names its generator; the setpoint is then recovered through the feedforward, or failing that read off the
  // latest tick -- only when that tick carries one (a stored tick does; a live one under a `none` feedforward does not).
  const setpoint = setpointOf(loop) ?? (following ? latest(history.reference) : null);
  const reading = loop.reading?.value ?? null;
  // The actuator clamped: it will give `expected`, not what was asked.
  const clamped = loop.expected != null && differs(loop.demand, loop.expected);
  const output = loop.expected ?? loop.demand;
  const deviation = reading != null && setpoint != null ? reading - setpoint : null;
  const deviationWarn = alarmLevel(reading, channel) !== "ok";

  const range = channel.range;
  const pvFraction = fractionOf(reading, range);
  const spFraction = fractionOf(setpoint, range);
  const pvTicks = range ? bandTicks(channel, range) : [];
  const opFraction = fractionOf(output, outputRange);
  // Which rail the output is pinned to, for the OP bar's highlighted end when clamped.
  const limitEdge: "hi" | "lo" | null = clamped ? ((loop.demand ?? 0) > loop.expected! ? "hi" : "lo") : null;

  // `loop.mode === "open"` is a distinct wire state the backend does not currently emit; the
  // real "no feedback" condition an operator meets is the `open_loop` law tag -- present and
  // "regulating" (it is still driving the actuator), just with nothing correcting for error.
  const banner = readerOffline
    ? { text: "reader offline", hint: "No reading has arrived for this channel recently." }
    : clamped
      ? { text: "actuator at limit", hint: `${loop.name} cannot give the full demand; it is clamped to what it can achieve.` }
      : loop.mode === "open" || tag === "open_loop"
        ? { text: "open loop", hint: "Following the setpoint with no law correcting for error." }
        : null;

  const frame = (
    <div className={`fb-loop-frame${trends ? "" : " fb-loop-no-trends"}`}>
      {!bare && (
        <header className="fb-loop-head">
          <h3><Ref kind="loop" name={loop.name}>{loop.label ?? loop.name}</Ref></h3>
          <span className="fb-muted">
            {loop.label && `${loop.name} · `}
            <Ref kind="channel" name={channel.source} measurand={channel.measurand} />
            {loop.default && " · default"}
          </span>
          <span className={`fb-badge fb-mode fb-mode-${loop.mode}`}>{loop.mode}</span>
        </header>
      )}
      {banner && (
        <div className="fb-loop-banner" title={banner.hint}>
          ⚠ {banner.text}
        </div>
      )}
      <dl className="fb-loop-rows">
        <div className="fb-loop-row">
          <dt title="process value — PV">Reading</dt>
          <dd>{fmt(reading, unit)}</dd>
          {pvFraction !== null && (
            <div className="fb-range" title={`${range![0]} – ${range![1]} ${unit}`}>
              <div className="fb-range-fill" style={{ width: `${pvFraction * 100}%` }} />
              {pvTicks.map((t) => (
                <div key={`${t.band}${t.left}`} className={`fb-range-tick fb-range-tick-${t.band}`} style={{ left: `${t.left}%` }} />
              ))}
              {spFraction !== null && <div className="fb-loop-sp-notch" style={{ left: `${spFraction * 100}%` }} title={`setpoint ${fmt(setpoint, unit)}`} />}
            </div>
          )}
          <span className="fb-loop-caption" style={deviation != null ? { color: deviationWarn ? "var(--fb-warn)" : "var(--fb-fg-2)" } : undefined}>
            {deviation != null ? `${Math.abs(deviation).toFixed(precision)} ${unit} ${deviation >= 0 ? "above" : "below"} target` : " "}
          </span>
        </div>
        <div className="fb-loop-row">
          <dt title="setpoint — SP">Target</dt>
          <dd>{fmt(setpoint, unit)}</dd>
          {controls && <span className="fb-loop-sp-controls">{controls}</span>}
          <span className="fb-loop-caption">{following ? `→ following ${following.toLowerCase()}` : " "}</span>
        </div>
        <div className="fb-loop-row">
          <dt title="drive after limits — OP">Output</dt>
          <dd>{fmt(output, dUnit)}</dd>
          {opFraction !== null && (
            <div
              className={`fb-range${clamped ? ` fb-range-limit-${limitEdge}` : ""}`}
              title={clamped ? `output at its ${limitEdge === "hi" ? "upper" : "lower"} limit; requested ${fmt(loop.demand, dUnit)}` : `${outputRange![0]} – ${outputRange![1]} ${dUnit}`}
            >
              <div className="fb-range-fill" style={{ width: `${opFraction * 100}%` }} />
            </div>
          )}
          <span className="fb-loop-caption">{clamped ? `requested ${fmt(loop.demand, dUnit)}` : " "}</span>
        </div>
      </dl>
      {headerControls && <div className="fb-loop-stop">{headerControls}</div>}
      {trends && (
        <div className="fb-loop-trends">
          <div>
            <h4 className="fb-loop-chart-title" title="What the loop measures against where it is aiming">
              Process <span className="fb-muted">{unit}</span>
            </h4>
            <MiniTrend series={process} height={trendHeight} every={every} yScale={yScale} range={channel.range} windowS={windowS} settledBand={setpoint != null ? settledBand(setpoint, channel.warn, channel.range, history.reading) : null} />
          </div>
          <div>
            <h4 className="fb-loop-chart-title" title={`What the loop asks of ${loop.name}, and what it can give back`}>
              Drive <span className="fb-muted">{loop.name} · {dUnit}</span>
            </h4>
            {/* The port limits, when known: "at limit" then reads as the line sitting on the rail, not a mystery flat spot. */}
            <MiniTrend series={drive} height={trendHeight} every={every} yScale={outputRange ? "range" : undefined} range={outputRange} windowS={windowS} />
          </div>
        </div>
      )}
      {/* Always open, one line each -- no `<details>`: a user report found the disclosure hid
          content people needed every time. Each abbreviated field carries its full name as a
          hover hint (`describeStateKey`) rather than spelling it out and crowding the line. */}
      {detail && (
        <section className="fb-loop-law">
          {law ? (
            <p className="fb-loop-law-line">
              <span className="fb-tag">{typeof tag === "string" ? tag : "?"}</span>
              {fieldsLine(rest, { tt: "s", last_raw: dUnit, last_elapsed: "s" }).map((f) => (
                <span key={f.key} title={f.title}>
                  {" "}
                  · {f.text}
                </span>
              ))}
            </p>
          ) : (
            <p className="fb-loop-law-line fb-muted" title="No law is fitted; the actuator is driven by demand alone.">
              manual · no law
            </p>
          )}
          {ffTag && (
            <p className="fb-loop-law-line fb-loop-feedforward" title="What the loop asks for before the law corrects: the setpoint mapped into the actuator's unit">
              <span className="fb-tag">{ffTag}</span>
              <span> · {unit === dUnit ? dUnit : `${unit} → ${dUnit}`}</span>
              {(() => {
                const args = feedforwardArgs(feedforward);
                return typeof args === "string"
                  ? ` · ${args}`
                  : fieldsLine(args as Record<string, unknown>).map((f) => (
                      <span key={f.key} title={f.title}>
                        {" "}
                        · {f.text}
                      </span>
                    ));
              })()}
            </p>
          )}
        </section>
      )}
      {extra && !bare && <div className="fb-loop-extra">{extra}</div>}
    </div>
  );
  // `.fb-loop` (container-query root for `.fb-loop-frame`'s responsive collapse) stays even when
  // `bare` drops the card look (`.fb-panel`) -- an element cannot query its own size, so this is
  // still needed one level above the grid that reacts to it.
  return <article className={`fb-loop${bare ? " fb-loop-bare" : " fb-panel"}`}>{frame}</article>;
}
