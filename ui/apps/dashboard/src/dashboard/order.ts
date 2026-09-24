/**
 * Moving a dashboard's tab: which documents get a new `order`. A dashboard's place is its
 * document's `order` (schema 3), ascending; one without an order sorts after, newest first
 * (`byTabOrder`). A move writes as few documents as it can: once every dashboard has an order,
 * the moved one alone gets the midpoint of its new neighbours. Before that -- the first move on
 * a rig, or after a new dashboard arrives without one -- every dashboard is numbered in its
 * current place, so the order it already had is kept rather than reshuffled.
 */
import type { DashboardRow, RigClient } from "@flyball/client";

export interface OrderChange {
  name: string;
  order: number;
}

/** `rows` in tab order; the dashboard at `from` moved to `to`. The documents whose `order` changes, and to what. */
export function reorder(rows: readonly DashboardRow[], from: number, to: number): OrderChange[] {
  if (from === to || from < 0 || to < 0 || from >= rows.length || to >= rows.length) return [];
  const moved = rows.slice();
  const [row] = moved.splice(from, 1);
  moved.splice(to, 0, row!);
  const orderOf = (r: DashboardRow | undefined) => r?.body?.order ?? null;
  if (moved.every((r) => orderOf(r) !== null)) {
    const before = orderOf(moved[to - 1]);
    const after = orderOf(moved[to + 1]);
    const order = before === null ? after! - 1 : after === null ? before + 1 : (before + after) / 2;
    // Two neighbours so close that their midpoint is one of them: renumber instead (below).
    if (order !== before && order !== after) return [{ name: row!.name, order }];
  }
  return moved.flatMap((r, i) => (orderOf(r) === i + 1 ? [] : [{ name: r.name, order: i + 1 }]));
}

/** Saves each change as a new version of that dashboard: its newest document with the new `order`. */
export async function saveOrder(rig: Pick<RigClient, "saveDashboard">, rows: readonly DashboardRow[], changes: readonly OrderChange[]): Promise<void> {
  for (const change of changes) {
    const row = rows.find((r) => r.name === change.name);
    if (row) await rig.saveDashboard(change.name, { ...row.body, order: change.order });
  }
}
