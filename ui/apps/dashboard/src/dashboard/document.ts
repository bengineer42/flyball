/**
 * The dashboard document as the UI works on it: helpers that make, copy and
 * compare documents. The server validates the outline (see the client's
 * `DashboardDocument`); the widget types and their `config` are `../widgets`.
 */
import { humanise, type DashboardDocument, type DashboardWidget } from "@flyball/client";

/** The document version the server takes, and the only one: it refuses any other (nothing converts an older document). */
export const SCHEMA_VERSION = 6;
export const DEFAULT_GRID = { cols: 24, row_height: 24 } as const;
/** Pixels between tiles, both ways: the app's own gutter. */
export const GRID_MARGIN: readonly [number, number] = [12, 12];

/** A widget id: its type and a short random tail, unique enough within one document. */
export const newId = (type: string) => `${type}-${Math.random().toString(36).slice(2, 8)}`;

/** An empty document for `rig`, under `name`, showing `label` (none: the name). */
export function emptyDocument(name: string, rig: string, label: string | null = null): DashboardDocument {
  return { schema_version: SCHEMA_VERSION, name, label, rig, description: null, grid: { ...DEFAULT_GRID }, widgets: [], readonly: false, order: null };
}

/**
 * What a person sees for a dashboard not yet saved (a saved one's row carries its `label`, resolved
 * by the server): its `label`, else its `name` humanised, as the server resolves a blank one.
 */
export const labelOf = (doc: Pick<DashboardDocument, "name" | "label"> | null | undefined, name = ""): string => doc?.label || humanise(doc?.name || name);

/**
 * The key a dashboard called `label` is saved under, in the canonical form (D-079): lower case,
 * accents dropped, every run of anything but a letter or digit one `_`, starting with a letter,
 * at most 64 characters (the key grammar, `^[a-z][a-z0-9_]{0,63}$`). Empty when `label` has no
 * letter.
 */
export const nameFor = (label: string): string =>
  label
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^[^a-z]+/, "")
    .slice(0, 64)
    .replace(/_+$/, "");

/** The `label` to store for what a person typed: none when it is the key itself, or what a blank label resolves to. */
export const labelFor = (typed: string, name: string): string | null => (typed === name || typed === humanise(name) ? null : typed);

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
    label: doc.label ?? null,
    rig: doc.rig,
    description: doc.description ?? null,
    readonly: doc.readonly ?? false,
    order: doc.order ?? null,
    grid: { cols: cols === 12 ? DEFAULT_GRID.cols : cols, row_height: doc.grid?.row_height ?? DEFAULT_GRID.row_height },
    widgets: (doc.widgets ?? []).map((w) => ({
      id: w.id,
      type: w.type,
      label: w.label ?? null,
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
  return { ...widget, id: newId(widget.type), config: JSON.parse(JSON.stringify(widget.config)) as Record<string, unknown>, x: widget.x, y: bottomOf(widgets) };
}

/** The document as a pretty JSON file, without the fields the server sets on import (`name`, `rig`). */
export function exportJson(doc: DashboardDocument, keepIdentity = true): string {
  const { name, rig, ...rest } = normalise(doc);
  return JSON.stringify(keepIdentity ? { name, rig, ...rest } : rest, null, 2) + "\n";
}
