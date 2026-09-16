import { useCallback, useEffect, useRef, useState } from "react";
import uPlot from "uplot";
import { axisSize, yRange, type YScale } from "./yscale.js";
import { thin, pointCap } from "./thin.js";
import { navigation } from "./navigation.js";
import { ChartToolbar } from "./ChartToolbar.js";
import { ChartOverlay, plotHeight } from "./ChartOverlay.js";
import { saveTable, seriesTable } from "./download.js";
import { useChartLifecycle } from "./useChartLifecycle.js";
import type { TraceRef } from "../store/hooks.js";
import { emptyTrace, type TraceView } from "../store/telemetry.js";

export interface MultiSeriesTrace {
  label: string;
  /** Unit of this trace. A trace whose unit differs from the chart's `unit` is drawn against its own y axis on the right. */
  unit?: string;
  /** Seconds since the epoch, ascending. Traces need not share the same times. Omitted when the chart draws from a `source`. */
  t?: number[];
  v?: (number | null)[];
  /** The channel in the `source` store this trace draws from (`source.measurand`); the trace at the same index of `source.keys` otherwise. */
  key?: string;
  /** Any CSS colour. Defaults cycle through `--fb-series-1` … `--fb-series-6`. */
  color?: string;
  /** Dashed line: `true` for a default dash, or a canvas dash array. */
  dash?: boolean | number[];
  width?: number;
  /** Shown on hover over the legend entry: what this trace is, in a sentence. */
  hint?: string;
  /** Decimal places in the legend. */
  precision?: number;
}

export interface MultiSeriesProps {
  /** What to draw: labels, units, styles -- and the points too, unless `source` gives them. */
  series: MultiSeriesTrace[];
  /**
   * Draw straight from the telemetry store (`useTraceRef`): the chart subscribes
   * itself, redraws at most ten times a second while on screen, and never
   * re-renders on samples. Each trace's `key` (or its index in `source.keys`)
   * says which channel it is.
   */
  source?: TraceRef;
  /** Hold every redraw (the editor is dragging); one follows when released. */
  paused?: boolean;
  /** `cursor.sync.key`: charts sharing a key share a cursor. Default: the page (the location hash). */
  syncKey?: string;
  /** Names this chart in the redraw counter (`window.__fb.chartsById`); default the trace labels. */
  id?: string;
  /** Unit of the left y axis; traces in any other unit get their own axis, alternating right/left up to 4 axes total (beyond that, extra units fold onto one shared axis). */
  unit?: string;
  /** Plot height in pixels, `"auto"` (follows the width: 0.3 of it, between 160 and 360), or `"fill"` (follows the host's own laid-out height, like the full-size overlay). */
  height?: number | "auto" | "fill";
  /**
   * Seconds of x axis to show. The traces grow from the left until they fill
   * the window, then scroll: the newest point stays at the right edge.
   * Omit to autoscale over everything held.
   */
  windowS?: number;
  /** How to scale the left y axis: fit the data, the declared range (`range` prop), or fixed bounds. */
  yScale?: YScale;
  /** The declared range `yScale="range"` uses. */
  range?: [number, number] | null;
  /** Draw one point in `every` per trace; the newest is always kept. */
  every?: number;
  /** Pan/zoom toolbar and wheel/drag navigation; on by default. */
  navigable?: boolean;
  /** Heading of the full-size view; default the trace labels and unit. */
  title?: string;
  /** Shown full-size in an overlay. Uncontrolled unless given: the toolbar button or a double-click opens it; Escape or the close button closes it. */
  expanded?: boolean;
  onExpandChange?(expanded: boolean): void;
  /** The same data in the store, as an export URL; the toolbar's download menu offers it beside what is held here. */
  exportHref?: string;
}

const FALLBACK = ["#2557a7", "#c2410c", "#15803d", "#7e22ce", "#b45309", "#0e7490", "#4a3aa7", "#b91c1c"];
const DASH = [6, 4];

/**
 * At most this many *extra* (non-primary) axes are drawn, alternating
 * right/left, so with the primary axis that is 6 axes on screen at once —
 * three a side. A unit past that budget still gets its own uPlot scale (it
 * is never shared with another unit: an axis is cosmetic, autoscaling is
 * not), it simply draws no ticks; its last value still shows in the legend.
 */
const MAX_EXTRA_AXES = 5;

/** Scale key for a trace: the chart's own unit (or none) shares `y`; every other unit always gets its own scale, drawn or not. */
const scaleOf = (trace: MultiSeriesTrace, unit: string | undefined) => (trace.unit === undefined || trace.unit === unit ? "y" : `y:${trace.unit}`);

/** A trace's value, formatted the same way whether it is the live legend row or a hover: `"20.5 °C"`, `"—"` when there is none. */
const displayValue = (raw: number | null | undefined, s: MultiSeriesTrace): string => (raw == null ? "—" : `${raw.toFixed(s.precision ?? 2)}${s.unit ? ` ${s.unit}` : ""}`);

/** An axis' tick labels at a fixed precision instead of uPlot's own significant-figure guess (which over-shows digits on a near-flat trace). */
const axisValues =
  (precision: number): uPlot.Axis.Values =>
  (_u, splits) =>
    splits.map((v) => (Number.isFinite(v) ? v.toFixed(precision) : ""));

/** Align traces with different time bases onto one x array, nulls where a trace has no point. */
function align(series: Array<{ t: number[]; v: (number | null)[] }>): uPlot.AlignedData {
  if (series.length === 0) return [[]];
  const shared = series.every((s) => s.t === series[0]!.t);
  if (shared) return [series[0]!.t, ...series.map((s) => s.v)] as uPlot.AlignedData;
  return uPlot.join(series.map((s) => [s.t, s.v] as uPlot.AlignedData));
}

const EMPTY: number[] = [];
const points = (s: MultiSeriesTrace) => ({ t: s.t ?? EMPTY, v: s.v ?? EMPTY });

/**
 * Legend rows are `live` (uPlot shows the value at `cursor.idx`), which stays
 * `null` — every row a placeholder dash — until the cursor moves. After each
 * draw, when nothing is actively hovering (`cursor.idx == null`), point the
 * legend at each trace's own newest point through uPlot's own `setLegend`:
 * the values (and their formatting) come from each series' own `value()`, so
 * hover and idle always agree. Traces are not resampled onto one clock (a
 * fast channel and a slow one keep their own timestamps, joined with nulls
 * where one has no point — `align()`), so "newest" is found per series, not
 * at one shared index: at the single latest instant some traces are still
 * null there. A real hover's own indices are left alone, and the toolbar
 * (driven by `nav`/`following`, not by the legend) is untouched either way.
 */
function showLatestInLegend(u: uPlot): void {
  if (u.cursor.idx != null) return;
  const n = u.data[0]?.length ?? 0;
  if (!n) return;
  const idxs: Array<number | null> = [n - 1];
  for (let sidx = 1; sidx < u.data.length; sidx++) {
    const col = u.data[sidx] as ReadonlyArray<number | null>;
    let idx: number | null = null;
    for (let k = col.length - 1; k >= 0; k--) {
      if (col[k] != null) {
        idx = k;
        break;
      }
    }
    idxs.push(idx);
  }
  u.setLegend({ idxs });
}

/** The page a chart is on, as the default cursor-sync key: charts on one page share a cursor. */
export const pageSyncKey = (): string | undefined => (typeof window === "undefined" ? undefined : window.location.hash.split("?")[0] || "#/");

/**
 * Several traces on one uPlot instance sharing the x axis; the chart is
 * built once per set of trace labels, units and styles, and fed new data on
 * every render. A trace in a unit other than the chart's is drawn against a
 * second y axis on the right, so a demand in watts can sit over a reading in
 * degrees. Legend and cursor on.
 */
export function MultiSeries({ series, source, paused, syncKey, id, unit, height = 200, windowS , yScale, range , every , navigable = true, title, expanded, onExpandChange, exportHref }: MultiSeriesProps) {
  const nav = useRef(navigation()).current;
  const [following, setFollowing] = useState(true);
  nav.onChange = setFollowing;
  const [ownExpanded, setOwnExpanded] = useState(false);
  const open = expanded ?? ownExpanded;
  const setOpen = useCallback(
    (next: boolean) => {
      onExpandChange?.(next);
      if (expanded === undefined) setOwnExpanded(next);
    },
    [expanded, onExpandChange],
  );
  const close = useCallback(() => setOpen(false), [setOpen]);
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<uPlot | null>(null);
  // The data as last aligned, so a rebuild (structure, theme, full-size) starts with it rather than empty.
  const latest = useRef<uPlot.AlignedData>([[]]);
  // Rebuild only when something structural changes, not on every data tick.
  const shape = JSON.stringify(
    series.map((s) => [s.label, s.unit ?? null, s.color ?? null, s.dash ?? null, s.width ?? null, s.precision ?? null]),
  );

  const [yFit, setYFit] = useState<YScale | null>(null);
  const wantedY: YScale = yScale ?? "auto";
  const effectiveY: YScale = yFit !== null && yFit === wantedY ? "auto" : wantedY;
  const yKey = typeof effectiveY === "object" ? `${effectiveY.min}:${effectiveY.max}` : `${effectiveY}:${range?.join(",") ?? ""}`;

  const isFill = height === "fill";
  const fillMode = open || isFill;
  const heightNum: number | "auto" = isFill ? 0 : height;

  useEffect(() => {
    if (!host.current) return;
    const el = host.current;
    const style = getComputedStyle(el);
    const palette = FALLBACK.map((fallback, i) => style.getPropertyValue(`--fb-series-${i + 1}`).trim() || fallback);
    const fg = style.getPropertyValue("--fb-muted").trim() || "#6b6b6b";
    const primaryPrecision = series.find((s) => s.unit === undefined || s.unit === unit)?.precision ?? 2;

    const scales: uPlot.Scales = {
      x: {
        time: true,
        range: (u, min, max) => nav.xRange(u, min, max, windowS),
      },
      y: yRange(effectiveY, range) ? { range: yRange(effectiveY, range)! } : {},
    };
    const axes: uPlot.Axis[] = [
      { label: "time", stroke: fg },
      { label: unit ? `(${unit})` : undefined, size: axisSize, scale: "y", stroke: fg, space: 48, values: axisValues(primaryPrecision) },
    ];
    const plotted: uPlot.Series[] = [{}];
    // Extra axes alternate right/left (side 1, 3, 1, 3, …), three a side, six on screen with the primary.
    // A unit past that budget still gets its own scale below — every unit is always autoscaled on its own,
    // never sharing with another — it simply draws no axis; its value still reads in the legend.
    let extraAxesShown = 0;
    series.forEach((s, i) => {
      const scale = scaleOf(s, unit);
      const strokeColor = s.color ?? palette[i % palette.length];
      const precision = s.precision ?? 2;
      if (!(scale in scales)) {
        scales[scale] = {};
        if (scale !== "y" && extraAxesShown < MAX_EXTRA_AXES) {
          const side = extraAxesShown % 2 === 0 ? 1 : 3;
          extraAxesShown++;
          axes.push({ label: `(${s.unit})`, size: axisSize, scale, side, grid: { show: false }, stroke: strokeColor, space: 48, values: axisValues(precision) });
        }
      }
      const line: uPlot.Series = {
        label: s.label,
        scale,
        stroke: strokeColor,
        width: s.width ?? 1.5,
        spanGaps: true,
        points: { show: false }, // a thinned or sparse trace stays a line, not a row of dots
        value: (_u, raw) => displayValue(raw, s),
      };
      if (s.dash) line.dash = Array.isArray(s.dash) ? s.dash : DASH;
      plotted.push(line);
    });

    const options: uPlot.Options = {
      width: el.clientWidth || 400,
      height: plotHeight(el, heightNum, fillMode),
      series: plotted,
      axes,
      scales,
      legend: { show: true },
      cursor: { drag: { x: true, y: false }, sync: { key: syncKey ?? pageSyncKey() ?? "", scales: ["x", null] } },
      plugins: [
        ...(navigable ? [nav.plugin()] : []),
        {
          hooks: {
            ready(u) {
              // The legend is uPlot's own DOM: put each trace's hint on its row as a tooltip.
              const rows = u.root.querySelectorAll<HTMLElement>(".u-legend .u-series");
              series.forEach((s, i) => {
                const row = rows[i + 1];
                if (row && s.hint) row.title = s.hint;
              });
              showLatestInLegend(u);
            },
          },
        },
      ],
    };
    const data = latest.current.length === series.length + 1 ? latest.current : ([[], ...series.map(() => [])] as uPlot.AlignedData);
    chart.current = new uPlot(options, data, el);
    (el as HTMLDivElement & { uplot?: uPlot }).uplot = chart.current; // for tests and devtools
    // Follow the host's laid-out width (the host is `contain: inline-size`, so it never follows the canvas):
    // window resizes, the drawer opening, a grid reflowing. The first callback fires on observe.
    const resize = new ResizeObserver(([entry]) => {
      const width = Math.round(entry?.contentRect.width ?? el.clientWidth);
      const u = chart.current;
      if (!u || width <= 0) return;
      const h = plotHeight(el, heightNum, fillMode);
      if (width !== u.width || h !== u.height) u.setSize({ width, height: h });
    });
    resize.observe(el);
    return () => {
      resize.disconnect();
      chart.current?.destroy();
      chart.current = null;
    };
    // `shape` stands in for `series`: only its structure rebuilds the chart.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape, unit, height, windowS, yKey, navigable, open, syncKey]);

  // Store-fed: the views are reused between draws, so a redraw allocates only uPlot's join.
  const views = useRef<TraceView[]>([]);
  const keysRef = useRef<string[]>([]);
  keysRef.current = series.map((s, i) => s.key ?? source?.keys[i] ?? "");
  const everyRef = useRef(every);
  everyRef.current = every;
  const { redraw } = useChartLifecycle({
    host,
    source,
    everyMs: 100,
    paused,
    id: id ?? series.map((s) => s.label).join(","),
    draw: () => {
      const u = chart.current;
      if (source) {
        const keys = keysRef.current;
        while (views.current.length < keys.length) views.current.push(emptyTrace());
        // Traces on their own time bases are joined onto one x array (a union): keep the
        // sum of their points near the cap, not each of them, or eight traces make a
        // 20 000-row axis every series has to walk.
        const maxPoints = Math.max(300, Math.floor(pointCap(u?.width ?? host.current?.clientWidth ?? 400) / Math.max(1, keys.length)));
        const opts = { every: everyRef.current, maxPoints };
        latest.current = align(keys.map((key, i) => source.store.read(key, views.current[i]!, opts)));
      }
      u?.setData(latest.current);
      if (u) showLatestInLegend(u);
    },
  });

  useEffect(() => {
    if (source) return;
    const thinned = series.map((s) => (every && every > 1 ? { t: thin(s.t ?? EMPTY, every), v: thin(s.v ?? EMPTY, every) } : points(s)));
    latest.current = align(thinned);
    redraw();
  }, [series, every, source, redraw]);

  /** Everything held for the download: the store's rows unthinned, or the props. */
  const table = () => {
    if (!source) return seriesTable(series.map((s) => ({ label: s.label, unit: s.unit ?? unit, t: s.t ?? EMPTY, v: s.v ?? EMPTY })));
    return seriesTable(
      series.map((s, i) => {
        const view = source.store.read(keysRef.current[i]!, emptyTrace());
        return { label: s.label, unit: s.unit ?? unit, t: view.t, v: view.v };
      }),
    );
  };

  const plot = <div ref={host} className={`fb-chart fb-multi${fillMode ? " fb-chart-fill" : ""}`} onDoubleClick={() => setOpen(!open)} />;
  const body = navigable ? (
    // Flex column only in fill mode: the plot's `.fb-chart-fill` (flex: 1 1 auto) needs a flex parent to
    // actually stretch into the space the host's own layout (`.fb-chart-host`/`.fb-fill`) gives this wrap.
    <div className="fb-chart-wrap" style={fillMode ? { display: "flex", flexDirection: "column", minHeight: 0 } : undefined}>
      <ChartToolbar
        nav={nav}
        chart={() => chart.current}
        following={following}
        onFitY={effectiveY === "auto" ? undefined : () => setYFit(wantedY)}
        yLabel={unit}
        onExpand={() => setOpen(!open)}
        expanded={open}
        onDownload={(format) =>
          // Everything held, not the thinned traces the canvas draws.
          saveTable(title ?? series.map((s) => s.label).join("-") ?? "chart", table(), format)
        }
        exportHref={exportHref}
      />
      {plot}
    </div>
  ) : (
    plot
  );
  if (!open) return body;
  const heading = title ?? `${series.map((s) => s.label).join(", ")}${unit ? ` (${unit})` : ""}`;
  return (
    <>
      <div className="fb-chart fb-chart-placeholder" style={{ height: typeof height === "number" ? height : 200 }} onClick={close} title="Showing full-size">
        <span className="fb-muted">full-size · Esc to return</span>
      </div>
      <ChartOverlay title={heading} onClose={close}>
        {body}
      </ChartOverlay>
    </>
  );
}
