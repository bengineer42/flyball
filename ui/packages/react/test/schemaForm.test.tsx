import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { JsonSchema } from "@flyball/client";
import { SchemaForm } from "../src/form/SchemaForm.js";
import { impliedUiSchema, simplifyNullables } from "../src/form/uiSchema.js";

// The shape `sim_drive`'s config takes (DEVICE-MODEL-2, `GET /api/rig/schema`): a dict
// (`additionalProperties`) of ports, each an `anyOf` of a bare string or a `DrivePort` object
// spelled out, whose `limits` is a `Band | None` (a `tuple[float, float]`, which pydantic emits as
// `prefixItems` and RJSF 5 only renders once `simplifyNullables` rewrites it to `items: [...]`).
const band: JsonSchema = { maxItems: 2, minItems: 2, type: "array", prefixItems: [{ type: "number" }, { type: "number" }] };
const drivePort: JsonSchema = {
  type: "object",
  title: "DrivePort",
  properties: {
    port: { type: "string", title: "Port" },
    limits: { anyOf: [{ $ref: "#/$defs/Band" }, { type: "null" }], default: null, title: "Limits" },
  },
  required: ["port"],
};
const simDriveConfig: JsonSchema = {
  $defs: { Band: band, DrivePort: drivePort },
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
    // branch of the anyOf -> limits -> the Band branch of *its* anyOf.
    const portsUi = ui.ports as any;
    expect(portsUi).toBeDefined();
    const driveportUi = portsUi.additionalProperties.anyOf[1];
    expect(driveportUi.limits.anyOf[1]["ui:widget"]).toBe("band");
  });

  it("rewrites the tuple's prefixItems to items on the $defs entry, so RJSF can see it as a fixed array", () => {
    const simplified = simplifyNullables(simDriveConfig);
    expect(simplified.$defs!.Band!.items).toEqual([{ type: "number" }, { type: "number" }]);
    expect(simplified.$defs!.Band!.prefixItems).toBeUndefined();
  });
});

describe("SchemaForm with a Band (bounded tuple) field", () => {
  // Regression: switching a `Band | None` field from "leave unchanged" to "set" left RJSF's own
  // `ArrayField` rendering a fixed-size array whose `formData` was `null` rather than `undefined`
  // (its default only fires on `undefined`), so `formData[index]` threw
  // "Cannot read properties of null (reading '0')" and unmounted the form. `widgetFor`/`fieldUiSchema`
  // now points a Band-shaped field at the `band` widget instead, which tolerates `null`.
  it("renders a null Band value as two empty boxes rather than throwing", () => {
    const schema: JsonSchema = { type: "object", properties: { limits: band } };
    const html = renderToStaticMarkup(<SchemaForm schema={schema} value={{ limits: null }} onSubmit={() => undefined} />);
    expect(html).toContain("fb-band");
    expect(html.match(/type="number"/g)).toHaveLength(2);
    expect(html).not.toContain('value="null"');
  });

  it("renders a set Band value in the two boxes", () => {
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
