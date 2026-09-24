// @vitest-environment jsdom
/** The conditions chip's tooltip links each condition to what it is on, by its subject's kind (wave 2: a condition may be on a signal, a controller or the rig, not only a device). */
import { describe, expect, it, vi } from "vitest";
// Status imports @flyball/react, which pulls in uPlot and its module-scope matchMedia probe.
vi.mock("uplot", () => ({ default: class {} }));
import { conditionHref } from "../src/Status.js";

describe("conditionHref", () => {
  it.each([
    ["device", "furnace", "#/devices/furnace"],
    ["signal", "furnace.zone1", "#/readings/furnace.zone1"],
    ["controller", "heaters.heater1", "#/controllers/heaters.heater1"],
    ["rig", "rig", "#/events"],
    ["something-new", "x", "#/events"],
  ])("%s %s -> %s", (subject_kind, subject, href) => {
    expect(conditionHref({ subject_kind, subject })).toBe(href);
  });
});
