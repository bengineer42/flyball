import { useCallback, useEffect, useRef, useState } from "react";
import uPlot from "uplot";
import { describeSignal, describeUnit, withUnit, type SignalOut, fixed } from "@flyball/client";
import { axisSize, axisValues, edgeTicks, yRange, type YScale } from "./yscale.js";
import { thin, pointCap, breakGaps } from "./thin.js";
import { navigation } from "./navigation.js";
import { showLatestInLegend } from "./legend.js";
import { ChartToolbar } from "./ChartToolbar.js";
import { ChartOverlay, plotHeight } from "./ChartOverlay.js";
import { saveTable, seriesTable } from "./download.js";
import { useChartLifecycle } from "./useChartLifecycle.js";
import { pageSyncKey } from "./MultiSeries.js";
import type { TraceRef } from "../store/hooks.js";
import { emptyTrace } from "../store/telemetry.js";

const EMPTY: number[] = [];

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
  signal: SignalOut;
  /** Seconds since the epoch, ascending. Omitted when the chart draws from a `source`. */
  t?: number[];
  v?: number[];
  /**
   * Draw the signal straight from the telemetry store (`useTraceRef`): the
   * chart subscribes itself, redraws at most ten times a second (twice for a
   * sparkline) while on screen, and never re-renders on samples.
   */
  source?: TraceRef;
  /** Hold every redraw (the editor is dragging); one follows when released. */
  paused?: boolean;
  /** `cursor.sync.key`: charts sharing a key share a cursor. Default: the page (the location hash). */
  syncKey?: string;
  /** Names this chart in the redraw counter (`window.__fb.chartsById`); default the signal's address. */
  id?: string;
  /** Plot height in pixels, or `"auto"`: follows the width (0.3 of it, between 160 and 360). */
  height?: number | "auto";
  /** Fix the y axis to the signal's declared range rather than autoscaling. Shorthand for `yScale="range"`. */
  fixedRange?: boolean;
  /** How to scale the y axis: fit the data, the signal's range, or fixed bounds. */
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
  /** Show the toolbar's "live" button. Default true; false for a closed/historical session. */
  live?: boolean;
  /** Heading of the full-size view; default the signal's address. */
  title?: string;
  /**
   * Shown full-size in an overlay (a sparkline opens as a full chart). Uncontrolled
   * unless given: the toolbar button, a double-click on the plot, or a click on a
   * sparkline opens it; Escape, the close button or the backdrop closes it.
   */
  expanded?: boolean;
  onExpandChange?(expanded: boolean): void;
  /** The same signal in the store, as an export URL; the toolbar's download menu offers it beside what is held here. */
  exportHref?: string;
}

/**
 * One signal over time. A thin wrapper over uPlot: the chart is built once
 * per signal and fed new data on every render, so a live trace at 10 Hz
 * costs a `setData`, not a rebuild. Axis label and unit come from the signal.
 */
export function TimeSeries({ signal, t: tProp, v: vProp, source, paused, syncKey, id, height = 160, fixedRange = false, compact: compactProp = false, windowS, yScale, every, navigable, live = true, title, expanded, onExpandChange, exportHref }: TimeSeriesProps) {
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
  // Alt+wheel pans y. A ref, not state: the installed scale's `range` function reads it fresh on
  // every live redraw (same trick `xRange`'s closure `held` uses), so a pan survives a live
  // chart's own `setData` calls instead of being overwritten by the next auto-fit. `yPanned` only
  // flips once, to rebuild the chart exactly once with a range function that consults the ref --
  // panning itself never rebuilds; it calls `setScale` directly (see `onWheelY`).
  const heldYRef = useRef<[number, number] | null>(null);
  const [yPanned, setYPanned] = useState(false);
  useEffect(() => {
    heldYRef.current = null;
    setYPanned(false);
  }, [wanted]);
  nav.onWheelY = (u, deltaY) => {
    const scale = u.scales["y"];
    const [min, max] = heldYRef.current ?? [scale?.min ?? 0, scale?.max ?? 1];
    const shift = (deltaY < 0 ? -0.1 : 0.1) * (max - min);
    const next: [number, number] = [min + shift, max + shift];
    heldYRef.current = next;
    u.setScale("y", { min: next[0], max: next[1] });
    if (!yPanned) setYPanned(true);
  };
  const baseY = yRange(effective, signal.range);
  const y = yPanned || baseY ? () => heldYRef.current ?? baseY?.() ?? [0, 1] : undefined;
  const yKey = `${typeof effective === "object" ? `${effective.min}:${effective.max}` : effective}:${yPanned}`;
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<uPlot | null>(null);
  const theme = useThemeVersion();
  const t = tProp ?? EMPTY;
  const v = vProp ?? EMPTY;
  const label = describeSignal(signal);

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
          label,
          stroke: palette.accent,
          points: { show: false },
          width: 1.5,
          value: (_u, raw) => (raw == null ? "—" : withUnit(fixed(raw, signal.precision ?? 2), signal.unit)),
        },
      ],
      axes: compact
        ? [{ show: false }, { show: false }]
        : [
            axis({ label: "time", space: 260 }),
            axis({
              // The unit alone: the legend already names the signal, and brackets read as a variable name.
              label: describeUnit(signal.unit) || label,
              size: axisSize,
              values: axisValues(signal.precision != null ? Math.min(signal.precision, 2) : 1),
              filter: edgeTicks,
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
      cursor: compact ? { show: false } : { drag: { x: true, y: false }, sync: { key: syncKey ?? pageSyncKey() ?? "", scales: ["x", null] } },
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
    // `label` follows `signal`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signal, height, yKey, compact, windowS, theme, interactive, open, syncKey]);

  const latest = useRef({ t: thin(t, every), v: thin(v, every) });
  if (!source) latest.current = { t: thin(t, every), v: thin(v, every) };
  // Store-fed: one view reused between draws.
  const view = useRef(emptyTrace());
  const everyRef = useRef(every);
  everyRef.current = every;
  const key = signal.address;
  // A few multiples of the signal's own poll period: normal jitter between samples never counts
  // as a gap, only real dead time (a restart, an offline reader). No known period: nothing to
  // compare a gap against, so no gap is ever drawn -- better silent than a false break.
  const maxGapS = signal.poll_s ? signal.poll_s * 3 : undefined;
  const { redraw } = useChartLifecycle({
    host,
    source,
    everyMs: compact ? 500 : 100,
    paused,
    id: id ?? key,
    draw: () => {
      const u = chart.current;
      if (source) {
        // Gap-breaking runs inside the ring, on raw rows before decimation: two bucket
        // representatives are roughly a bucket-width apart by construction, which is
        // routinely wider than the signal's own sample period once a window holds more
        // rows than the target point count, so comparing bucketed spacing against
        // `maxGapS` would flag every bucket boundary as dead time and erase the line.
        const maxPoints = pointCap(u?.width ?? host.current?.clientWidth ?? 400);
        source.store.read(key, view.current, { every: everyRef.current, maxPoints, maxGapS });
        latest.current = view.current;
        u?.setData([latest.current.t, latest.current.v]);
      } else {
        const [gt, gv] = breakGaps(latest.current.t, latest.current.v, maxGapS);
        u?.setData([gt as number[], gv as number[]]);
      }
      if (u && !compact) showLatestInLegend(u);
    },
  });
  useEffect(() => {
    if (source) return;
    redraw();
  }, [t, v, every, source, redraw]);
  /** Everything held for the download: the store's rows unthinned, or the props. */
  const held = () => (source ? source.store.read(key, emptyTrace()) : { t, v });

  // A sparkline opens on a click, so its double-click must not toggle straight back.
  const plot = <div ref={host} className={`fb-chart${open ? " fb-chart-fill" : ""}`} onDoubleClick={compact ? undefined : () => setOpen(!open)} />;
  const body = interactive ? (
    <div className="fb-chart-wrap">
      <ChartToolbar
        nav={nav}
        chart={() => chart.current}
        following={following}
        onFitY={effective === "auto" ? undefined : () => setYFit(wanted)}
        yLabel={describeUnit(signal.unit) || label}
        onExpand={() => setOpen(!open)}
        expanded={open}
        onDownload={(format) =>
          // Everything held, not the thinned trace the canvas draws.
          saveTable(signal.address, seriesTable([{ label: signal.address, unit: describeUnit(signal.unit), ...held() }]), format)
        }
        exportHref={exportHref}
        live={live}
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
      <ChartOverlay title={title ?? label} onClose={close}>
        {body}
      </ChartOverlay>
    </>
  );
}
