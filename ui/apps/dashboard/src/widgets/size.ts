import { useEffect, useRef, useState, type RefObject } from "react";
import { GRID_MARGIN } from "../dashboard/document.js";

/** The pixels inside a tile's body for a widget `h` rows tall: the rows and the gaps between them, less the header row and the padding. */
export const bodyPx = (h: number, rowHeight: number, header: boolean) => h * rowHeight + (h - 1) * GRID_MARGIN[1] - (header ? 32 : 0) - 24 - 2;

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
    const measure = () => {
      const legends = el.querySelectorAll<HTMLElement>(".u-legend");
      const legend = legends.length ? legends[legends.length - 1]!.offsetHeight : 0;
      const titles = [...el.querySelectorAll<HTMLElement>(".fb-chart-title")].reduce((s, t) => s + t.offsetHeight, 0);
      const h = Math.max(min, Math.floor(el.clientHeight - legend - titles - 4));
      if (Math.abs(h - last.current) >= 2) {
        last.current = h;
        setHeight(h);
      }
    };
    const resize = new ResizeObserver(measure);
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
