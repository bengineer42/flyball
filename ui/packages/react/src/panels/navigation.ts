/**
 * Pan, zoom and "follow live" for a time-series chart, shared by every chart.
 *
 * A chart has two states. *Following*: the x range is the scrolling window
 * (or everything, when no window is set) and every `setData` keeps the newest
 * point at the right edge. *Held*: the user zoomed or panned, so the x range
 * is theirs and new data does not move it. Wheel zooms x about the pointer,
 * Alt+wheel pans y (`onWheelY`, wired by the caller -- not Shift, which
 * uPlot's own select-to-zoom drag already owns, and not Ctrl, which a
 * trackpad pinch turns into a page-zoom gesture the browser would fight
 * over), drag on the plot pans x (shift-drag keeps uPlot's select-to-zoom),
 * and the toolbar steps back and forth, fits, and returns to following.
 * (Double-click is the chart's: it opens the chart full-size.)
 */

import type uPlot from "uplot";

export interface Navigation {
  /** True while the chart tracks the newest data. */
  following: boolean;
  /** uPlot's `scales.x.range`: the window while following, the held range otherwise. */
  xRange(u: uPlot, min: number, max: number, windowS: number | undefined): [number, number];
  /** The uPlot hooks and handlers that pan and zoom; pass through `plugins`. */
  plugin(): uPlot.Plugin;
  /** Toolbar actions. */
  follow(u: uPlot): void;
  fitX(u: uPlot): void;
  step(u: uPlot, fraction: number): void;
  zoom(u: uPlot, factor: number, about?: number): void;
  /** Called whenever `following` changes, so a toolbar can re-render. */
  onChange?: (following: boolean) => void;
  /**
   * Alt+wheel pans the y axis instead of zooming x -- set by the caller (the
   * y range is chart-specific: single scale for `TimeSeries`, one of several
   * for `MultiSeries`), a no-op until wired. `deltaY` is the wheel event's own.
   */
  onWheelY?: (u: uPlot, deltaY: number) => void;
}

/** The largest finite value in `xs` (times ascend, so it is the last finite one), or null. */
function lastFinite(xs: ArrayLike<number> | undefined): number | null {
  if (!xs) return null;
  for (let i = xs.length - 1; i >= 0; i--) if (Number.isFinite(xs[i])) return xs[i]!;
  return null;
}

export function navigation(): Navigation {
  let held: [number, number] | null = null;

  const nav: Navigation = {
    following: true,

    xRange(_u, min, max, windowS) {
      if (held) return held;
      if (!windowS) return [min, max];
      // The newest point, from the data itself: with a single point (or all points at one time) uPlot
      // pads a time scale's max far ahead -- a thousand days -- and a window hung from that max shows
      // an empty chart dated years ahead. The data's own last time is what "live" means.
      const newest = lastFinite(_u.data[0] as ArrayLike<number> | undefined);
      if (newest !== null) max = newest;
      // No points yet: a window ending now, so an empty chart is not labelled with whatever uPlot makes of NaN.
      if (!Number.isFinite(max)) return [Date.now() / 1000 - windowS, Date.now() / 1000];
      // Live follows the right edge (UI.md B8): the newest point sits at the right and the window
      // reaches back from it, empty on the left until there is that much history -- never a sliver
      // at the left with an empty window ahead of it.
      return [max - windowS, max];
    },

    plugin() {
      let dragging: { x: number; min: number; max: number } | null = null;
      return {
        hooks: {
          ready(u) {
            const over = u.over;
            over.addEventListener(
              "wheel",
              (e) => {
                if (!e.deltaY) return;
                e.preventDefault();
                if (e.altKey) {
                  nav.onWheelY?.(u, e.deltaY);
                  return;
                }
                const { left } = u.cursor;
                const at = u.posToVal(left ?? u.bbox.width / 2 / devicePixelRatio, "x");
                nav.zoom(u, e.deltaY < 0 ? 0.8 : 1.25, at);
              },
              { passive: false },
            );
            over.addEventListener("mousedown", (e) => {
              if (e.button !== 0 || e.shiftKey) return; // shift-drag: uPlot's own select-to-zoom
              const [min, max] = current(u);
              dragging = { x: e.clientX, min, max };
              e.preventDefault();
            });
            const move = (e: MouseEvent) => {
              if (!dragging) return;
              const perPx = (dragging.max - dragging.min) / (u.bbox.width / devicePixelRatio);
              const dx = (e.clientX - dragging.x) * perPx;
              hold(u, [dragging.min - dx, dragging.max - dx]);
            };
            const up = () => (dragging = null);
            window.addEventListener("mousemove", move);
            window.addEventListener("mouseup", up);
          },
          setSelect(u) {
            // uPlot's select-to-zoom (shift-drag) sets the scale itself; record it as held.
            if (u.select.width > 0) {
              held = [u.posToVal(u.select.left, "x"), u.posToVal(u.select.left + u.select.width, "x")];
              set(false);
            }
          },
        },
      };
    },

    follow(u) {
      held = null;
      set(true);
      u.redraw(false);
      u.setScale("x", { min: u.data[0][0] ?? 0, max: u.data[0][u.data[0].length - 1] ?? 1 });
    },

    fitX(u) {
      const t = u.data[0];
      if (!t.length) return;
      hold(u, [t[0]!, t[t.length - 1]!]);
    },

    step(u, fraction) {
      const [min, max] = current(u);
      const span = max - min;
      hold(u, [min + span * fraction, max + span * fraction]);
    },

    zoom(u, factor, about?: number) {
      const [min, max] = current(u);
      const centre = about ?? (min + max) / 2;
      hold(u, [centre - (centre - min) * factor, centre + (max - centre) * factor]);
    },
  };

  function current(u: uPlot): [number, number] {
    const x = u.scales["x"];
    return [x?.min ?? 0, x?.max ?? 1];
  }

  function hold(u: uPlot, range: [number, number]) {
    held = range;
    set(false);
    u.setScale("x", { min: range[0], max: range[1] });
  }

  function set(following: boolean) {
    if (nav.following !== following) {
      nav.following = following;
      nav.onChange?.(following);
    }
  }

  return nav;
}
