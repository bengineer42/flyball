import { useCallback, useEffect, useRef, useState } from "react";
import uPlot from "uplot";
import type { ChannelOut } from "@flyball/client";
import { yRange, type YScale } from "./yscale.js";
import { thin } from "./thin.js";
import { navigation } from "./navigation.js";
import { ChartToolbar } from "./ChartToolbar.js";
import { ChartOverlay, plotHeight } from "./ChartOverlay.js";
import { saveTable, seriesTable } from "./download.js";

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
  /** Plot height in pixels, or `"auto"`: follows the width (0.3 of it, between 160 and 360). */
  height?: number | "auto";
  /** Fix the y axis to the channel's declared range rather than autoscaling. Shorthand for `yScale="range"`. */
  fixedRange?: boolean;
  /** How to scale the y axis: fit the data, the channel's range, or fixed bounds. */
  yScale?: YScale;
  /** No axes, no legend: a sparkline. */
  compact?: boolean;
  /**
   * Seconds of x axis to show. The trace grows from the left until it fills
   * the window, then scrolls: the newest point stays at the right edge.
   * Omit to autoscale over everything held.
   */
  windowS?: number;
  /** Draw one point in `every`; the newest is always kept. */
  every?: number;
  /** Pan/zoom toolbar and wheel/drag navigation; on by default for full charts, never for sparklines. */
  navigable?: boolean;
  /** Heading of the full-size view; default `source.measurand`. */
  title?: string;
  /**
   * Shown full-size in an overlay (a sparkline opens as a full chart). Uncontrolled
   * unless given: the toolbar button, a double-click on the plot, or a click on a
   * sparkline opens it; Escape, the close button or the backdrop closes it.
   */
  expanded?: boolean;
  onExpandChange?(expanded: boolean): void;
  /** The same channel in the store, as an export URL; the toolbar's download menu offers it beside what is held here. */
  exportHref?: string;
}

/**
 * One channel over time. A thin wrapper over uPlot: the chart is built once
 * per channel and fed new data on every render, so a live trace at 10 Hz
 * costs a `setData`, not a rebuild. Axis label and unit come from the channel.
 */
export function TimeSeries({ channel, t, v, height = 160, fixedRange = false, compact: compactProp = false, windowS, yScale, every, navigable, title, expanded, onExpandChange, exportHref }: TimeSeriesProps) {
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
  // Full-size, a sparkline becomes a real chart: axes, legend, toolbar.
  const compact = compactProp && !open;
  const interactive = navigable ?? !compact;
  // "fit y" on the toolbar autoscales until the caller's y scale changes again.
  const [yFit, setYFit] = useState<YScale | null>(null);
  const wanted: YScale = yScale ?? (fixedRange ? "range" : "auto");
  const effective = yFit !== null && yFit === wanted ? "auto" : wanted;
  const y = yRange(effective, channel.range);
  const yKey = typeof effective === "object" ? `${effective.min}:${effective.max}` : effective;
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
      height: plotHeight(el, height, open),
      series: [
        {},
        {
          label: channel.label,
          stroke: palette.accent,
          points: { show: false },
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
          range: (u, min, max) => nav.xRange(u, min, max, windowS),
        },
        y: y ? { range: y } : {},
      },
      legend: { show: !compact },
      cursor: compact ? { show: false } : { drag: { x: true, y: false } },
      padding: compact ? [4, 4, 4, 4] : undefined,
      plugins: interactive ? [nav.plugin()] : [],
    };
    chart.current = new uPlot(options, [[], []], el);
    (el as HTMLDivElement & { uplot?: uPlot }).uplot = chart.current; // for tests and devtools
    chart.current.setData([latest.current.t, latest.current.v]);
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
  }, [channel, height, yKey, compact, windowS, theme, interactive, open]);

  const latest = useRef({ t: thin(t, every), v: thin(v, every) });
  latest.current = { t: thin(t, every), v: thin(v, every) };
  useEffect(() => {
    chart.current?.setData([latest.current.t, latest.current.v]);
  }, [t, v, every]);

  // A sparkline opens on a click, so its double-click must not toggle straight back.
  const plot = <div ref={host} className={`fb-chart${open ? " fb-chart-fill" : ""}`} onDoubleClick={compact ? undefined : () => setOpen(!open)} />;
  const body = interactive ? (
    <div className="fb-chart-wrap">
      <ChartToolbar
        nav={nav}
        chart={() => chart.current}
        following={following}
        onFitY={effective === "auto" ? undefined : () => setYFit(wanted)}
        yLabel={channel.unit || channel.label}
        onExpand={() => setOpen(!open)}
        expanded={open}
        onDownload={(format) =>
          // Everything held, not the thinned trace the canvas draws.
          saveTable(`${channel.source}.${channel.measurand}`, seriesTable([{ label: `${channel.source}.${channel.measurand}`, unit: channel.unit, t, v }]), format)
        }
        exportHref={exportHref}
      />
      {plot}
    </div>
  ) : compact ? (
    // A sparkline: the whole thing is a button to the full chart.
    <div className="fb-chart-wrap fb-chart-compact" onClick={() => setOpen(true)} title="Open the full chart">
      {plot}
    </div>
  ) : (
    plot
  );
  if (!open) return body;
  return (
    <>
      <div className="fb-chart fb-chart-placeholder" style={{ height: typeof height === "number" ? height : 160 }} onClick={close} title="Showing full-size">
        <span className="fb-muted">full-size · Esc to return</span>
      </div>
      <ChartOverlay title={title ?? `${channel.source}.${channel.measurand}`} onClose={close}>
        {body}
      </ChartOverlay>
    </>
  );
}
