import { describe, expect, it } from "vitest";
import type { DashboardRow } from "@flyball/client";
import { byTabOrder } from "../src/hooks/useDashboards.js";

const row = (name: string, created_ns: number, order?: number | null): DashboardRow => ({
  id: created_ns,
  name,
  rig: "t",
  created_ns,
  sha256: "",
  body: { schema_version: 3, name, rig: "t", grid: { cols: 24, row_height: 24 }, widgets: [], ...(order === undefined ? {} : { order }) },
});

describe("byTabOrder", () => {
  it("with no order anywhere, newest saved first (the order before tabs could move)", () => {
    expect(byTabOrder([row("a", 1), row("b", 3), row("c", 2)]).map((r) => r.name)).toEqual(["b", "c", "a"]);
  });

  it("ordered ones first, ascending; then the unordered, newest first", () => {
    const rows = [row("new", 9), row("second", 1, 2), row("old", 2, null), row("first", 5, 0.5)];
    expect(byTabOrder(rows).map((r) => r.name)).toEqual(["first", "second", "new", "old"]);
  });

  it("a tab moved between two others needs only its own order changed", () => {
    const rows = [row("a", 1, 1), row("b", 2, 2), row("c", 3, 1.5)];
    expect(byTabOrder(rows).map((r) => r.name)).toEqual(["a", "c", "b"]);
  });

  it("is a copy", () => {
    const rows = [row("a", 1), row("b", 2)];
    byTabOrder(rows);
    expect(rows.map((r) => r.name)).toEqual(["a", "b"]);
  });
});
