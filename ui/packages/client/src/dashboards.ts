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

/**
 * The document a dashboard is saved as; `extra` fields are refused at every
 * level. `schema_version` 2 binds by address and controller name; 3 adds
 * `readonly` and `order`; 4 names a program widget's button `cancel`. The
 * server migrates an older document on read: version 1's channels, loops and
 * actuators become addresses and names, a version-2 document is writable and
 * unordered, and a program widget's `interrupt` becomes `cancel`.
 */
export interface DashboardDocument {
  schema_version: number;
  /** The key it is saved under. */
  name: string;
  /** Which rig it was made for. */
  rig: string;
  description?: string | null;
  grid: DashboardGrid;
  widgets: DashboardWidget[];
  /**
   * Its widgets' write controls render disabled, for everyone. A convenience for
   * a wall display, not access control: whoever may save it may clear it.
   * Layout editing is unaffected. Absent in a document not yet read back from
   * the server; treat as `false`.
   */
  readonly?: boolean;
  /** Where its tab sits: ascending, then unordered ones newest first. A float, so a move rewrites one document. */
  order?: number | null;
}

/**
 * A widget whose binding -- a `readout`/`gauge`'s `address`, a `chart`'s
 * `addresses`, a `loop`'s `controller`, a `device`'s `device` -- names
 * something the rig does not have (DESIGN-SPEC.md §4.8). The document is
 * kept and returned anyway; the widget renders the "unbound" state instead
 * of being dropped.
 */
export interface DashboardProblem {
  widget_id: string;
  address: string;
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
}

/** `GET /api/dashboards/{name}` and the answer to a save: the row plus what it names that this rig lacks. */
export interface DashboardWithProblems extends DashboardRow {
  problems: DashboardProblem[];
}
