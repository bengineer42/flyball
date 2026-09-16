/**
 * Counters a measurement script reads from outside the page (`window.__fb`),
 * never application code: React commits of the app root, `setData` calls per
 * chart. Cheap enough to leave in production builds; absent outside a browser.
 */
export interface DebugCounters {
  /** `setData` calls, every chart. */
  charts: number;
  /** `setData` calls by chart id (a widget id, or `source.measurand`). */
  chartsById: Record<string, number>;
  /** Renders of the app root. */
  renders: number;
  /** Renders by component name, for pages that opt in. */
  rendersById: Record<string, number>;
  /** Messages folded into the store, by stream. */
  messages: Record<string, number>;
}

declare global {
  interface Window {
    __fb?: DebugCounters;
  }
}

export function debugCounters(): DebugCounters {
  const w = typeof window === "undefined" ? undefined : window;
  if (!w) return { charts: 0, chartsById: {}, renders: 0, rendersById: {}, messages: {} };
  w.__fb ??= { charts: 0, chartsById: {}, renders: 0, rendersById: {}, messages: {} };
  return w.__fb;
}

export function countRender(id: string): void {
  const c = debugCounters();
  if (id === "App") c.renders++;
  c.rendersById[id] = (c.rendersById[id] ?? 0) + 1;
}

export function countRedraw(id: string): void {
  const c = debugCounters();
  c.charts++;
  c.chartsById[id] = (c.chartsById[id] ?? 0) + 1;
}
