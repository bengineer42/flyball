import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import uPlot from "uplot";
import type { Condition, ControllerOut, FeedforwardConfig, GeneratorOut, SignalOut } from "@flyball/client";
import { alarmLevel, describeController, describeQuality, describeStateKey, deviceOf, humanise, setpointOf, describeSignal, fixed, withUnit } from "@flyball/client";
import type { ControllerTrace } from "../hooks/useControllers.js";
import { useQuery } from "../hooks/useQuery.js";
import { Ref } from "../links.js";
import { useRig } from "../provider.js";
import { useBandLevel, useConditions, useDeviceRun, useNowS, useReading, useWriteState } from "../store/hooks.js";
import { QualityBadge, noValue } from "./quality.js";
import { thin } from "./thin.js";
import { MultiSeries, toBreaks, type MultiSeriesTrace } from "./MultiSeries.js";
import { axisValues, yRange, type YScale } from "./yscale.js";

/** Same fallback order `MultiSeries` cycles through for a trace with no explicit colour. */
const SERIES_FALLBACK = ["#2a78d6", "#c2410c", "#15803d", "#7e22ce", "#b45309", "#0e7490"];

interface MiniTrace {
  t: number[];
  /** `null` a break (a reading with none); `undefined` joined across (a re-apply that took no reading). */
  v: (number | null | undefined)[];
  color?: string;
  dash?: boolean;
  /** Draws as a step (holds the previous value until the next point, then jumps) rather than
   * interpolating a line between points: for a value that only actually changes at a tick --
   * a written setpoint, a fixed clamp limit -- not a `dash` style choice. */
  stepped?: boolean;
  width?: number;
  precision?: number;
  /** This trace's name and what it means; carried through to the expanded full chart's legend/hover. Not shown on the mini trend itself. */
  label?: string;
  hint?: string;
}

/**
 * A faceplate trend: a few traces on one legend-free, toolbar-free uPlot
 * instance (DESIGN-SPEC §3.4's "optional trends" are a glance at shape, not
 * a chart to read exact values off -- the rows above already carry the
 * numbers) -- but with a minimal axis pair (a user report found a totally
 * bare chart unreadable): 3-4 y ticks at the series' precision, 2-3 sparse
 * time labels, no axis title, no grid on x. Deliberately not `MultiSeries`
 * while collapsed: that component's toolbar and full axes would leave little
 * plot area at this widget's ~140px height. A click opens it as a real
 * `MultiSeries` in the same full-screen overlay every other chart uses (a
 * trend is a sparkline in this sense too) -- reusing that component rather
 * than building a second overlay/expand mechanism for this one.
 */
/** Population standard deviation, ignoring nulls; 0 with fewer than two points. */
function stdDev(values: (number | null | undefined)[]): number {
  const xs = values.filter((v): v is number => v != null);
  if (xs.length < 2) return 0;
  const mean = xs.reduce((a, b) => a + b, 0) / xs.length;
  return Math.sqrt(xs.reduce((a, b) => a + (b - mean) ** 2, 0) / xs.length);
}

/**
 * A band around `center` (the setpoint) for the process mini trend under
 * "auto": uPlot's plain fit-to-data zooms into pure sensor noise once a controller
 * settles onto a flat setpoint, and a hold with a millikelvin of noise reads
 * as a storm. The floor is whichever is widest of the warning band, 2% of the
 * signal's declared range, or 5x the reading's own noise; widened further
 * to cover the reading itself, so a real excursion (an output at limit,
 * say) still shows instead of being clipped to the floor.
 */
function settledBand(center: number, warning: [number, number] | null | undefined, range: [number, number] | null | undefined, reading: (number | null | undefined)[]): [number, number] | null {
  const xs = reading.filter((v): v is number => v != null);
  const halves = [5 * stdDev(reading)];
  if (warning) halves.push(Math.max(Math.abs(center - warning[0]), Math.abs(warning[1] - center)));
  if (range) halves.push(0.02 * Math.abs(range[1] - range[0]));
  const half = Math.max(...halves);
  if (half <= 0 && !xs.length) return null;
  return [Math.min(center - half, ...xs), Math.max(center + half, ...xs)];
}

interface MiniTrendProps {
  series: MiniTrace[];
  height: number;
  every?: number;
  yScale?: YScale;
  range?: [number, number] | null;
  windowS?: number;
  settledBand?: [number, number] | null;
  /** Heading of the full-size view a click opens; default lists the traces. */
  title?: string;
  /** Unit of the primary axis, for the full-size view. */
  unit?: string;
  /** The same ticks in the store, as an export URL; the full-size view's download menu offers it. */
  exportHref?: string;
}

/** A `MiniTrace`, opened full-size: the same data as a `MultiSeriesTrace` (the export `MiniTrend` uses when clicked). */
const asMultiSeriesTrace = (s: MiniTrace, i: number, unit: string | undefined): MultiSeriesTrace => ({
  label: s.label ?? `trace ${i + 1}`,
  hint: s.hint,
  unit,
  t: s.t,
  v: s.v,
  color: s.color,
  dash: s.dash,
  stepped: s.stepped,
  width: s.width,
  precision: s.precision,
});

function MiniTrend({ series, height, every, yScale, range, windowS, settledBand: settled, title, unit, exportHref }: MiniTrendProps) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<uPlot | null>(null);
  const shape = JSON.stringify(series.map((s) => [s.color ?? null, s.dash ?? null, s.stepped ?? null, s.width ?? null]));
  // A caller-computed settled band stands in for a plain data-fit under "auto"; "range" and fixed bounds still win outright.
  const y = yScale && yScale !== "auto" ? yRange(yScale, range ?? null) : settled ? () => settled : undefined;
  const yKey = y ? y().join(":") : "auto";
  const precision = series[0]?.precision ?? 1;
  // Uncontrolled, same as a `TimeSeries`/`MultiSeries` sparkline: a click opens it, the overlay's own
  // Escape/close/backdrop hands back here. Not `MultiSeries` while collapsed -- see the doc comment above.
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);

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
          // Holds the previous value until the next tick's x, then jumps: how a written setpoint
          // or a fixed clamp limit actually moves, confirmed against a sim rig's controller ticks
          // (a `regulate` write lands as an instant step between two ticks, not a ramp) -- a plain
          // line would draw a diagonal guess across the interval that never happened.
          paths: s.stepped ? uPlot.paths!.stepped!({ align: 1 }) : undefined,
          points: { show: false },
          // false, same reasoning and same `toBreaks` conversion as `MultiSeries`: a real gap
          // (NaN, converted to null by the `setData` effect below) must still draw as a break.
          spanGaps: false,
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
        { show: true, stroke: axisColour, font: "11px system-ui", size: 40, gap: 4, space: 34, grid: { stroke: gridColour, width: 1 }, ticks: { show: false }, values: axisValues(precision) },
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
    chart.current?.setData([t, ...series.map((s) => toBreaks(thin(s.v, every)))] as uPlot.AlignedData);
  }, [series, every]);

  if (open) {
    return (
      <MultiSeries
        series={series.map((s, i) => asMultiSeriesTrace(s, i, unit))}
        unit={unit}
        title={title}
        height="fill"
        windowS={windowS}
        yScale={yScale}
        range={range}
        every={every}
        exportHref={exportHref}
        expanded
        onExpandChange={(next) => {
          if (!next) close();
        }}
      />
    );
  }
  return <div ref={host} className="fb-chart fb-loop-mini" onClick={() => setOpen(true)} title="Open the full chart" />;
}

export interface ControllerPanelProps {
  controller: ControllerOut;
  /** The signal the controller regulates (`controller.measured_signal`): its unit, range, bands and precision shape the PV/SP rows and the process trend. */
  source: SignalOut;
  /**
   * The signal it drives (`controller.output_signal`): its `limits` draw the OP bar
   * and its unit labels the drive rows. Omitted, the unit is
   * `controller.output_unit` and the OP row has no bar.
   */
  target?: SignalOut;
  /** The controller's recent ticks; `useControllers` supplies one per controller. Omit for a readout-only panel. */
  history?: ControllerTrace;
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
  /** The controller's ticks in the store, as an export URL; both trends' download menus offer it. */
  exportHref?: string;
  /** Process and Drive mini trends, stacked at the right. Off shows the three rows alone. */
  trends?: boolean;
  /** Each trend's height in px; default 150 (the Controllers page's own room). A dashboard tile computes and passes what it actually has. */
  trendHeight?: number;
  /** Law and feedforward state: a compact, always-open section (not a `<details>` any more -- a user report found the disclosure hid content people needed every time). Only the Controllers page opens this; a dashboard widget never does. */
  detail?: boolean;
  /**
   * No outer card and no header (name, source, mode badge): for a caller that already draws its
   * own frame -- `WidgetFrame`'s tile, fed the title/subtitle/mode badge through `useWidgetChrome`.
   * Default false: the full card the Controllers page shows.
   */
  bare?: boolean;
  /** Extra content in the card, below the law/feedforward details -- the Controllers page's collapsible write state / device commands. Ignored when `bare`. */
  extra?: ReactNode;
  /** This browser may drive the rig (the server's `AuthState.canOperate`); default true. False dims `controls`/`headerControls` and blocks clicks into them, greyed out but still visible -- a proactive echo of the 401 the server would otherwise give. */
  canOperate?: boolean;
  /** The controller's own conditions, when the caller has them; omitted, the store's (`useConditions`). `latched` and `not_permitted` are shown on the panel. */
  conditions?: Condition[];
  /** Lets a latch on this controller go (`POST /api/rig/reset {cause}`); offered for its own `on_fault:<name>` latch. Omitted: no Reset here. */
  onReset?(cause: string): void;
}

/** Condition codes the panel names under its header: why regulating is refused or held. */
const HELD_CODES = new Set(["latched", "not_permitted"]);

const EMPTY: ControllerTrace = { t: [], reference: [], measured: [], output: [], expected: [], correction: [] };
/** Default trend height on the Controllers page, which has room; a dashboard tile passes its own (`trendHeight`), computed from what it actually has. */
const TREND_HEIGHT = 150;

/** True when what the target will give differs from what was asked, beyond float noise. */
const differs = (demand: number | null, expected: number) => demand == null || Math.abs(expected - demand) > 1e-9 * Math.max(1, Math.abs(demand));

/** The trace column's last value: the current one, or null when the latest tick had none (never an older one, which would be stale). */
const latest = (column: (number | null)[]): number | null => (column.length ? column[column.length - 1]! : null);

/** A `frozen` condition's `details.quality` / `details.reason`: why its measured signal has no value. */
const frozenQuality = (details: unknown) => {
  const q = (details as { quality?: unknown } | null | undefined)?.quality;
  return q === "invalid" || q === "stale" || q === "not_applicable" || q === "pending" ? q : undefined;
};
const frozenReason = (details: unknown) => {
  const r = (details as { reason?: unknown } | null | undefined)?.reason;
  return typeof r === "string" ? r : undefined;
};

/** A value's fraction of `range`, clamped to [0, 1]; null with no value or no range. */
const fractionOf = (value: number | null, range: [number, number] | null | undefined): number | null =>
  value == null || !range ? null : Math.min(1, Math.max(0, (value - range[0]) / (range[1] - range[0])));

/** Warn/alarm band edges that fall inside `range`, as left-offset percentages for ticks on a bar (mirrors `Readout`). */
function bandTicks(signal: Pick<SignalOut, "warning" | "alarm">, range: [number, number]): Array<{ band: "warn" | "alarm"; left: number }> {
  return ([["warning", "warn"], ["alarm", "alarm"]] as const).flatMap(([key, band]) =>
    (signal[key] ?? [])
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
  const { type, ...args } = feedforward;
  if (type === "table") {
    const points = (args as { points?: Array<[number, number]> }).points ?? [];
    const pair = (p: [number, number]) => `${p[0]} → ${p[1]}`;
    const shown = points.length <= 4 ? points.map(pair).join(", ") : `${points.slice(0, 2).map(pair).join(", ")} … ${points.slice(-2).map(pair).join(", ")}`;
    return { points: `${points.length} point${points.length === 1 ? "" : "s"}${shown ? `, ${shown}` : ""}` };
  }
  if (type === "identity") return "demand = setpoint; the law corrects about it";
  if (type === "none") return "0; the law does all the work";
  return args;
}

/** `90` → `1 min 30 s`, `3600` → `1 h 0 min`; whole seconds. */
const span = (s: number) => {
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : m > 0 ? `${m} min ${r} s` : `${r} s`;
};

/** A rig-time instant (seconds since the epoch) as a wall-clock time of day, to the second when under an hour away. */
const clockAt = (s: number, nowS: number) => new Date(s * 1000).toLocaleTimeString([], Math.abs(s - nowS) < 3600 ? {} : { hour: "2-digit", minute: "2-digit" });

/** A pace as the wire shows one: a speed `{value, per}` → `10 %RH/min`; a duration `{seconds, nanoseconds}` → `over 3 min 0 s`. */
function describePace(pace: unknown, unit: string): string {
  const p = (pace ?? {}) as { value?: unknown; per?: unknown; seconds?: unknown; nanoseconds?: unknown };
  if (typeof p.value === "number" && typeof p.per === "string") {
    const per = ({ second: "s", minute: "min", hour: "h", day: "d" } as Record<string, string>)[p.per] ?? p.per;
    return `at ${shortNumber(p.value)} ${unit}/${per}`;
  }
  if (typeof p.seconds === "number") return `over ${span(p.seconds + (typeof p.nanoseconds === "number" ? p.nanoseconds / 1e9 : 0))}`;
  return "";
}

/**
 * What a controller is following, for the line under TARGET: a ramp as
 * "ramping to 75 %RH at 10 %RH/min · arrives 17:31 (in 2 min 30 s)", a hold
 * as "holding 30 %RH until 17:31", a profile as "profile · segment 2 of 4 ·
 * ramping to …"; once `arrived`, the destination and "arrived". Null for a
 * fixed reference. `end_time` is seconds from the rig's start, so the
 * arrival time needs `startS` (the rig's origin in epoch seconds, from
 * `GET /api/clock`) and `nowS` (rig time now, `useNowS`); without `startS`
 * the arrival is left off.
 */
export function describeReference(controller: Pick<ControllerOut, "reference" | "arrived">, unit: string, precision: number, nowS: number, startS: number | null): string | null {
  const g = controller.reference;
  if (g === null || typeof g === "number") return null;
  const value = (v: unknown) => (typeof v === "number" ? `${fixed(v, precision)} ${unit}` : "?");
  const arrived = controller.arrived === true;
  const arrival = (verb: string) => {
    if (typeof g.end_time !== "number" || startS === null) return "";
    const at = startS + g.end_time;
    const left = at - nowS;
    return ` · ${verb} ${clockAt(at, nowS)}${left > 0 ? ` (in ${span(left)})` : ""}`;
  };
  const segment = (seg: GeneratorOut, live: boolean): string => {
    if (seg.type === "linear_ramp_setpoint") {
      const pace = describePace(seg.pace, unit);
      return `${live ? "ramping" : "ramp"} to ${value(seg.end)}${pace ? ` ${pace}` : ""}`;
    }
    if (seg.type === "dwell") {
      const d = seg.duration as { seconds?: number; nanoseconds?: number } | null | undefined;
      const forS = d && typeof d.seconds === "number" ? d.seconds + (d.nanoseconds ?? 0) / 1e9 : null;
      return `${live ? `dwelling at ${value(seg.value)}` : `dwell ${value(seg.value)}`}${forS !== null ? ` for ${span(forS)}` : ""}`;
    }
    return `following ${humanise(seg.type).toLowerCase()}`;
  };
  if (g.type === "profile") {
    const segments = Array.isArray(g.segments) ? (g.segments as GeneratorOut[]) : [];
    if (arrived) return `profile of ${segments.length} segment${segments.length === 1 ? "" : "s"} · arrived`;
    const active = typeof g.active === "number" ? segments[g.active] : undefined;
    const which = typeof g.active === "number" ? ` · segment ${g.active + 1} of ${segments.length}` : "";
    return `profile${which}${active ? ` · ${segment(active, true)}` : ""}${arrival("ends")}`;
  }
  if (arrived) {
    if (g.type === "linear_ramp_setpoint") return `ramped to ${value(g.end)} · arrived`;
    if (g.type === "dwell") return `dwelt at ${value(g.value)} · arrived`;
    return `${segment(g, false)} · arrived`;
  }
  return `${segment(g, true)}${arrival(g.type === "dwell" ? "until" : "arrives")}`;
}

/** The rig clock's origin in epoch seconds (`GET /api/clock`, once per mount): what a generator's `end_time` counts from. */
function useRigStartS(): number | null {
  const rig = useRig();
  const clock = useQuery(() => rig.clock(), [rig]);
  return clock.data ? clock.data.start_time_ns / 1e9 : null;
}

/**
 * One controller as a process-control faceplate: mode badge and a banner
 * for the top condition in the header, then three aligned rows -- PV (what
 * the source reads), SP (where it aims, with the entry and Move controls),
 * OP (what the target was set to, after limits) -- each with a bar, and the
 * Process/Drive trends stacked beside them. `useControllers` supplies the
 * history and the caller the controls; the live PV, the output's write
 * state and the source device's run come from the telemetry store, so the
 * panel needs a `RigProvider` above it.
 *
 * The trends are drawn apart from the OP bar on purpose: a reading and a
 * demand are different things even when the rig gives them the same unit (a
 * temperature the heater is told to hold is not the temperature measured).
 */
export function ControllerPanel({
  controller,
  source,
  target,
  history = EMPTY,
  windowS,
  controls,
  headerControls,
  yScale,
  every,
  exportHref,
  trends = true,
  trendHeight = TREND_HEIGHT,
  detail = false,
  bare = false,
  extra,
  canOperate = true,
  conditions,
  onReset,
}: ControllerPanelProps) {
  const live = useReading(controller.measured_signal);
  const nowS = useNowS();
  const startS = useRigStartS();
  const write = useWriteState(controller.output_signal) ?? target?.write ?? null;
  const run = useDeviceRun(deviceOf(controller.measured_signal));
  const measuredBand = useBandLevel(controller.measured_signal);
  // `frozen`: the measured signal has no value, so the law is not stepped and nothing is written.
  const held = useConditions(controller.name);
  const frozen = held.find((c) => c.code === "frozen");
  const unit = source.unit;
  const dUnit = target?.unit ?? controller.output_unit ?? unit;
  const precision = source.precision ?? 2;
  const fmt = (value: number | string | null | undefined, u: string) =>
    value == null ? "—" : typeof value === "number" ? `${fixed(value, precision)} ${u}` : value;

  const process = [
    {
      label: "measured",
      hint: `What ${controller.measured_signal} measures: the value the controller is trying to hold.`,
      unit,
      t: history.t,
      v: history.measured,
      precision,
    },
    {
      label: "setpoint",
      hint: "Where the controller is aiming right now; a ramp moves it over time.",
      unit,
      t: history.t,
      v: history.reference,
      dash: true,
      // Holds between ticks rather than interpolating a line to the next one: a written setpoint
      // moves in a single instant step, not a diagonal ramp across the poll interval (confirmed
      // against a sim rig's controller ticks -- a `regulate` write shows up as a flat value that
      // jumps between two adjacent ticks). A generator's own ramp still reads as smooth: it moves
      // in many small steps, one per tick, close enough together to look like a line.
      stepped: true,
      color: "var(--fb-series-setpoint)",
      precision,
    },
  ];
  const feedforward = controller.feedforward as FeedforwardConfig | undefined;
  const ffType = typeof feedforward?.type === "string" ? feedforward.type : null;
  // Under no feedforward the correction *is* the output; drawing it twice says nothing.
  const showCorrection = ffType !== "none";
  const clampedTrace = history.expected.map((e, i) => (e != null && differs(history.output[i] ?? null, e) ? e : null));
  const drive = [
    {
      label: "output",
      hint: showCorrection ? `What the controller asks of ${controller.output_signal}: feedforward(setpoint) plus the law's correction.` : `What the controller asks of ${controller.output_signal}: the law's correction alone (no feedforward).`,
      unit: dUnit,
      t: history.t,
      v: history.output,
      precision,
    },
    // Achievable is drawn only where it differs from the output (the output signal clamped); elsewhere the two coincide.
    ...(clampedTrace.some((v) => v != null)
      ? [
          {
            label: "achievable (clamped)",
            hint: `What ${controller.output_signal} could actually give while the output was beyond its limit.`,
            unit: dUnit,
            t: history.t,
            v: clampedTrace,
            dash: true,
            stepped: true, // a fixed physical limit, not a value that ramps
            color: "var(--fb-series-setpoint)",
            precision,
          },
        ]
      : []),
    ...(showCorrection
      ? [
          {
            label: "correction",
            hint: "The law's share of the output: what it adds to feedforward(setpoint) to close the error.",
            unit: dUnit,
            t: history.t,
            v: history.correction,
            width: 1,
            precision,
          },
        ]
      : []),
  ];
  const law = controller.law as Record<string, unknown> | null;
  const { type: lawType, ...rest } = law ?? {};
  const following = describeReference(controller, unit, precision, nowS, startS);
  // Under a generator the setpoint is the resolved value; failing that, recovered through the feedforward, or read off
  // the latest tick -- only when that tick carries one (a stored tick does; a live one under a `none` feedforward does not).
  const setpoint = setpointOf(controller) ?? (following ? latest(history.reference) : null);
  // PV: the measured signal's newest reading from the store, else what the controller saw at its last tick --
  // never an older value when the newest has none: that shows as "—" and why.
  const measured = live ?? (controller.measured_value ? { t: controller.measured_value.time_ns / 1e9, value: controller.measured_value.value, quality: controller.measured_value.quality, reason: controller.measured_value.reason } : undefined);
  const reading = typeof measured?.value === "number" ? measured.value : null;
  const none = noValue(measured, (v) => withUnit(fixed(v, precision), unit));
  // OP: the output signal's write state (after limits) from `/ws/writes`, else what the controller expects it to give.
  const clamped = write ? write.at_limit !== null : controller.expected != null && differs(controller.output_value, controller.expected);
  const output = write?.value ?? controller.expected ?? controller.output_value;
  const requested = write?.requested ?? controller.output_value;
  const deviation = reading != null && setpoint != null ? reading - setpoint : null;
  const deviationWarn = alarmLevel(reading, source, measuredBand) !== "ok";

  const range = source.range;
  const pvFraction = fractionOf(reading, range);
  const spFraction = fractionOf(setpoint, range);
  const pvTicks = range ? bandTicks(source, range) : [];
  const outputRange = target?.limits ?? null;
  const opFraction = fractionOf(output, outputRange);
  // Which rail the output is pinned to, for the OP bar's highlighted end when clamped.
  const limitEdge: "hi" | "lo" | null = !clamped ? null : write?.at_limit ? (write.at_limit === "high" ? "hi" : "lo") : (controller.output_value ?? 0) > controller.expected! ? "hi" : "lo";

  // The measured signal's device run says whether anything is arriving at all; the rig's own `stale`
  // reading catches a device that is nominally running but has gone quiet, and its `frozen` condition a
  // controller holding because its measured signal has no value. Open loop is not a mode (D23): it is the
  // `open_loop` law type, under "regulating" (it is still driving the output), just with nothing correcting for error.
  const offline = !!run && (!run.running || run.conditions.some((c) => c.code === "offline"));
  const stale = measured?.quality === "stale";
  const frozenWhy = frozen ? describeQuality(frozenQuality(frozen.details), frozenReason(frozen.details)) : "";
  const banner = offline
    ? { text: "measured offline", hint: `${deviceOf(controller.measured_signal)} is not being read; the controller has nothing to regulate on.`, level: "warn" }
    : frozen
      ? { text: "frozen", hint: `${frozen.message || `${controller.measured_signal} has no value${frozenWhy ? ` (${frozenWhy})` : ""}`}. The law is not stepped and nothing is written until it reads again.`, level: frozen.severity === "info" ? "info" : "warn" }
      : stale
      ? { text: "no recent reading", hint: `${controller.measured_signal}: ${describeQuality("stale", measured?.reason)}.`, level: "warn" }
      : clamped
        ? { text: "output at limit", hint: `${controller.output_signal} cannot give the full output; it is clamped to what it can achieve.`, level: "warn" }
        : lawType === "open_loop"
          ? { text: "open loop", hint: "Following the setpoint with no law correcting for error.", level: "warn" }
          : null;

  // Neither `controls` nor `headerControls` are this panel's own -- the caller builds them (a
  // setpoint entry, Stop) -- so a sub-operate browser gets them dimmed and click-blocked rather
  // than a per-button `disabled`, which would need reaching into content this panel doesn't own.
  const gated = (node: ReactNode): ReactNode =>
    canOperate ? node : (
      <span aria-disabled="true" style={{ opacity: 0.5, pointerEvents: "none" }}>
        {node}
      </span>
    );

  const frame = (
    <div className={`fb-loop-frame${trends ? "" : " fb-loop-no-trends"}`}>
      {!bare && (
        <header className="fb-loop-head">
          <h3><Ref kind="controller" name={controller.name}>{controller.label ? describeController(controller) : target ? describeSignal(target) : controller.name}</Ref></h3>
          <span className="fb-muted" title={`${controller.name} regulates ${controller.measured_signal}`}>
            regulates <Ref kind="signal" name={controller.measured_signal}>{describeSignal(source)}</Ref>
            {controller.is_default && " · default"}
          </span>
          <span className={`fb-badge fb-mode fb-mode-${controller.mode}`}>{controller.mode}</span>
          {/* In the header, not its own row: a row that appears/disappears as `banner` flips
              would otherwise reflow everything below it each time (grid rows size to content). */}
          {banner && (
            <span className={banner.level === "info" ? "fb-loop-info" : "fb-loop-warn"} title={banner.hint} data-testid="loop-banner">
              {banner.level === "info" ? "ⓘ" : "⚠"} {banner.text}
            </span>
          )}
        </header>
      )}
      <LatchLine controller={controller} conditions={conditions ?? held} onReset={canOperate ? onReset : undefined} />
      <dl className="fb-loop-rows">
        <div className="fb-loop-row">
          <dt title="measured value — PV">Measured</dt>
          <dd title={none?.hint}>{none ? <>{none.glyph} <QualityBadge state={none} /></> : fmt(reading, unit)}</dd>
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
            {deviation != null ? `${fixed(Math.abs(deviation), precision)} ${unit} ${deviation >= 0 ? "above" : "below"} setpoint` : " "}
          </span>
        </div>
        <div className="fb-loop-row">
          <dt title="setpoint — SP">Setpoint</dt>
          <dd>{fmt(setpoint, unit)}</dd>
          {controls && <span className="fb-loop-sp-controls">{gated(controls)}</span>}
          <span className="fb-loop-caption" data-testid="following">{following ? `→ ${following}` : " "}</span>
        </div>
        <div className="fb-loop-row">
          <dt title="what the output signal was set to, after limits — OP">Output</dt>
          <dd style={clamped ? { color: "var(--fb-alarm)" } : undefined}>{fmt(output, dUnit)}</dd>
          {opFraction !== null && (
            <div
              className={`fb-range${clamped ? ` fb-range-limit-${limitEdge}` : ""}`}
              title={clamped ? `output at its ${limitEdge === "hi" ? "upper" : "lower"} limit; requested ${fmt(requested, dUnit)}` : `${outputRange![0]} – ${outputRange![1]} ${dUnit}`}
            >
              <div className="fb-range-fill" style={{ width: `${opFraction * 100}%` }} />
            </div>
          )}
          <span className="fb-loop-caption">{clamped ? `requested ${fmt(requested, dUnit)}` : " "}</span>
        </div>
      </dl>
      {headerControls && <div className="fb-loop-stop">{gated(headerControls)}</div>}
      {trends && (
        <div className="fb-loop-trends">
          <div>
            <h4 className="fb-loop-chart-title" title="What the controller measures against where it is aiming">
              Process <span className="fb-muted">{unit}</span>
            </h4>
            <MiniTrend
              series={process}
              height={trendHeight}
              every={every}
              yScale={yScale}
              range={source.range}
              windowS={windowS}
              settledBand={setpoint != null ? settledBand(setpoint, source.warning, source.range, history.measured) : null}
              title={`${describeController(controller)} · process`}
              unit={unit}
              exportHref={exportHref}
            />
          </div>
          <div>
            <h4 className="fb-loop-chart-title" title={`What the controller asks of ${controller.output_signal}, and what it can give back`}>
              Drive <span className="fb-muted">{[target ? describeSignal(target) : controller.output_signal, dUnit].filter((part) => part && part.toLowerCase() !== "drive").join(" · ")}</span>
            </h4>
            {/* The output signal's limits, when known: "at limit" then reads as the line sitting on the rail, not a mystery flat spot. */}
            <MiniTrend
              series={drive}
              height={trendHeight}
              every={every}
              yScale={outputRange ? "range" : undefined}
              range={outputRange}
              windowS={windowS}
              title={`${describeController(controller)} · drive`}
              unit={dUnit}
              exportHref={exportHref}
            />
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
              <span className="fb-tag">{typeof lawType === "string" ? lawType : "?"}</span>
              {fieldsLine(rest, { tt_s: "s", last_raw: dUnit, last_elapsed: "s" }).map((f) => (
                <span key={f.key} title={f.title}>
                  {" "}
                  · {f.text}
                </span>
              ))}
            </p>
          ) : (
            <p className="fb-loop-law-line fb-muted" title="No law is fitted; the target is driven by demand alone.">
              manual · no law
            </p>
          )}
          {ffType && (
            <p className="fb-loop-law-line fb-loop-feedforward" title="What the controller asks for before the law corrects: the setpoint mapped into the output's unit">
              <span className="fb-tag">{ffType}</span>
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


/**
 * Why this controller cannot regulate now, under its header: each latch cause holding it (the
 * software stop, its own fault action) and a `not_permitted` hold, with Reset for its own fault
 * latch (the software stop's Reset is on the page banner). Nothing when nothing holds it.
 */
function LatchLine({ controller, conditions, onReset }: { controller: ControllerOut; conditions?: Condition[]; onReset?(cause: string): void }) {
  const own = `on_fault:${controller.name}`;
  // A tick's snapshot carries no `latched` (only `/api/controllers` does), so the controller's own
  // `latched` condition -- raised by its fault latch -- stands for that cause too.
  const causes = [...new Set([...(controller.latched ?? []), ...((conditions ?? []).some((c) => c.code === "latched") ? [own] : [])])];
  const held = (conditions ?? []).filter((c) => HELD_CODES.has(c.code) && c.code !== "latched");
  if (causes.length === 0 && held.length === 0) return null;
  return (
    <div className="fb-loop-latch" role="status" data-testid="controller-latch">
      {causes.map((cause) => (
        <span key={cause}>
          {cause === "stop" ? "Latched by the software stop" : cause === own ? "Latched by its fault action" : `Latched by ${cause}`}
          {cause === own && onReset && (
            <button type="button" className="btn fb-loop-reset" onClick={() => onReset(cause)} data-testid="controller-reset">
              Reset
            </button>
          )}
        </span>
      ))}
      {held.map((c) => (
        <span key={c.code} title={c.message}>
          {c.code === "not_permitted" ? `Held: ${c.message}` : c.message}
        </span>
      ))}
    </div>
  );
}
