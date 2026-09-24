// @vitest-environment jsdom
/**
 * The humidity blender's `{keep: true, fallback}` blend flow, in both places a form meets it: the
 * `set_blend` command's `BlendFlow` tagged union (a fourth variant, "Keep total", whose `keep`
 * marker is set, not shown), and the device config's `blend_flow: number | KeepBlendFlow` (a
 * picker whose options are named, not "option 1").
 */
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { JsonSchema } from "@flyball/client";
import { SchemaForm } from "../src/form/SchemaForm.js";
import { formatTagged } from "../src/form/tagged.js";
import { simplifyNullables } from "../src/form/uiSchema.js";

afterEach(cleanup);

const fixed = { anyOf: [{ $ref: "#/$defs/Absolute" }, { $ref: "#/$defs/OfBlendMax" }] };
const setBlend: JsonSchema = {
  $defs: {
    Absolute: { type: "object", title: "Absolute", properties: { flow: { type: "number", unit: "L/min" } }, required: ["flow"] },
    OfBlendMax: { type: "object", title: "OfBlendMax", properties: { blend_fraction: { type: "number", default: 1, minimum: 0, maximum: 1 } } },
    FixedBlendFlow: fixed,
    KeepTotal: { type: "object", title: "KeepTotal", properties: { fallback: { anyOf: [{ $ref: "#/$defs/FixedBlendFlow" }, { type: "null" }], default: null }, keep: { const: true, default: true, type: "boolean" } } },
    BlendFlow: { anyOf: [{ $ref: "#/$defs/Absolute" }, { $ref: "#/$defs/OfBlendMax" }, { $ref: "#/$defs/KeepTotal" }] },
  },
  type: "object",
  properties: { blend_flow: { $ref: "#/$defs/BlendFlow", title: "Blend flow" } },
};

describe("set_blend's Keep total", () => {
  it("is a variant; choosing it sends keep: true without a Keep toggle", async () => {
    const onSubmit = vi.fn();
    render(createElement(SchemaForm, { schema: setBlend, value: { blend_flow: { flow: 1 } }, onSubmit }));
    fireEvent.click(screen.getByLabelText("Keep total"));
    expect(screen.queryByText("Keep")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(onSubmit.mock.calls[0]![0]).toEqual({ blend_flow: { keep: true, fallback: null } });
  });
  it("reads back as Keep total and its fallback, no raw `true`", () => {
    expect(formatTagged({ keep: true, fallback: { blend_fraction: 0.5 } }, setBlend.properties!.blend_flow, setBlend)).toBe("Keep total · Of blend max · 0.50");
  });
});

describe("the config's blend_flow: a number or {keep, fallback}", () => {
  const config: JsonSchema = {
    $defs: { KeepBlendFlow: { type: "object", title: "KeepBlendFlow", additionalProperties: false, properties: { keep: { const: true, type: "boolean" }, fallback: { type: "number", exclusiveMinimum: 0 } }, required: ["keep", "fallback"] } },
    type: "object",
    properties: { blend_flow: { anyOf: [{ type: "number", exclusiveMinimum: 0 }, { $ref: "#/$defs/KeepBlendFlow" }], default: 1, title: "Blend Flow" } },
  };
  it("names each branch", () => {
    const branches = simplifyNullables(config).properties!.blend_flow!.anyOf!;
    expect(branches.map((b) => b.title)).toEqual(["Blend Flow", "Keep blend flow"]);
  });
  it("the keep branch fills keep: true and asks only for the fallback", async () => {
    const onChange = vi.fn();
    const { container } = render(createElement(SchemaForm, { schema: config, value: {}, onChange }));
    const picker = container.querySelector("select") as HTMLSelectElement;
    expect([...picker.options].map((o) => o.textContent)).toEqual(["Blend Flow", "Keep blend flow"]);
    fireEvent.change(picker, { target: { value: "1" } });
    const box = await waitFor(() => {
      const b = container.querySelector("input[id$='fallback']") as HTMLInputElement | null;
      expect(b).not.toBeNull();
      return b!;
    });
    fireEvent.change(box, { target: { value: "1.5" } });
    await waitFor(() => expect(onChange.mock.calls.at(-1)![0]).toEqual({ blend_flow: { keep: true, fallback: 1.5 } }));
    expect(container.querySelector("input[id$='keep'][type='checkbox']")).toBeNull();
  });
});
