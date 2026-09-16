import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { GeneratorOut } from "@flyball/client";
import { describeReference } from "../src/panels/ControllerPanel.js";
import { SchemaForm } from "../src/form/SchemaForm.js";
import { widgets } from "../src/form/widgets.js";

// The rig's origin is epoch 1000 s; "now" is 100 s in. Times of day come out in the local zone, so
// only their presence is asserted, not their digits.
const START = 1000;
const NOW = 1100;
const ramp = (extra: Partial<GeneratorOut> = {}): GeneratorOut => ({ tag: "linear_ramp_setpoint", pace: { value: 10, per: "minute" }, end: 75, end_time: 250, ...extra });

describe("describeReference", () => {
  it("is null for a fixed or absent reference", () => {
    expect(describeReference({ reference: 50, arrived: true }, "%RH", 1, NOW, START)).toBeNull();
    expect(describeReference({ reference: null, arrived: false }, "%RH", 1, NOW, START)).toBeNull();
  });
  it("describes a ramp at a speed with its arrival, counting down in rig time", () => {
    const text = describeReference({ reference: ramp(), arrived: false }, "%RH", 1, NOW, START)!;
    expect(text).toMatch(/^ramping to 75\.0 %RH at 10 %RH\/min · arrives .+ \(in 2 min 30 s\)$/);
  });
  it("describes a ramp over a duration", () => {
    const text = describeReference({ reference: ramp({ pace: { seconds: 180, nanoseconds: 0 } }), arrived: false }, "°C", 2, NOW, START)!;
    expect(text).toMatch(/^ramping to 75\.00 °C over 3 min 0 s · arrives /);
  });
  it("leaves the arrival off without the rig's origin", () => {
    expect(describeReference({ reference: ramp(), arrived: false }, "%RH", 1, NOW, null)).toBe("ramping to 75.0 %RH at 10 %RH/min");
  });
  it("says arrived once the controller has", () => {
    expect(describeReference({ reference: ramp(), arrived: true }, "%RH", 1, NOW, START)).toBe("ramped to 75.0 %RH · arrived");
    expect(describeReference({ reference: { tag: "hold", value: 30, duration: { seconds: 60, nanoseconds: 0 }, end_time: 160 }, arrived: true }, "%RH", 0, NOW, START)).toBe("held 30 %RH · arrived");
  });
  it("describes a hold, timed or not", () => {
    expect(describeReference({ reference: { tag: "hold", value: 30, duration: { seconds: 90, nanoseconds: 0 }, end_time: 190 }, arrived: false }, "%RH", 0, NOW, START)).toMatch(/^holding 30 %RH for 1 min 30 s · until .+ \(in 1 min 30 s\)$/);
    expect(describeReference({ reference: { tag: "hold", value: 30, duration: null }, arrived: false }, "%RH", 0, NOW, START)).toBe("holding 30 %RH");
  });
  it("describes a profile by its segment in force", () => {
    const profile: GeneratorOut = { tag: "profile", segments: [ramp({ end_time: undefined }), { tag: "hold", value: 75, duration: { seconds: 60, nanoseconds: 0 } }], active: 1, end_time: 310 };
    expect(describeReference({ reference: profile, arrived: false }, "%RH", 1, NOW, START)).toMatch(/^profile · segment 2 of 2 · holding 75\.0 %RH for 1 min 0 s · ends .+ \(in 3 min 30 s\)$/);
    expect(describeReference({ reference: profile, arrived: true }, "%RH", 1, NOW, START)).toBe("profile of 2 segments · arrived");
  });
  it("falls back to the tag for a generator it does not know", () => {
    expect(describeReference({ reference: { tag: "sine_sweep", end_time: 200 }, arrived: false }, "%RH", 1, NOW, START)).toMatch(/^following sine sweep · arrives /);
  });
});

describe("string fields", () => {
  const schema = { type: "object", properties: { text: { type: "string", title: "Text" }, mode: { type: "string", enum: ["a", "b"] } }, required: ["text"] };
  it("get one visible text box under the widgets' own label, and an enum keeps the segmented buttons", () => {
    const html = renderToStaticMarkup(<SchemaForm schema={schema} onSubmit={() => undefined} />);
    expect(html).toContain('class="fb-unit-input fb-text"');
    expect(html).toContain('type="text"');
    // Exactly one label for the field: the widget's own, not the theme's template's as well.
    expect(html.match(/<label[^>]*for="root_text"/g)).toHaveLength(1);
    expect(html).toContain('class="fb-field-label" id="root_text-label"');
    expect(html).toContain('class="fb-segmented"');
  });
  it("is registered as `text`", () => {
    expect(widgets.text).toBeTypeOf("function");
  });
});
