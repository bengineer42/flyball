import { useCallback, useEffect, useRef, useState } from "react";
import uPlot from "uplot";
import { yRange, type YScale } from "./yscale.js";
import { thin } from "./thin.js";
import { navigation } from "./navigation.js";
import { ChartToolbar } from "./ChartToolbar.js";
import { ChartOverlay, plotHeight } from "./ChartOverlay.js";
import { saveTable, seriesTable } from "./download.js";

export interface MultiSeriesTrace {
  label: string;
  /** Unit of this trace. A trace whose unit differs from the chart's `unit` is drawn against its own y axis on the right. */
  unit?: string;
  /** Seconds since the epoch, ascending. Traces need not share the same times. */
  t: number[];
  v: (number | null)[];
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
  series: MultiSeriesTrace[];
  /** Unit of the left y axis; traces in any other unit get a right axis. */
  unit?: string;
  /** Plot height in pixels, or `"auto"`: follows the width (0.3 of it, between 160 and 360). */
  height?: number | "auto";
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

const FALLBACK = ["#2557a7", "#c2410c", "#15803d", "#7e22ce", "#b45309", "#0e7490"];
const DASH = [6, 4];

/** Scale key for a trace: the chart's own unit (or none) shares `y`; anything else gets a scale named by its unit. */
const scaleOf = (trace: MultiSeriesTrace, unit: string | undefined) =>
  trace.unit === undefined || trace.unit === unit ? "y" : `y:${trace.unit}`;

/** Align traces with different time bases onto one x array, nulls where a trace has no point. */
function align(series: MultiSeriesTrace[]): uPlot.AlignedData {
  if (series.length === 0) return [[]];
  const shared = series.every((s) => s.t === series[0]!.t);
  if (shared) return [series[0]!.t, ...series.map((s) => s.v)] as uPlot.AlignedData;
  return uPlot.join(series.map((s) => [s.t, s.v] as uPlot.AlignedData));
}

/**
 * Several traces on one uPlot instance sharing the x axis; the chart is
 * built once per set of trace labels, units and styles, and fed new data on
 * every render. A trace in a unit other than the chart's is drawn against a
 * second y axis on the right, so a demand in watts can sit over a reading in
 * degrees. Legend and cursor on.
 */
export function MultiSeries({ series, unit, height = 200, windowS , yScale, range , every , navigable = true, title, expanded, onExpandChange, exportHref }: MultiSeriesProps) {
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

  useEffect(() => {
    if (!host.current) return;
    const el = host.current;
    const style = getComputedStyle(el);
    const palette = FALLBACK.map((fallback, i) => style.getPropertyValue(`--fb-series-${i + 1}`).trim() || fallback);
    const fg = style.getPropertyValue("--fb-muted").trim() || "#6b6b6b";

    const scales: uPlot.Scales = {
      x: {
        time: true,
        range: (u, min, max) => nav.xRange(u, min, max, windowS),
      },
      y: yRange(effectiveY, range) ? { range: yRange(effectiveY, range)! } : {},
    };
    const axes: uPlot.Axis[] = [
      { label: "time", stroke: fg },
      { label: unit ? `(${unit})` : undefined, size: 64, scale: "y", stroke: fg },
    ];
    const plotted: uPlot.Series[] = [{}];
    series.forEach((s, i) => {
      const scale = scaleOf(s, unit);
      if (!(scale in scales)) {
        scales[scale] = {};
        axes.push({ label: `(${s.unit})`, size: 64, scale, side: 1, grid: { show: false }, stroke: fg });
      }
      const precision = s.precision ?? 2;
      const suffix = s.unit ? ` ${s.unit}` : "";
      const line: uPlot.Series = {
        label: s.label,
        scale,
        stroke: s.color ?? palette[i % palette.length],
        width: s.width ?? 1.5,
        spanGaps: true,
        points: { show: false }, // a thinned or sparse trace stays a line, not a row of dots
        value: (_u, raw) => (raw == null ? "—" : `${raw.toFixed(precision)}${suffix}`),
      };
      if (s.dash) line.dash = Array.isArray(s.dash) ? s.dash : DASH;
      plotted.push(line);
    });

    const options: uPlot.Options = {
      width: el.clientWidth || 400,
      height: plotHeight(el, height, open),
      series: plotted,
      axes,
      scales,
      legend: { show: true },
      cursor: { drag: { x: true, y: false } },
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
      const h = plotHeight(el, height, open);
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
  }, [shape, unit, height, windowS, yKey, navigable, open]);

  useEffect(() => {
    const thinned = every && every > 1 ? series.map((s) => ({ ...s, t: thin(s.t, every), v: thin(s.v, every) })) : series;
    latest.current = align(thinned);
    chart.current?.setData(latest.current);
  }, [series, every]);

  const plot = <div ref={host} className={`fb-chart fb-multi${open ? " fb-chart-fill" : ""}`} onDoubleClick={() => setOpen(!open)} />;
  const body = navigable ? (
    <div className="fb-chart-wrap">
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
          saveTable(title ?? series.map((s) => s.label).join("-") ?? "chart", seriesTable(series.map((s) => ({ label: s.label, unit: s.unit ?? unit, t: s.t, v: s.v }))), format)
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
