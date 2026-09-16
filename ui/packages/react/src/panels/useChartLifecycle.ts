import { useCallback, useEffect, useRef, type RefObject } from "react";
import type { TraceRef } from "../store/hooks.js";
import { countRedraw } from "../store/debug.js";

export interface ChartLifecycleOptions {
  /** The element the chart is drawn into; drawing stops while it is off screen. */
  host: RefObject<HTMLElement | null>;
  /** What to draw from; omit for a chart fed by props. */
  source?: TraceRef;
  /** Draws from the store (or from what the caller holds) into the canvas: a `setData`. */
  draw(): void;
  /** Least milliseconds between two draws of a store-fed chart: 100 for a chart, 500 for a sparkline. */
  everyMs?: number;
  /** Hold every draw: the editor is dragging, say. One draw follows when released. */
  paused?: boolean;
  /** `IntersectionObserver` margin: how far off screen still counts as visible. */
  rootMargin?: string;
  /** Names this chart in `window.__fb.chartsById`. */
  id?: string;
}

/**
 * When a chart may draw: on screen (`IntersectionObserver`), in a tab the
 * viewer is looking at (`visibilitychange`), and not paused. A store-fed
 * chart subscribes while it may draw and lets go otherwise; what it missed
 * is in the rings, and one draw follows the moment it may draw again. A
 * prop-fed chart calls `redraw()` when its data changed and gets the same
 * treatment. Nothing here sets React state: no re-render, only the canvas.
 */
export function useChartLifecycle({ host, source, draw, everyMs = 100, paused = false, rootMargin = "200px", id }: ChartLifecycleOptions): { redraw(): void } {
  const drawRef = useRef(draw);
  drawRef.current = draw;
  // Shared with the effect below: whether the chart may draw now, and whether a draw is owed.
  const state = useRef({ may: false, owed: false });
  const label = id ?? source?.keys.join(",") ?? "chart";

  const push = useCallback(() => {
    state.current.owed = false;
    countRedraw(label);
    drawRef.current();
  }, [label]);

  useEffect(() => {
    const el = host.current;
    let onScreen = true;
    let shown = typeof document === "undefined" || document.visibilityState !== "hidden";
    let unsubscribe: (() => void) | null = null;
    const update = () => {
      const may = onScreen && shown && !paused;
      state.current.may = may;
      if (may && source && source.keys.length && !unsubscribe) {
        unsubscribe = source.store.subscribeTrace(source.keys, push, everyMs);
        push(); // what arrived while it could not draw
      } else if (!may && unsubscribe) {
        unsubscribe();
        unsubscribe = null;
      } else if (may && !source && state.current.owed) push();
    };
    let observer: IntersectionObserver | null = null;
    if (el && typeof IntersectionObserver !== "undefined") {
      // Wait for the observer's first word rather than assume on screen: a chart
      // built below the fold must not draw once only to stop.
      onScreen = false;
      observer = new IntersectionObserver(
        ([entry]) => {
          onScreen = entry?.isIntersecting ?? true;
          update();
        },
        { rootMargin },
      );
      observer.observe(el);
    }
    const onVisibility = () => {
      shown = document.visibilityState !== "hidden";
      update();
    };
    document.addEventListener("visibilitychange", onVisibility);
    update();
    return () => {
      observer?.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
      unsubscribe?.();
      state.current.may = false;
    };
  }, [host, source, everyMs, paused, rootMargin, push]);

  const redraw = useCallback(() => {
    if (state.current.may) push();
    else state.current.owed = true;
  }, [push]);

  return { redraw };
}
