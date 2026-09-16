/**
 * The dashboard document as the UI works on it: helpers that make, copy and
 * compare documents. The server validates the outline (see the client's
 * `DashboardDocument`); the widget kinds and their `config` are `../widgets`.
 */
import type { DashboardDocument, DashboardWidget } from "@flyball/client";

export const SCHEMA_VERSION = 1;
export const DEFAULT_GRID = { cols: 12, row_height: 40 } as const;
/** Pixels between tiles, both ways: the app's own gutter. */
export const GRID_MARGIN: readonly [number, number] = [16, 16];

/** A widget id: its kind and a short random tail, unique enough within one document. */
export const newId = (kind: string) => `${kind}-${Math.random().toString(36).slice(2, 8)}`;

/** An empty document for `rig`, under `name`. */
export function emptyDocument(name: string, rig: string): DashboardDocument {
  return { schema_version: SCHEMA_VERSION, name, rig, description: null, grid: { ...DEFAULT_GRID }, widgets: [] };
}

/** A document with every optional field filled in and its widgets' positions whole numbers. */
export function normalise(doc: DashboardDocument): DashboardDocument {
  return {
    schema_version: doc.schema_version ?? SCHEMA_VERSION,
    name: doc.name,
    rig: doc.rig,
    description: doc.description ?? null,
    grid: { cols: doc.grid?.cols ?? DEFAULT_GRID.cols, row_height: doc.grid?.row_height ?? DEFAULT_GRID.row_height },
    widgets: (doc.widgets ?? []).map((w) => ({
      id: w.id,
      kind: w.kind,
      title: w.title ?? null,
      x: Math.max(0, Math.round(w.x)),
      y: Math.max(0, Math.round(w.y)),
      w: Math.max(1, Math.round(w.w)),
      h: Math.max(1, Math.round(w.h)),
      config: w.config ?? {},
    })),
  };
}

/** Deep-equal for two documents, positions included: what "unsaved changes" means. */
export const sameDocument = (a: DashboardDocument | null, b: DashboardDocument | null) =>
  a === b || (a !== null && b !== null && JSON.stringify(normalise(a)) === JSON.stringify(normalise(b)));

/** The row below every widget: where a new one lands. */
export const bottomOf = (widgets: DashboardWidget[]) => widgets.reduce((y, w) => Math.max(y, w.y + w.h), 0);

/** A copy of `widget` under a new id, placed at the bottom. */
export function duplicateWidget(widget: DashboardWidget, widgets: DashboardWidget[]): DashboardWidget {
  return { ...widget, id: newId(widget.kind), config: JSON.parse(JSON.stringify(widget.config)) as Record<string, unknown>, x: widget.x, y: bottomOf(widgets) };
}

/** The document as a pretty JSON file, without the fields the server sets on import (`name`, `rig`). */
export function exportJson(doc: DashboardDocument, keepIdentity = true): string {
  const { name, rig, ...rest } = normalise(doc);
  return JSON.stringify(keepIdentity ? { name, rig, ...rest } : rest, null, 2) + "\n";
}
