/**
 * Dashboards: what the UI shows and how, saved per rig under a name with a
 * version history (`/api/dashboards`). The server validates the outline --
 * grid, widgets with a position -- and leaves each widget's `kind` and
 * `config` to the UI, so the widget catalogue can grow without a release.
 */

import type { Nanoseconds } from "./wire.js";

export interface DashboardGrid {
  /** Columns across: 24, or 12 for a document saved before the 24-column grid (scaled on read). */
  cols: number;
  /** Pixels per grid row. */
  row_height: number;
}

/** One tile on the grid. Position and size are in grid units. `config` is the kind's own. */
export interface DashboardWidget {
  id: string;
  kind: string;
  title?: string | null;
  x: number;
  y: number;
  w: number;
  h: number;
  config: Record<string, unknown>;
}

/** The document a dashboard is saved as; `extra` fields are refused at every level. */
export interface DashboardDocument {
  schema_version: number;
  /** The key it is saved under. */
  name: string;
  /** Which rig it was made for. */
  rig: string;
  description?: string | null;
  grid: DashboardGrid;
  widgets: DashboardWidget[];
}

/**
 * A widget naming a channel, loop or actuator the rig does not have (DESIGN-SPEC.md §4.8).
 * The document is kept and returned anyway; the widget renders the "unbound" state instead of
 * being dropped.
 */
export interface DashboardProblem {
  widget_id: string;
  ref: string;
  reason: string;
}

/** A stored version: the document plus when it was saved and a digest of it. */
export interface DashboardRow {
  id: number;
  name: string;
  rig: string;
  body: DashboardDocument;
  created_ns: Nanoseconds;
  sha256: string;
  /** What it names that this rig lacks; empty when everything resolves. */
  problems?: DashboardProblem[];
}
