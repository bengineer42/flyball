import { describe, expect, it } from "vitest";
import type uPlot from "uplot";
import { navigation } from "../src/panels/navigation.js";

/** A stand-in for the chart: xRange reads only `data`. */
const chart = (xs: number[]) => ({ data: [xs] }) as unknown as uPlot;
const T = 1_790_386_035; // 2026-09-26

describe("live x window", () => {
  it("hangs from the newest point, not from uPlot's padded max for a single point (the 2029 axis)", () => {
    // uPlot pads a one-point time scale by a thousand days; the window must ignore that.
    expect(navigation().xRange(chart([T]), T, T + 86_400_000, 300)).toEqual([T - 300, T]);
  });
  it("with several points: the last one", () => {
    expect(navigation().xRange(chart([T - 100, T - 50, T]), T - 100, T, 300)).toEqual([T - 300, T]);
  });
  it("skips trailing gaps", () => {
    expect(navigation().xRange(chart([T - 10, T, Number.NaN]), T - 10, T, 60)).toEqual([T - 60, T]);
  });
  it("no window: uPlot's own range", () => {
    expect(navigation().xRange(chart([T]), T - 5, T + 5, undefined)).toEqual([T - 5, T + 5]);
  });
});
