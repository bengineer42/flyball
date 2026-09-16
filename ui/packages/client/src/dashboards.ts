/**
 * Dashboards: what the UI shows and how, saved per rig under a name with a
 * version history (`/api/dashboards`). The server validates the outline --
 * grid, widgets with a position -- and leaves each widget's `kind` and
 * `config` to the UI, so the widget catalogue can grow without a release.
 */

import type { Nanoseconds } from "./wire.js";

export interface DashboardGrid {
  /** Columns across; 12 unless a dashboard says otherwise. */
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

/** A stored version: the document plus when it was saved and a digest of it. */
export interface DashboardRow {
  id: number;
  name: string;
  rig: string;
  body: DashboardDocument;
  created_ns: Nanoseconds;
  sha256: string;
}
