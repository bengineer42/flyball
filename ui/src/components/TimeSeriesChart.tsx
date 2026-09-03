/**
 * One chart, one axis.
 *
 * Generic over the row type so the same component draws humidity, temperature
 * and flow without any of them sharing a y-scale — two measures of different
 * units get two charts, never two axes. Series values are `number | null`:
 * a null is a gap, so an absent dry sensor leaves a hole rather than a line
 * dropping to zero.
 *
 * Identity is never colour alone: every series is in the legend, the last
 * point of each is directly labelled, and the table view carries the numbers
 * for anyone the colours do not serve.
 */

import { useEffect, useMemo, useRef, useState } from "react";

const PADDING = { top: 12, right: 62, bottom: 22, left: 44 };
const MIN_SPAN = 1e-6;

export interface SeriesSpec<T> {
  key: string;
  label: string;
  colour: string;
  /** Dashed marks a demand or a target, as opposed to a measurement. */
  dashed?: boolean;
  value(datum: T): number | null;
}

export interface TimeSeriesChartProps<T> {
  data: ReadonlyArray<T>;
  x(datum: T): number;
  series: ReadonlyArray<SeriesSpec<T>>;
  height?: number;
  /** Appended to every value in labels and the tooltip. */
  unit?: string;
  digits?: number;
  /** Forces the y range; otherwise it is taken from the data with padding. */
  yDomain?: [number, number];
  emptyMessage?: string;
}

interface Layout {
  width: number;
  height: number;
  yMin: number;
  yMax: number;
  xOf(value: number): number;
  yOf(value: number): number;
}

function niceTicks(min: number, max: number, count = 4): number[] {
  const span = Math.max(max - min, MIN_SPAN);
  const rough = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? magnitude * 10;
  const first = Math.ceil(min / step) * step;
  const ticks: number[] = [];
  for (let value = first; value <= max + MIN_SPAN; value += step) ticks.push(value);
  return ticks;
}

function formatSeconds(seconds: number): string {
  if (!Number.isFinite(seconds)) return "";
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  const secs = Math.abs(total % 60);
  return `${minutes}:${secs.toString().padStart(2, "0")}`;
}

export function TimeSeriesChart<T>({
  data,
  x,
  series,
  height = 220,
  unit,
  digits = 1,
  yDomain,
  emptyMessage = "No data yet.",
}: TimeSeriesChartProps<T>) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(720);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  const [cursor, setCursor] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const visible = useMemo(
    () => series.filter((spec) => !hidden.has(spec.key)),
    [series, hidden],
  );

  const layout = useMemo<Layout | null>(() => {
    if (data.length < 2 || width <= PADDING.left + PADDING.right) return null;

    const xs = data.map(x);
    const xMin = xs[0];
    const xMax = xs[xs.length - 1];

    let yMin = Number.POSITIVE_INFINITY;
    let yMax = Number.NEGATIVE_INFINITY;
    for (const datum of data) {
      for (const spec of visible) {
        const value = spec.value(datum);
        if (value === null || !Number.isFinite(value)) continue;
        if (value < yMin) yMin = value;
        if (value > yMax) yMax = value;
      }
    }
    if (!Number.isFinite(yMin) || !Number.isFinite(yMax)) return null;

    if (yDomain) {
      [yMin, yMax] = yDomain;
    } else {
      const pad = Math.max((yMax - yMin) * 0.12, 0.5);
      yMin -= pad;
      yMax += pad;
    }

    const plotWidth = width - PADDING.left - PADDING.right;
    const plotHeight = height - PADDING.top - PADDING.bottom;
    const xSpan = Math.max(xMax - xMin, MIN_SPAN);
    const ySpan = Math.max(yMax - yMin, MIN_SPAN);

    return {
      width,
      height,
      yMin,
      yMax,
      xOf: (value) => PADDING.left + ((value - xMin) / xSpan) * plotWidth,
      yOf: (value) => PADDING.top + plotHeight - ((value - yMin) / ySpan) * plotHeight,
    };
  }, [data, x, visible, width, height, yDomain]);

  const yTicks = useMemo(() => {
    if (!layout) return [];
    return niceTicks(layout.yMin, layout.yMax).map((value) => ({ value, y: layout.yOf(value) }));
  }, [layout]);

  const format = (value: number | null) =>
    value === null || !Number.isFinite(value)
      ? "—"
      : `${value.toFixed(digits)}${unit ? ` ${unit}` : ""}`;

  const paths = useMemo(() => {
    if (!layout) return [];
    return visible.map((spec) => {
      let path = "";
      let open = false;
      for (const datum of data) {
        const value = spec.value(datum);
        if (value === null || !Number.isFinite(value)) {
          open = false;
          continue;
        }
        const point = `${layout.xOf(x(datum)).toFixed(1)} ${layout.yOf(value).toFixed(1)}`;
        path += open ? ` L ${point}` : `${path ? " " : ""}M ${point}`;
        open = true;
      }
      // The last real point carries the direct label.
      let last: { value: number; datum: T } | null = null;
      for (let index = data.length - 1; index >= 0; index -= 1) {
        const value = spec.value(data[index]);
        if (value !== null && Number.isFinite(value)) {
          last = { value, datum: data[index] };
          break;
        }
      }
      return { spec, path, last };
    });
  }, [layout, visible, data, x]);

  /** Direct labels, nudged apart so two close series stay readable. */
  const endLabels = useMemo(() => {
    if (!layout) return [];
    const entries = paths
      .filter((entry) => entry.last !== null)
      .map((entry) => ({
        key: entry.spec.key,
        colour: entry.spec.colour,
        text: format(entry.last!.value),
        y: layout.yOf(entry.last!.value),
      }))
      .sort((a, b) => a.y - b.y);
    for (let index = 1; index < entries.length; index += 1) {
      const gap = entries[index].y - entries[index - 1].y;
      if (gap < 12) entries[index].y = entries[index - 1].y + 12;
    }
    return entries;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paths, layout, digits, unit]);

  const cursorIndex = useMemo(() => {
    if (cursor === null || !layout || data.length === 0) return null;
    let best = 0;
    let bestDistance = Number.POSITIVE_INFINITY;
    data.forEach((datum, index) => {
      const distance = Math.abs(layout.xOf(x(datum)) - cursor);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = index;
      }
    });
    return best;
  }, [cursor, layout, data, x]);

  const toggle = (key: string) =>
    setHidden((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      // Hiding the last series would empty the chart; keep one on.
      return next.size >= series.length ? current : next;
    });

  const hovered = cursorIndex !== null ? data[cursorIndex] : null;

  return (
    <div className="chart" ref={container}>
      {layout ? (
        <>
          <svg
            height={height}
            viewBox={`0 0 ${layout.width} ${height}`}
            role="img"
            aria-label={`${series.map((s) => s.label).join(", ")} against time`}
            onMouseMove={(event) => {
              const box = event.currentTarget.getBoundingClientRect();
              setCursor(((event.clientX - box.left) / box.width) * layout.width);
            }}
            onMouseLeave={() => setCursor(null)}
          >
            {yTicks.map((tick) => (
              <g key={tick.value}>
                <line
                  className="grid-line"
                  x1={PADDING.left}
                  x2={layout.width - PADDING.right}
                  y1={tick.y}
                  y2={tick.y}
                />
                <text className="axis" x={PADDING.left - 6} y={tick.y + 3} textAnchor="end">
                  {tick.value.toFixed(digits === 0 ? 0 : 1)}
                </text>
              </g>
            ))}

            <text className="axis" x={PADDING.left} y={height - 6}>
              {formatSeconds(x(data[0]))}
            </text>
            <text
              className="axis"
              x={layout.width - PADDING.right}
              y={height - 6}
              textAnchor="end"
            >
              {formatSeconds(x(data[data.length - 1]))}
            </text>

            {paths.map(({ spec, path }) => (
              <path
                key={spec.key}
                className={spec.dashed ? "series-line dashed" : "series-line"}
                d={path}
                stroke={spec.colour}
              />
            ))}

            {endLabels.map((label) => (
              <text
                key={label.key}
                className="end-label"
                x={layout.width - PADDING.right + 6}
                y={label.y + 3}
                fill={label.colour}
              >
                {label.text}
              </text>
            ))}

            {hovered !== null && cursorIndex !== null && (
              <g>
                <line
                  className="crosshair"
                  x1={layout.xOf(x(hovered))}
                  x2={layout.xOf(x(hovered))}
                  y1={PADDING.top}
                  y2={height - PADDING.bottom}
                />
                {visible.map((spec) => {
                  const value = spec.value(hovered);
                  if (value === null || !Number.isFinite(value)) return null;
                  return (
                    <circle
                      key={spec.key}
                      className="marker"
                      cx={layout.xOf(x(hovered))}
                      cy={layout.yOf(value)}
                      r={4}
                      fill={spec.colour}
                    />
                  );
                })}
              </g>
            )}
          </svg>

          {hovered !== null && (
            <div
              className="tooltip"
              style={{
                left: Math.min(
                  Math.max(layout.xOf(x(hovered)) + 10, 0),
                  Math.max(layout.width - 150, 0),
                ),
                top: PADDING.top,
              }}
            >
              <div className="t-time">t + {formatSeconds(x(hovered))}</div>
              {visible.map((spec) => (
                <div className="t-row" key={spec.key}>
                  <span className="row" style={{ gap: 5 }}>
                    <span className="swatch" style={{ background: spec.colour }} aria-hidden />
                    {spec.label}
                  </span>
                  <span className="mono">{format(spec.value(hovered))}</span>
                </div>
              ))}
            </div>
          )}
        </>
      ) : (
        <div className="empty-chart" style={{ height }}>
          {emptyMessage}
        </div>
      )}

      <div className="row" style={{ justifyContent: "space-between", marginTop: 8 }}>
        <div className="legend">
          {series.map((spec) => (
            <button
              key={spec.key}
              type="button"
              className={hidden.has(spec.key) ? "item off ghost small" : "item ghost small"}
              onClick={() => toggle(spec.key)}
              aria-pressed={!hidden.has(spec.key)}
            >
              <span className="swatch" style={{ background: spec.colour }} aria-hidden />
              {spec.label}
            </button>
          ))}
        </div>
        {data.length > 0 && (
          <button type="button" className="ghost small" onClick={() => setShowTable((v) => !v)}>
            {showTable ? "Hide table" : "Table"}
          </button>
        )}
      </div>

      {showTable && (
        <div className="scroll-x" style={{ maxHeight: 220, overflowY: "auto", marginTop: 8 }}>
          <table>
            <thead>
              <tr>
                <th>t</th>
                {series.map((spec) => (
                  <th key={spec.key}>{spec.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data
                .slice(-40)
                .reverse()
                .map((datum, index) => (
                  <tr key={index}>
                    <td className="mono">{formatSeconds(x(datum))}</td>
                    {series.map((spec) => (
                      <td key={spec.key} className="mono">
                        {format(spec.value(datum))}
                      </td>
                    ))}
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
