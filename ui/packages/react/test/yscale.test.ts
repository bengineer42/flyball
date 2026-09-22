import { describe, expect, it } from "vitest";
import { axisValues, edgeTicks } from "../src/panels/yscale.js";

describe("axisValues", () => {
  it("labels ordinary ticks at the given precision", () => {
    const values = axisValues(1)({} as never, [0, 5, 10]);
    expect(values).toEqual(["0.0", "5.0", "10.0"]);
  });

  it("does not throw on the null placeholders a filter (edgeTicks) leaves in the splits array", () => {
    // Regression: uPlot's own size-convergence pass, and `edgeTicks`'s filter,
    // both hand this callback splits arrays carrying `null` for the ticks
    // they don't want labelled. A plain `.toFixed()` on one of those threw
    // mid-layout and left the chart's canvas unsized -- it opened empty.
    expect(() => axisValues(1)({} as never, [0, null, null, 10] as unknown as number[])).not.toThrow();
    expect(axisValues(1)({} as never, [0, null, null, 10] as unknown as number[])).toEqual(["0.0", "", "", "10.0"]);
  });
});

describe("edgeTicks", () => {
  it("keeps only the two splits nearest the scale's min and max, nulling the rest", () => {
    const u = { axes: [{ scale: "y" }], scales: { y: { min: 0, max: 10 } } } as never;
    expect(edgeTicks(u, [0, 2, 5, 8, 10], 0)).toEqual([0, null, null, null, 10]);
  });

  it("passes splits through unchanged when the scale has no bounds yet", () => {
    const u = { axes: [{ scale: "y" }], scales: { y: {} } } as never;
    expect(edgeTicks(u, [0, 2, 5], 0)).toEqual([0, 2, 5]);
  });
});
