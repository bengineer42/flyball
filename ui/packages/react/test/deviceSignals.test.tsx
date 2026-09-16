import { describe, expect, it } from "vitest";
import type { JsonSchema } from "@flyball/client";
import { formatValue, isNumeric, linkedSignal } from "@flyball/client";
import { linkedArguments } from "../src/panels/CommandForm.js";

describe("formatValue by dtype", () => {
  it("renders a bool as on/off, whatever the meta says", () => {
    expect(formatValue(true, { dtype: "bool" })).toBe("on");
    expect(formatValue(false, { dtype: "bool" })).toBe("off");
    // A plain boolean is recognised even with no dtype given.
    expect(formatValue(true)).toBe("on");
  });

  it("renders a number at its precision with its unit", () => {
    expect(formatValue(20.567, { dtype: "float", unit: "°C", precision: 1 })).toBe("20.6 °C");
    expect(formatValue(3, { dtype: "int", unit: "" })).toBe("3.00"); // no precision given: 2 decimals
  });

  it("renders a str/enum as plain text", () => {
    expect(formatValue("blend", { dtype: "enum" })).toBe("blend");
    expect(formatValue("dry", { dtype: "str" })).toBe("dry");
  });

  it("renders json as a compact structure, and null/undefined as a dash", () => {
    expect(formatValue({ kind: "offline", level: 40 }, { dtype: "json" })).toBe('{"kind":"offline","level":40}');
    expect(formatValue(null, { dtype: "float" })).toBe("—");
    expect(formatValue(undefined)).toBe("—");
  });
});

describe("isNumeric", () => {
  it("is true only for float/int", () => {
    expect(isNumeric("float")).toBe(true);
    expect(isNumeric("int")).toBe(true);
    expect(isNumeric("bool")).toBe(false);
    expect(isNumeric("str")).toBe(false);
    expect(isNumeric("enum")).toBe(false);
    expect(isNumeric("json")).toBe(false);
  });
});

describe("linkedSignal / linkedArguments", () => {
  const schema: JsonSchema = {
    type: "object",
    properties: {
      flow: { type: "number", title: "Flow", unit: "L/min", "x-signal": "blender.dry_flow" },
      note: { type: "string", title: "Note" },
    },
  };

  it("reads a field's `x-signal` address, and nothing off a plain field", () => {
    expect(linkedSignal(schema.properties!.flow!)).toBe("blender.dry_flow");
    expect(linkedSignal(schema.properties!.note!)).toBeUndefined();
  });

  it("finds every linked argument of a command's schema, in order, and only those", () => {
    expect(linkedArguments(schema)).toEqual([{ name: "flow", field: schema.properties!.flow, address: "blender.dry_flow" }]);
  });
});
