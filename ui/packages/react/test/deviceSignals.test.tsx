import { describe, expect, it } from "vitest";
import type { SignalOut } from "@flyball/client";
import { writeGroupLabel, writeGroupsOf } from "../src/panels/DeviceSignals.js";

/** A writable signal under `blender`, with what it must be set with. */
function w(name: string, together: string[] = [], access = "rpw"): SignalOut {
  return { name, address: `blender.${name}`, access, label: "", quantity: "flow", unit: "L/min", dimension: null, dtype: "float", shape: [], range: null, precision: null, warn: null, alarm: null, poll_s: null, limits: [0, 2], together, latest: null, write: null };
}

describe("writeGroupsOf", () => {
  it("closes each together group over its siblings, in tree order, and leaves a lone signal alone", () => {
    const humidity = w("humidity", [], "w");
    const dry = w("dry_flow", ["wet_flow"]);
    const wet = w("wet_flow", ["dry_flow"]);
    const blend = w("blend_flow", [], "rw");
    expect(writeGroupsOf([humidity, wet, dry, blend])).toEqual([[humidity], [wet, dry], [blend]]);
  });

  it("follows a one-sided listing to the closure", () => {
    const a = w("a", ["b"]);
    const b = w("b", []);
    const c = w("c", ["b"]);
    expect(writeGroupsOf([a, b, c]).map((g) => g.map((s) => s.name))).toEqual([["a", "b", "c"]]);
  });
});

describe("writeGroupLabel", () => {
  it("pluralises a shared tail, keeps a shared head singular, else joins the names", () => {
    expect(writeGroupLabel([w("dry_flow"), w("wet_flow")])).toBe("Flows");
    expect(writeGroupLabel([w("dry_effort"), w("wet_effort")])).toBe("Efforts");
    expect(writeGroupLabel([w("position_x"), w("position_y")])).toBe("Position");
    expect(writeGroupLabel([w("gain"), w("offset")])).toBe("Gain · Offset");
  });
});
