import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { SignalOut, ActivityOut } from "@flyball/client";
import { Gauge, gaugeRange, gaugeZones } from "../src/panels/Gauge.js";
import { readoutLevel } from "../src/panels/Readout.js";
import { groupByUnit } from "../src/panels/UnitCharts.js";
import { PromptPanel } from "../src/panels/PromptPanel.js";

/** A publishing signal as `GET /api/devices` lists one, with the given bands. */
function signal(address: string, unit: string, bands: Partial<Pick<SignalOut, "range" | "precision" | "warn" | "alarm" | "limits">> = {}): SignalOut {
  return {
    name: address.slice(address.lastIndexOf(".") + 1),
    address,
    access: "rp",
    label: "",
    quantity: "temperature",
    unit,
    dimension: null,
    dtype: "float",
    shape: [],
    role: "readout",
    tags: {},
    initial: null,
    range: null,
    precision: null,
    warning: null,
    alarm: null,
    poll_s: null,
    limits: null,
    latest: null,
    write: null,
    ...bands,
  };
}

describe("Gauge on a SignalOut", () => {
  it("takes the range from the signal, else pads the widest band, else its limits, else 0–100", () => {
    expect(gaugeRange(signal("furnace.zone1", "°C", { range: [0, 800] }))).toEqual([0, 800]);
    expect(gaugeRange(signal("furnace.zone1", "°C", { alarm: [100, 200] }))).toEqual([90, 210]);
    // A signal with no range and no bands but a demand clamp (a blend's 0-2 L/min flow, say) --
    // the limits beat a meaningless 0-100 default.
    expect(gaugeRange(signal("blender.dry_flow", "L/min", { limits: [0, 2] }))).toEqual([0, 2]);
    expect(gaugeRange(signal("furnace.zone1", "°C"))).toEqual([0, 100]);
  });

  it("cuts the range at every band edge and labels each piece", () => {
    const zones = gaugeZones(signal("furnace.zone1", "°C", { range: [0, 100], warning: [30, 90], alarm: [10, 110] }));
    expect(zones.map((z) => [z.from, z.to, z.level])).toEqual([
      [0, 10, "alarm"],
      [10, 30, "warn"],
      [30, 90, "ok"],
      [90, 100, "warn"],
    ]);
    expect(gaugeZones(signal("meter.flow", "L/min", { range: [0, 50] }))).toEqual([{ from: 0, to: 50, level: null }]);
  });

  it("renders the value at the signal's precision with its unit", () => {
    const html = renderToStaticMarkup(<Gauge signal={signal("furnace.zone1", "°C", { range: [0, 800], precision: 1 })} value={654.72} kind="bar" />);
    expect(html).toContain("654.7");
    expect(html).toContain("°C");
    expect(html).toContain("fb-alarm-ok");
  });
});

describe("readoutLevel", () => {
  const s = signal("furnace.zone1", "°C", { warning: [30, 90], alarm: [10, 110] });
  it("reads the bands off the signal", () => {
    expect(readoutLevel(s, 60, undefined).level).toBe("ok");
    expect(readoutLevel(s, 95, undefined).level).toBe("warn");
    expect(readoutLevel(s, 5, undefined).level).toBe("alarm");
  });
  it("is stale, with the age in the footer, once no sample has arrived for max(3 × period, 5 s)", () => {
    const stale = readoutLevel(s, 60, { periodS: 2, lastSampleS: 100, nowS: 112 });
    expect(stale.level).toBe("stale");
    expect(stale.footer).toBe("last sample 12 s ago");
    expect(readoutLevel(s, 60, { periodS: 2, lastSampleS: 100, nowS: 105 }).level).toBe("ok");
  });
});

describe("groupByUnit", () => {
  it("groups signals by unit in first-seen order", () => {
    const groups = groupByUnit([signal("furnace.zone1", "°C"), signal("heaters.heater1", "W"), signal("furnace.zone2", "°C")]);
    expect(groups.map((g) => [g.unit, g.signals.map((s) => s.address)])).toEqual([
      ["°C", ["furnace.zone1", "furnace.zone2"]],
      ["W", ["heaters.heater1"]],
    ]);
  });
});

describe("PromptPanel", () => {
  const wait = (name: string, message: string | null, timeout_s: number | null): ActivityOut => ({ name, message, outcome: "pending", since_ns: 100e9, timeout_s, prompt: true });
  it("renders nothing with no pending prompt", () => {
    expect(renderToStaticMarkup(<PromptPanel prompts={[]} onFire={() => undefined} />)).toBe("");
  });
  it("shows each prompt's message, how long it has waited in rig time, and when it gives up", () => {
    const html = renderToStaticMarkup(<PromptPanel prompts={[wait("door", "Close the door", 600), wait("ack", null, null)]} nowS={190} onFire={() => undefined} onCancel={() => undefined} />);
    expect(html).toContain("Close the door");
    expect(html).toContain("waiting 1 min 30 s");
    expect(html).toContain("gives up in 8 min 30 s");
    expect(html).toContain("waiting for ack");
    expect(html).toContain("Cancel");
  });
});
