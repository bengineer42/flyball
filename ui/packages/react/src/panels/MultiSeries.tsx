import { useEffect, useRef } from "react";
import uPlot from "uplot";

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
  /** Decimal places in the legend. */
  precision?: number;
}

export interface MultiSeriesProps {
  series: MultiSeriesTrace[];
  /** Unit of the left y axis; traces in any other unit get a right axis. */
  unit?: string;
  height?: number;
  /**
   * Seconds of x axis to show. The traces grow from the left until they fill
   * the window, then scroll: the newest point stays at the right edge.
   * Omit to autoscale over everything held.
   */
  windowS?: number;
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
export function MultiSeries({ series, unit, height = 200, windowS }: MultiSeriesProps) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<uPlot | null>(null);
  // Rebuild only when something structural changes, not on every data tick.
  const shape = JSON.stringify(
    series.map((s) => [s.label, s.unit ?? null, s.color ?? null, s.dash ?? null, s.width ?? null, s.precision ?? null]),
  );

  useEffect(() => {
    if (!host.current) return;
    const el = host.current;
    const style = getComputedStyle(el);
    const palette = FALLBACK.map((fallback, i) => style.getPropertyValue(`--fb-series-${i + 1}`).trim() || fallback);
    const fg = style.getPropertyValue("--fb-muted").trim() || "#6b6b6b";

    const scales: uPlot.Scales = {
      x: {
        time: true,
        range: (_u, min, max) => {
          if (!windowS) return [min, max];
          const span = max - min;
          return span < windowS ? [min, min + windowS] : [max - windowS, max];
        },
      },
      y: {},
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
        value: (_u, raw) => (raw == null ? "—" : `${raw.toFixed(precision)}${suffix}`),
      };
      if (s.dash) line.dash = Array.isArray(s.dash) ? s.dash : DASH;
      plotted.push(line);
    });

    const options: uPlot.Options = {
      width: el.clientWidth || 400,
      height,
      series: plotted,
      axes,
      scales,
      legend: { show: true },
      cursor: { drag: { x: true, y: false } },
    };
    chart.current = new uPlot(options, [[], ...series.map(() => [])] as uPlot.AlignedData, el);
    (el as HTMLDivElement & { uplot?: uPlot }).uplot = chart.current; // for tests and devtools
    const resize = new ResizeObserver(() => chart.current?.setSize({ width: el.clientWidth, height }));
    resize.observe(el);
    return () => {
      resize.disconnect();
      chart.current?.destroy();
      chart.current = null;
    };
    // `shape` stands in for `series`: only its structure rebuilds the chart.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape, unit, height, windowS]);

  useEffect(() => {
    chart.current?.setData(align(series));
  }, [series]);

  return <div ref={host} className="fb-chart fb-multi" />;
}
