import { useEffect, useRef, useState } from "react";
import uPlot from "uplot";
import type { ChannelOut } from "@flyball/client";

/** Canvas cannot resolve CSS variables, so read the palette off the element and pass real colours. */
export function chartPalette(el: Element): { accent: string; fg: string; muted: string; border: string } {
  const css = getComputedStyle(el);
  const read = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    accent: read("--fb-accent", "#2557a7"),
    fg: read("--fb-fg", "#1b1b1b"),
    muted: read("--fb-muted", "#6b6b6b"),
    border: read("--fb-border", "#d8d8d8"),
  };
}

/** Bumps when the document's root style/class/data-theme changes, so charts rebuild with the new palette. */
export function useThemeVersion(): number {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const observer = new MutationObserver(() => setVersion((v) => v + 1));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["style", "class", "data-theme"] });
    return () => observer.disconnect();
  }, []);
  return version;
}

export interface TimeSeriesProps {
  channel: ChannelOut;
  /** Seconds since the epoch, ascending. */
  t: number[];
  v: number[];
  height?: number;
  /** Fix the y axis to the channel's declared range rather than autoscaling. */
  fixedRange?: boolean;
  /** No axes, no legend: a sparkline. */
  compact?: boolean;
  /**
   * Seconds of x axis to show. The trace grows from the left until it fills
   * the window, then scrolls: the newest point stays at the right edge.
   * Omit to autoscale over everything held.
   */
  windowS?: number;
}

/**
 * One channel over time. A thin wrapper over uPlot: the chart is built once
 * per channel and fed new data on every render, so a live trace at 10 Hz
 * costs a `setData`, not a rebuild. Axis label and unit come from the channel.
 */
export function TimeSeries({ channel, t, v, height = 160, fixedRange = false, compact = false, windowS }: TimeSeriesProps) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<uPlot | null>(null);
  const theme = useThemeVersion();

  useEffect(() => {
    if (!host.current) return;
    const el = host.current;
    const palette = chartPalette(el);
    const axis = (extra: uPlot.Axis): uPlot.Axis => ({
      stroke: palette.muted,
      grid: { stroke: palette.border, width: 1 },
      ticks: { stroke: palette.border, width: 1 },
      ...extra,
    });
    const options: uPlot.Options = {
      width: el.clientWidth || 400,
      height,
      series: [
        {},
        {
          label: channel.label,
          stroke: palette.accent,
          width: 1.5,
          value: (_u, raw) => (raw == null ? "—" : `${raw.toFixed(channel.precision ?? 2)} ${channel.unit}`),
        },
      ],
      axes: compact
        ? [{ show: false }, { show: false }]
        : [
            axis({ label: "time" }),
            axis({
              label: `${channel.label} (${channel.unit})`,
              size: 64,
              values: (_u, ticks) => ticks.map((x) => x.toFixed(channel.precision != null ? Math.min(channel.precision, 2) : 1)),
            }),
          ],
      scales: {
        x: {
          time: true,
          // Scrolling window: anchor the right edge on the newest point once the
          // window has filled; before that, grow from the first point.
          range: (_u, min, max) => {
            if (!windowS) return [min, max];
            const span = max - min;
            return span < windowS ? [min, min + windowS] : [max - windowS, max];
          },
        },
        y: fixedRange && channel.range ? { range: () => channel.range as [number, number] } : {},
      },
      legend: { show: !compact },
      cursor: compact ? { show: false } : { drag: { x: true, y: false } },
      padding: compact ? [4, 4, 4, 4] : undefined,
    };
    chart.current = new uPlot(options, [[], []], el);
    (el as HTMLDivElement & { uplot?: uPlot }).uplot = chart.current; // for tests and devtools
    chart.current.setData([latest.current.t, latest.current.v]);
    const resize = new ResizeObserver(() => chart.current?.setSize({ width: el.clientWidth, height }));
    resize.observe(el);
    return () => {
      resize.disconnect();
      chart.current?.destroy();
      chart.current = null;
    };
  }, [channel, height, fixedRange, compact, windowS, theme]);

  const latest = useRef({ t, v });
  latest.current = { t, v };
  useEffect(() => {
    chart.current?.setData([t, v]);
  }, [t, v]);

  return <div ref={host} className="fb-chart" />;
}
