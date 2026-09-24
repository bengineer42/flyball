import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { JsonSchema } from "@flyball/client";
import { SchemaForm } from "../src/form/SchemaForm.js";
import { formatTagged } from "../src/form/tagged.js";
import { impliedUiSchema, isTaggedUnion, simplifyNullables } from "../src/form/uiSchema.js";

// The shape `sim_drive`'s config takes (DEVICE-MODEL-2, `GET /api/rig/schema`): a dict
// (`additionalProperties`) of ports, each an `anyOf` of a bare string or a `DrivePort` object
// spelled out, whose `limits` is a `Bounds | None` (a `tuple[float, float]`, which pydantic emits as
// `prefixItems` and RJSF 5 only renders once `simplifyNullables` rewrites it to `items: [...]`).
const band: JsonSchema = { maxItems: 2, minItems: 2, type: "array", prefixItems: [{ type: "number" }, { type: "number" }] };
const drivePort: JsonSchema = {
  type: "object",
  title: "DrivePort",
  properties: {
    port: { type: "string", title: "Port" },
    limits: { anyOf: [{ $ref: "#/$defs/Bounds" }, { type: "null" }], default: null, title: "Limits" },
  },
  required: ["port"],
};
const simDriveConfig: JsonSchema = {
  $defs: { Bounds: band, DrivePort: drivePort },
  type: "object",
  properties: {
    ports: {
      type: "object",
      title: "Ports",
      additionalProperties: { anyOf: [{ type: "string" }, { $ref: "#/$defs/DrivePort" }] },
    },
  },
};

describe("impliedUiSchema and a dict's additionalProperties", () => {
  it("walks into a dict's additionalProperties, so a union branch nested inside it (DrivePort.limits) gets its widget", () => {
    const simplified = simplifyNullables(simDriveConfig);
    const ui = impliedUiSchema(simplified, simplified);
    // ports -> additionalProperties (the RJSF convention for a dict's value schema) -> the DrivePort
    // branch of the anyOf -> limits -> the Bounds branch of *its* anyOf.
    const portsUi = ui.ports as any;
    expect(portsUi).toBeDefined();
    const driveportUi = portsUi.additionalProperties.anyOf[1];
    expect(driveportUi.limits.anyOf[1]["ui:widget"]).toBe("band");
  });

  it("rewrites the tuple's prefixItems to items on the $defs entry, so RJSF can see it as a fixed array", () => {
    const simplified = simplifyNullables(simDriveConfig);
    expect(simplified.$defs!.Bounds!.items).toEqual([{ type: "number" }, { type: "number" }]);
    expect(simplified.$defs!.Bounds!.prefixItems).toBeUndefined();
  });
});

describe("SchemaForm with a Bounds (bounded tuple) field", () => {
  // Regression: switching a `Bounds | None` field from "leave unchanged" to "set" left RJSF's own
  // `ArrayField` rendering a fixed-size array whose `formData` was `null` rather than `undefined`
  // (its default only fires on `undefined`), so `formData[index]` threw
  // "Cannot read properties of null (reading '0')" and unmounted the form. `widgetFor`/`fieldUiSchema`
  // now points a Bounds-shaped field at the `band` widget instead, which tolerates `null`.
  it("renders a null Bounds value as two empty boxes rather than throwing", () => {
    const schema: JsonSchema = { type: "object", properties: { limits: band } };
    const html = renderToStaticMarkup(<SchemaForm schema={schema} value={{ limits: null }} onSubmit={() => undefined} />);
    expect(html).toContain("fb-band");
    expect(html.match(/type="number"/g)).toHaveLength(2);
    expect(html).not.toContain('value="null"');
  });

  it("renders a set Bounds value in the two boxes", () => {
    const schema: JsonSchema = { type: "object", properties: { limits: band } };
    const html = renderToStaticMarkup(<SchemaForm schema={schema} value={{ limits: [10, 90] }} onSubmit={() => undefined} />);
    expect(html).toContain('value="10"');
    expect(html).toContain('value="90"');
  });

  it("is registered as `band`", async () => {
    const { widgets } = await import("../src/form/widgets.js");
    expect(widgets.band).toBeTypeOf("function");
  });

  it("renders the full sim_drive-shaped dict-of-DrivePort form without throwing, a set limits value included", () => {
    const value = { ports: { p1: { port: "heater1", limits: [0, 100] } } };
    const html = renderToStaticMarkup(<SchemaForm schema={simDriveConfig} value={value} onSubmit={() => undefined} />);
    expect(html).toContain("fb-band");
    expect(html).toContain('value="0"');
    expect(html).toContain('value="100"');
  });
});

// `BlendFlow` (DEVICE-MODEL-2 §6, the humidity example's blender): a structurally discriminated
// union of three dataclasses, no tag on the wire -- unlike a `Labelled` enum, whose branches are
// titled `const`s (`OnOverdrive`, nested inside `Absolute` here, the ordinary way). Shaped as the
// server actually sends it (`GET /api/devices/blender/schema`'s `set_blend`): `$defs`, `blend_flow`
// itself a bare `$ref` with siblings (`x-signal`, `default`, ...), `BlendFlow` an `anyOf` of `$ref`s.
const $defs: Record<string, JsonSchema> = {
  OnOverdrive: {
    title: "OnOverdrive",
    description: "How to handle a requested flow change that exceeds the maximum.",
    type: "string",
    oneOf: [
      { const: "raise", title: "Refuse the request" },
      { const: "clamp", title: "Clamp to the maximum" },
    ],
  },
  Absolute: {
    type: "object",
    title: "Absolute",
    properties: { flow: { type: "number", title: "Flow", unit: "L/min" }, on_overdrive: { $ref: "#/$defs/OnOverdrive", default: "raise" } },
    required: ["flow"],
  },
  OfBlendMax: { type: "object", title: "OfBlendMax", properties: { blend_fraction: { type: "number", title: "Blend Fraction", default: 1.0 } } },
  OfGuaranteedMax: { type: "object", title: "OfGuaranteedMax", properties: { guaranteed_max_fraction: { type: "number", title: "Guaranteed Max Fraction", default: 1.0 } } },
  BlendFlow: { anyOf: [{ $ref: "#/$defs/Absolute" }, { $ref: "#/$defs/OfBlendMax" }, { $ref: "#/$defs/OfGuaranteedMax" }] },
};
// `set_blend`'s own argument: linked (`x-signal`) and so not required, a bare `$ref`.
const blendFlowArg: JsonSchema = { $ref: "#/$defs/BlendFlow", default: null, "x-signal": "blender.blend.flow", unit: "", title: "Blend flow" };
// `set_humidity`'s: the same, but `BlendFlow | None` too (an `anyOf` with a null branch, wrapping the ref).
const blendFlowArgOptional: JsonSchema = { anyOf: [{ $ref: "#/$defs/BlendFlow" }, { type: "null" }], default: null, "x-signal": "blender.blend.flow", unit: "", title: "Blend flow" };
const schemaRoot: JsonSchema = { $defs, type: "object", properties: { blend_flow: blendFlowArg } };

describe("isTaggedUnion", () => {
  it("is true for a `BlendFlow`-shaped union, whether given directly or as a bare `$ref`", () => {
    expect(isTaggedUnion($defs.BlendFlow!, schemaRoot)).toBe(true);
    expect(isTaggedUnion(blendFlowArg, schemaRoot)).toBe(true); // the `$ref` itself, with siblings
  });

  it("is false for the nullable-object pattern (a null branch) and for a titled-enum oneOf", () => {
    expect(isTaggedUnion(blendFlowArgOptional, schemaRoot)).toBe(false);
    expect(isTaggedUnion($defs.OnOverdrive!, schemaRoot)).toBe(false);
  });
});

describe("SchemaForm with a BlendFlow-shaped tagged union field", () => {
  it("renders a segmented variant control (branch titles, not a raw nested-field title) and the matched branch's fields inline", () => {
    const html = renderToStaticMarkup(<SchemaForm schema={schemaRoot} value={{ blend_flow: { flow: 1, on_overdrive: "clamp" } }} onSubmit={() => undefined} />);
    expect(html).toContain("fb-tagged");
    expect(html).toContain("Absolute");
    expect(html).toContain("Of blend max");
    expect(html).toContain('value="1"'); // flow, inline
    // No JSON dump of the value, and no raw class-name title leaking through for the nested enum
    // (`on_overdrive`'s `$ref` is inlined by `simplifyNullables` before this ever renders, carrying
    // `OnOverdrive`'s own title with it unless the field is titled from its property name instead).
    expect(html).not.toContain("&quot;flow&quot;");
    expect(html).not.toContain(">OnOverdrive<");
    expect(html).toContain(">On overdrive<");
  });

  it("shows no asterisk on a branch field when the union itself is optional, but does when it is required", () => {
    const optional = renderToStaticMarkup(<SchemaForm schema={schemaRoot} value={{ blend_flow: { flow: 1 } }} onSubmit={() => undefined} />);
    expect(optional).not.toContain("fb-required");
    const required = renderToStaticMarkup(<SchemaForm schema={{ ...schemaRoot, required: ["blend_flow"] }} value={{ blend_flow: { flow: 1 } }} onSubmit={() => undefined} />);
    expect(required).toContain("fb-required");
  });
});

describe("formatTagged", () => {
  it("formats a tagged union's value as 'title · field · field', unit-suffixed where the field has one", () => {
    expect(formatTagged({ flow: 1, on_overdrive: "clamp" }, blendFlowArg, schemaRoot)).toBe("Absolute · 1.00 L/min · clamp");
    expect(formatTagged({ blend_fraction: 0.5 }, blendFlowArg, schemaRoot)).toBe("Of blend max · 0.50");
  });

  it("sees through an `X | None` wrapper (a linked, optional argument often is one) to the union inside", () => {
    expect(formatTagged({ flow: 1, on_overdrive: "clamp" }, blendFlowArgOptional, schemaRoot)).toBe("Absolute · 1.00 L/min · clamp");
  });

  it("falls back to null (so the caller uses JSON) off a tagged union or a non-object value", () => {
    expect(formatTagged({ flow: 1 }, { type: "number" }, schemaRoot)).toBeNull();
    expect(formatTagged(3, blendFlowArg, schemaRoot)).toBeNull();
    expect(formatTagged(null, blendFlowArg, schemaRoot)).toBeNull();
  });
});
