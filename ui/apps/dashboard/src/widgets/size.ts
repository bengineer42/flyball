import { useEffect, useRef, useState, type RefObject } from "react";
import { GRID_MARGIN } from "../dashboard/document.js";

/** A `--fb-*` length token on the document root, in px; `fallback` when it is not set (tests, a detached tree). */
export function tokenPx(name: string, fallback: number): number {
  if (typeof document === "undefined") return fallback;
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name);
  const v = Number.parseFloat(raw);
  return Number.isFinite(v) ? v : fallback;
}

/** The gutter between tiles, both ways: `--fb-gap` (12 comfortable, 8 compact), which the view grid and RGL both use. */
export const gridGap = () => tokenPx("--fb-gap", GRID_MARGIN[1]);

/** The frame's title row: 28px (24 compact) -- `--fb-tile-head-h` is set on `.fb-tile`, so read the density on the root instead. */
export const headPx = () => (document.documentElement.dataset.density === "compact" ? 24 : 28);

/**
 * The pixels inside a tile's body for a widget `h` rows tall: the rows and
 * the gaps between them, less the 1px borders, the title row and the body's
 * padding (`--fb-space-3` each side). Every widget kind's `minSize.h` is the
 * `h` at which this holds its default content (the table in DESIGN-SPEC.md §10).
 */
export const bodyPx = (h: number, rowHeight: number, header: boolean) => h * rowHeight + (h - 1) * gridGap() - 2 - (header ? headPx() : 0) - 2 * tokenPx("--fb-space-3", 12);

/** How many `rowPx`-tall rows a body of `h` grid rows holds after `usedPx` of other content: what a list shows rather than clipping. */
export const rowsThatFit = (h: number, rowHeight: number, header: boolean, usedPx: number, rowPx: number) => Math.max(1, Math.floor((bodyPx(h, rowHeight, header) - usedPx) / rowPx));

/**
 * The plot height a chart can have inside `ref`: the element's height less
 * whatever uPlot draws under the canvas (its legend). Measured, not
 * computed, so a legend that wraps onto two rows is allowed for; a chart's
 * rebuild (a new legend) triggers a re-measure through the mutation
 * observer, and a resize of the tile through the resize observer.
 */
export function useChartHeight(ref: RefObject<HTMLElement | null>, min = 72): number {
  const [height, setHeight] = useState(160);
  const last = useRef(height);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let resize: ResizeObserver;
    const measure = () => {
      const legends = el.querySelectorAll<HTMLElement>(".u-legend");
      // The legend reflows on its own (one row once the chart has its width; two when labels wrap): watch it too, or the
      // first measurement -- taken while it is still stacked tall -- would stand and leave the plot a sliver.
      legends.forEach((l) => resize.observe(l));
      const legend = legends.length ? legends[legends.length - 1]!.offsetHeight : 0;
      const titles = [...el.querySelectorAll<HTMLElement>(".fb-chart-title")].reduce((s, t) => s + t.offsetHeight, 0);
      const h = Math.max(min, Math.floor(el.clientHeight - legend - titles - 4));
      if (Math.abs(h - last.current) >= 2) {
        last.current = h;
        setHeight(h);
      }
    };
    resize = new ResizeObserver(measure);
    resize.observe(el);
    const mutate = new MutationObserver(measure);
    mutate.observe(el, { childList: true, subtree: true });
    measure();
    return () => {
      resize.disconnect();
      mutate.disconnect();
    };
  }, [ref, min]);
  return height;
}

/** `value` while `live`; the last live value otherwise. What a chart gets while it is off screen. */
export function useFrozen<T>(value: T, live: boolean): T {
  const held = useRef(value);
  if (live) held.current = value;
  return held.current;
}
