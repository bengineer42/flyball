import { describe, expect, it } from "vitest";
import type { DashboardDocument } from "@flyball/client";
import { SCHEMA_VERSION, emptyDocument, labelFor, labelOf, nameFor, normalise } from "../src/dashboard/document.js";

describe("a dashboard's name and label", () => {
  it("the name is the key a label is saved under: lower case, letters, digits and _, from a letter", () => {
    expect(nameFor("Furnace overview")).toBe("furnace_overview");
    expect(nameFor("  Zone 1 / Zone 2  ")).toBe("zone_1_zone_2");
    expect(nameFor("Été")).toBe("ete");
    expect(nameFor("ops")).toBe("ops");
    expect(nameFor("wet-pump")).toBe("wet_pump");
    expect(nameFor("2nd floor")).toBe("nd_floor");
    expect(nameFor("!!!")).toBe("");
    expect(nameFor("42")).toBe("");
    expect(nameFor("x".repeat(80))).toHaveLength(64);
    expect(nameFor("a" + " b".repeat(40))).toMatch(/^[a-z][a-z0-9_]{0,63}$/);
  });

  it("a label is kept only when it says more than the name", () => {
    expect(labelFor("Furnace overview", "furnace_overview")).toBe("Furnace overview");
    expect(labelFor("ops", "ops")).toBeNull();
  });

  it("what shows is the label, else the name", () => {
    expect(labelOf({ name: "wall", label: "Wall display" })).toBe("Wall display");
    expect(labelOf({ name: "wall", label: null })).toBe("wall");
    expect(labelOf(null, "wall")).toBe("wall");
  });

  it("a new document is at the current version and carries its label", () => {
    const doc = emptyDocument("wall", "t", "Wall display");
    expect([doc.schema_version, doc.name, doc.label]).toEqual([SCHEMA_VERSION, "wall", "Wall display"]);
    expect(SCHEMA_VERSION).toBe(6);
  });
});

describe("normalise", () => {
  it("keeps a widget's type and label and fills the document's label", () => {
    const doc = { schema_version: 6, name: "d", rig: "t", grid: { cols: 24, row_height: 24 }, widgets: [{ id: "r", type: "readout", label: "Zone", x: 0, y: 0, w: 3, h: 2, config: {} }, { id: "c", type: "chart", x: 3, y: 0, w: 6, h: 4, config: {} }] } as DashboardDocument;
    const out = normalise(doc);
    expect(out.label).toBeNull();
    expect(out.widgets.map((w) => [w.type, w.label])).toEqual([
      ["readout", "Zone"],
      ["chart", null],
    ]);
  });
});
