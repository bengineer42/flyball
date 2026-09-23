/**
 * The dashboard document as the UI works on it: helpers that make, copy and
 * compare documents. The server validates the outline (see the client's
 * `DashboardDocument`); the widget kinds and their `config` are `../widgets`.
 */
import type { DashboardDocument, DashboardWidget } from "@flyball/client";

/** Version 2 binds by address (`address`, `addresses`), controller name (`controller`) and device name (`device`); 3 adds `readonly` and `order`. The server migrates older versions on read. */
export const SCHEMA_VERSION = 3;
export const DEFAULT_GRID = { cols: 24, row_height: 24 } as const;
/** Pixels between tiles, both ways: the app's own gutter. */
export const GRID_MARGIN: readonly [number, number] = [12, 12];

/** A widget id: its kind and a short random tail, unique enough within one document. */
export const newId = (kind: string) => `${kind}-${Math.random().toString(36).slice(2, 8)}`;

/** An empty document for `rig`, under `name`. */
export function emptyDocument(name: string, rig: string): DashboardDocument {
  return { schema_version: SCHEMA_VERSION, name, rig, description: null, grid: { ...DEFAULT_GRID }, widgets: [], readonly: false, order: null };
}

/**
 * A document with every optional field filled in and its widgets' positions
 * whole numbers. The grid is 24 columns; a document saved under the old
 * 12-column scheme is doubled on the x axis (x and w) so its tiles land on
 * the same fraction of the row -- `y`/`h` are already in row units and need
 * no change.
 */
export function normalise(doc: DashboardDocument): DashboardDocument {
  const cols = doc.grid?.cols ?? DEFAULT_GRID.cols;
  const scale = cols === 12 ? 2 : 1;
  return {
    schema_version: doc.schema_version ?? SCHEMA_VERSION,
    name: doc.name,
    rig: doc.rig,
    description: doc.description ?? null,
    readonly: doc.readonly ?? false,
    order: doc.order ?? null,
    grid: { cols: cols === 12 ? DEFAULT_GRID.cols : cols, row_height: doc.grid?.row_height ?? DEFAULT_GRID.row_height },
    widgets: (doc.widgets ?? []).map((w) => ({
      id: w.id,
      kind: w.kind,
      title: w.title ?? null,
      x: Math.max(0, Math.round(w.x) * scale),
      y: Math.max(0, Math.round(w.y)),
      w: Math.max(1, Math.round(w.w) * scale),
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
