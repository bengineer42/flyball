import { describe, expect, it, vi } from "vitest";
import type { DashboardRow } from "@flyball/client";
import { reorder, saveOrder } from "../src/dashboard/order.js";

const row = (name: string, order: number | null = null): DashboardRow => ({
  id: 0,
  name,
  rig: "t",
  created_ns: 0,
  sha256: "",
  body: { schema_version: 3, name, rig: "t", grid: { cols: 24, row_height: 24 }, widgets: [], readonly: false, order },
});

describe("reorder", () => {
  it("the first move numbers every dashboard in its new place, keeping the rest where they were", () => {
    expect(reorder([row("a"), row("b"), row("c")], 2, 0)).toEqual([
      { name: "c", order: 1 },
      { name: "a", order: 2 },
      { name: "b", order: 3 },
    ]);
  });

  it("once all are ordered, only the moved one changes: the midpoint of its new neighbours", () => {
    expect(reorder([row("a", 1), row("b", 2), row("c", 3)], 2, 1)).toEqual([{ name: "c", order: 1.5 }]);
  });

  it("to the front or the back: one past the end neighbour", () => {
    expect(reorder([row("a", 1), row("b", 2), row("c", 3)], 2, 0)).toEqual([{ name: "c", order: 0 }]);
    expect(reorder([row("a", 1), row("b", 2), row("c", 3)], 0, 2)).toEqual([{ name: "a", order: 4 }]);
  });

  it("a dashboard with no order yet makes the move renumber, writing only what changes", () => {
    expect(reorder([row("a", 1), row("b", 2), row("new")], 2, 1)).toEqual([
      { name: "new", order: 2 },
      { name: "b", order: 3 },
    ]);
  });

  it("neighbours too close for a midpoint renumber instead", () => {
    const b = 1 + Number.EPSILON; // (1 + b) / 2 rounds to one of them
    expect(reorder([row("a", 1), row("b", b), row("c", 3)], 2, 1)).toEqual([
      { name: "c", order: 2 },
      { name: "b", order: 3 },
    ]);
  });

  it("no move, or a place out of range: nothing", () => {
    expect(reorder([row("a"), row("b")], 1, 1)).toEqual([]);
    expect(reorder([row("a"), row("b")], 0, 5)).toEqual([]);
  });
});

describe("saveOrder", () => {
  it("saves each changed dashboard's newest document with its new order, and nothing else", async () => {
    const saveDashboard = vi.fn(async () => ({}) as never);
    const rows = [row("a", 1), row("b", 2)];
    await saveOrder({ saveDashboard }, rows, [{ name: "b", order: 0.5 }]);
    expect(saveDashboard).toHaveBeenCalledTimes(1);
    expect(saveDashboard).toHaveBeenCalledWith("b", { ...rows[1]!.body, order: 0.5 });
  });
});
