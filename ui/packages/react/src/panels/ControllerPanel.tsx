import { useEffect, useRef, type ReactNode } from "react";
import uPlot from "uplot";
import type { ControllerOut, FeedforwardConfig, GeneratorOut, SignalOut } from "@flyball/client";
import { alarmLevel, describeController, describeStateKey, deviceOf, humanise, setpointOf, describeSignal, fixed, tickDigits } from "@flyball/client";
import type { ControllerTrace } from "../hooks/useControllers.js";
import { useQuery } from "../hooks/useQuery.js";
import { Ref } from "../links.js";
import { useRig } from "../provider.js";
import { useDeviceRun, useFreshness, useNowS, useSignal, useWriteState } from "../store/hooks.js";
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
 * "auto": uPlot's plain fit-to-data zooms into pure sensor noise once a controller
 * settles onto a flat setpoint, and a hold with a millikelvin of noise reads
 * as a storm. The floor is whichever is widest of the warn band, 2% of the
 * signal's declared range, or 5x the reading's own noise; widened further
 * to cover the reading itself, so a real excursion (a target at limit,
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
        { show: true, stroke: axisColour, font: "11px system-ui", size: 40, gap: 4, space: 34, grid: { stroke: gridColour, width: 1 }, ticks: { show: false }, values: (_u, vals) => vals.map((v) => fixed(v, tickDigits(vals, precision))) },
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

export interface ControllerPanelProps {
  controller: ControllerOut;
  /** The signal the controller regulates (`controller.source`): its unit, range, bands and precision shape the PV/SP rows and the process trend. */
  source: SignalOut;
  /**
   * The signal it drives (`controller.target`): its `limits` draw the OP bar
   * and its unit labels the drive rows. Omitted, the unit is
   * `controller.demand_unit` and the OP row has no bar.
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
}

const EMPTY: ControllerTrace = { t: [], reference: [], reading: [], demand: [], expected: [], correction: [] };
/** Default trend height on the Controllers page, which has room; a dashboard tile passes its own (`trendHeight`), computed from what it actually has. */
const TREND_HEIGHT = 150;

/** True when what the target will give differs from what was asked, beyond float noise. */
const differs = (demand: number | null, expected: number) => demand == null || Math.abs(expected - demand) > 1e-9 * Math.max(1, Math.abs(demand));

/** The trace column's last value: the current one, or null when the latest tick had none (never an older one, which would be stale). */
const latest = (column: (number | null)[]): number | null => (column.length ? column[column.length - 1]! : null);

/** A value's fraction of `range`, clamped to [0, 1]; null with no value or no range. */
const fractionOf = (value: number | null, range: [number, number] | null | undefined): number | null =>
  value == null || !range ? null : Math.min(1, Math.max(0, (value - range[0]) / (range[1] - range[0])));

/** Warn/alarm band edges that fall inside `range`, as left-offset percentages for ticks on a bar (mirrors `Readout`). */
function bandTicks(signal: Pick<SignalOut, "warn" | "alarm">, range: [number, number]): Array<{ band: "warn" | "alarm"; left: number }> {
  return (["warn", "alarm"] as const).flatMap((band) =>
    (signal[band] ?? [])
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
    if (seg.tag === "linear_ramp_setpoint") {
      const pace = describePace(seg.pace, unit);
      return `${live ? "ramping" : "ramp"} to ${value(seg.end)}${pace ? ` ${pace}` : ""}`;
    }
    if (seg.tag === "hold") {
      const d = seg.duration as { seconds?: number; nanoseconds?: number } | null | undefined;
      const forS = d && typeof d.seconds === "number" ? d.seconds + (d.nanoseconds ?? 0) / 1e9 : null;
      return `${live ? "holding" : "hold"} ${value(seg.value)}${forS !== null ? ` for ${span(forS)}` : ""}`;
    }
    return `following ${humanise(seg.tag).toLowerCase()}`;
  };
  if (g.tag === "profile") {
    const segments = Array.isArray(g.segments) ? (g.segments as GeneratorOut[]) : [];
    if (arrived) return `profile of ${segments.length} segment${segments.length === 1 ? "" : "s"} · arrived`;
    const active = typeof g.active === "number" ? segments[g.active] : undefined;
    const which = typeof g.active === "number" ? ` · segment ${g.active + 1} of ${segments.length}` : "";
    return `profile${which}${active ? ` · ${segment(active, true)}` : ""}${arrival("ends")}`;
  }
  if (arrived) {
    if (g.tag === "linear_ramp_setpoint") return `ramped to ${value(g.end)} · arrived`;
    if (g.tag === "hold") return `held ${value(g.value)} · arrived`;
    return `${segment(g, false)} · arrived`;
  }
  return `${segment(g, true)}${arrival(g.tag === "hold" ? "until" : "arrives")}`;
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
 * history and the caller the controls; the live PV, the target's write
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
}: ControllerPanelProps) {
  const point = useSignal(controller.source);
  const nowS = useNowS();
  const startS = useRigStartS();
  const write = useWriteState(controller.target) ?? target?.write ?? null;
  const run = useDeviceRun(deviceOf(controller.source));
  const fresh = useFreshness(controller.source);
  const unit = source.unit;
  const dUnit = target?.unit ?? controller.demand_unit ?? unit;
  const precision = source.precision ?? 2;
  const fmt = (value: number | string | null | undefined, u: string) =>
    value == null ? "—" : typeof value === "number" ? `${fixed(value, precision)} ${u}` : value;

  const process = [
    {
      label: "reading",
      hint: `What ${controller.source} measures: the value the controller is trying to hold.`,
      unit,
      t: history.t,
      v: history.reading,
      precision,
    },
    {
      label: "setpoint",
      hint: "Where the controller is aiming right now; a ramp moves it over time.",
      unit,
      t: history.t,
      v: history.reference,
      dash: true,
      color: "var(--fb-series-setpoint)",
      precision,
    },
  ];
  const feedforward = controller.feedforward as FeedforwardConfig | undefined;
  const ffTag = typeof feedforward?.tag === "string" ? feedforward.tag : null;
  // Under no feedforward the correction *is* the demand; drawing it twice says nothing.
  const showCorrection = ffTag !== "none";
  const clampedTrace = history.expected.map((e, i) => (e != null && differs(history.demand[i] ?? null, e) ? e : null));
  const drive = [
    {
      label: "demand",
      hint: showCorrection ? `What the controller asks of ${controller.target}: feedforward(setpoint) plus the law's correction.` : `What the controller asks of ${controller.target}: the law's correction alone (no feedforward).`,
      unit: dUnit,
      t: history.t,
      v: history.demand,
      precision,
    },
    // Achievable is drawn only where it differs from the demand (the target clamped); elsewhere the two coincide.
    ...(clampedTrace.some((v) => v != null)
      ? [
          {
            label: "achievable (clamped)",
            hint: `What ${controller.target} could actually give while the demand was beyond its limit.`,
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
  const law = controller.law as Record<string, unknown> | null;
  const { tag, ...rest } = law ?? {};
  const following = describeReference(controller, unit, precision, nowS, startS);
  // Under a generator the setpoint is the resolved value; failing that, recovered through the feedforward, or read off
  // the latest tick -- only when that tick carries one (a stored tick does; a live one under a `none` feedforward does not).
  const setpoint = setpointOf(controller) ?? (following ? latest(history.reference) : null);
  // PV: the source's newest sample from the store, else what the controller saw at its last tick.
  const reading = point?.v ?? (typeof controller.reading?.value === "number" ? controller.reading.value : null);
  // OP: the target's write state (after limits) from `/ws/writes`, else what the controller expects it to give.
  const clamped = write ? write.at_limit !== null : controller.expected != null && differs(controller.demand, controller.expected);
  const output = write?.value ?? controller.expected ?? controller.demand;
  const requested = write?.requested ?? controller.demand;
  const deviation = reading != null && setpoint != null ? reading - setpoint : null;
  const deviationWarn = alarmLevel(reading, source) !== "ok";

  const range = source.range;
  const pvFraction = fractionOf(reading, range);
  const spFraction = fractionOf(setpoint, range);
  const pvTicks = range ? bandTicks(source, range) : [];
  const outputRange = target?.limits ?? null;
  const opFraction = fractionOf(output, outputRange);
  // Which rail the output is pinned to, for the OP bar's highlighted end when clamped.
  const limitEdge: "hi" | "lo" | null = !clamped ? null : write?.at_limit ? (write.at_limit === "high" ? "hi" : "lo") : (controller.demand ?? 0) > controller.expected! ? "hi" : "lo";

  // The source device's run says whether anything is arriving at all; freshness catches a device that is
  // nominally running but has gone quiet. `controller.mode === "open"` is a distinct wire state the backend
  // does not currently emit; the real "no feedback" condition an operator meets is the `open_loop` law tag --
  // present and "regulating" (it is still driving the target), just with nothing correcting for error.
  const offline = !!run && (!run.running || run.conditions.some((c) => c.kind === "offline"));
  const stale = alarmLevel(reading, source, fresh) === "stale";
  const banner = offline
    ? { text: "source offline", hint: `${deviceOf(controller.source)} is not being read; the controller has nothing to regulate on.` }
    : stale
      ? { text: "no recent reading", hint: `No sample has arrived on ${controller.source} recently.` }
      : clamped
        ? { text: "target at limit", hint: `${controller.target} cannot give the full demand; it is clamped to what it can achieve.` }
        : controller.mode === "open" || tag === "open_loop"
          ? { text: "open loop", hint: "Following the setpoint with no law correcting for error." }
          : null;

  const frame = (
    <div className={`fb-loop-frame${trends ? "" : " fb-loop-no-trends"}`}>
      {!bare && (
        <header className="fb-loop-head">
          <h3><Ref kind="controller" name={controller.name}>{describeController(controller)}</Ref></h3>
          <span className="fb-muted" title={`${controller.name} regulates ${controller.source}`}>
            regulates <Ref kind="signal" name={controller.source}>{describeSignal(source)}</Ref>
            {controller.default && " · default"}
          </span>
          <span className={`fb-badge fb-mode fb-mode-${controller.mode}`}>{controller.mode}</span>
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
            {deviation != null ? `${fixed(Math.abs(deviation), precision)} ${unit} ${deviation >= 0 ? "above" : "below"} target` : " "}
          </span>
        </div>
        <div className="fb-loop-row">
          <dt title="setpoint — SP">Target</dt>
          <dd>{fmt(setpoint, unit)}</dd>
          {controls && <span className="fb-loop-sp-controls">{controls}</span>}
          <span className="fb-loop-caption" data-testid="following">{following ? `→ ${following}` : " "}</span>
        </div>
        <div className="fb-loop-row">
          <dt title="what the target was set to, after limits — OP">Output</dt>
          <dd>{fmt(output, dUnit)}</dd>
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
      {headerControls && <div className="fb-loop-stop">{headerControls}</div>}
      {trends && (
        <div className="fb-loop-trends">
          <div>
            <h4 className="fb-loop-chart-title" title="What the controller measures against where it is aiming">
              Process <span className="fb-muted">{unit}</span>
            </h4>
            <MiniTrend series={process} height={trendHeight} every={every} yScale={yScale} range={source.range} windowS={windowS} settledBand={setpoint != null ? settledBand(setpoint, source.warn, source.range, history.reading) : null} />
          </div>
          <div>
            <h4 className="fb-loop-chart-title" title={`What the controller asks of ${controller.target}, and what it can give back`}>
              Drive <span className="fb-muted">{[target ? describeSignal(target) : controller.target, dUnit].filter((part) => part && part.toLowerCase() !== "drive").join(" · ")}</span>
            </h4>
            {/* The target's limits, when known: "at limit" then reads as the line sitting on the rail, not a mystery flat spot. */}
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
            <p className="fb-loop-law-line fb-muted" title="No law is fitted; the target is driven by demand alone.">
              manual · no law
            </p>
          )}
          {ffTag && (
            <p className="fb-loop-law-line fb-loop-feedforward" title="What the controller asks for before the law corrects: the setpoint mapped into the target's unit">
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
