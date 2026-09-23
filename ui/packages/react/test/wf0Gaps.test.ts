import { describe, expect, it } from "vitest";
import { align, toBreaks } from "../src/panels/MultiSeries.js";

// wf0: multi-signal and controller charts used to draw a straight line across a real dead-time
// break, because the alignment fill uPlot.join inserts for a trace with no point at another
// trace's time (undefined) and a real gap (NaN, from the ring's gap insertion or a controller
// setpoint's null-as-NaN) both ended up as the same thing once `spanGaps: true` bridged both.
// uPlot 1.6.32's own join (node_modules/uplot/dist/uPlot.esm.js) only ever fills an alignment
// artifact with `undefined` (its default nullMode, NULL_RETAIN, leaves those undefined and only
// retains an explicit `null` as a break); its line-drawing code (`hasGap = true`) breaks only on
// `yVal === null`, strictly -- `undefined` is skipped without breaking, so a real gap must reach
// uPlot as `null`, never `NaN` (a stray NaN would instead call `pixelForY(NaN)`).

describe("toBreaks", () => {
  it("turns a real-gap NaN into a break (null)", () => {
    expect(toBreaks([1, Number.NaN, 3])).toEqual([1, null, 3]);
  });

  it("leaves an already-null or ordinary array alone", () => {
    const v = [1, null, 3];
    expect(toBreaks(v)).toBe(v); // no NaN present: same reference, no redundant copy
  });
});

describe("align", () => {
  it("connects two traces sampled at different timestamps (alignment fill is undefined, not null)", () => {
    const a = { t: [0, 2, 4], v: [10, 12, 14] };
    const b = { t: [1, 3, 5], v: [20, 22, 24] };
    const [x, va, vb] = align([a, b]) as [number[], (number | undefined)[], (number | undefined)[]];

    expect(x).toEqual([0, 1, 2, 3, 4, 5]);
    // `a` has no point at 1, 3, 5 -- an alignment artifact, not a real gap: undefined, so a chart
    // with `spanGaps: false` still draws through it.
    expect(va).toEqual([10, undefined, 12, undefined, 14, undefined]);
    expect(vb).toEqual([undefined, 20, undefined, 22, undefined, 24]);
  });

  it("keeps a real NaN/null break broken across alignment (does not get bridged into undefined)", () => {
    const a = { t: [0, 1, 2, 3], v: [10, Number.NaN, 12, 13] }; // a real dead-time gap at t=1
    const b = { t: [0, 1, 2, 3], v: [20, 21, 22, 23] }; // shares every timestamp with `a`
    const [x, va, vb] = align([a, b]) as [number[], (number | null)[], (number | null)[]];

    expect(x).toEqual([0, 1, 2, 3]);
    expect(va).toEqual([10, null, 12, 13]); // the break survives as a real `null`, not `NaN`
    expect(vb).toEqual([20, 21, 22, 23]); // unaffected trace stays fully connected
  });

  it("keeps a real gap broken even when the traces don't share timestamps (goes through uPlot.join)", () => {
    const a = { t: [0, 2, 4], v: [10, Number.NaN, 14] }; // a real gap at t=2
    const b = { t: [1, 3, 5], v: [20, 22, 24] };
    const [x, va] = align([a, b]) as [number[], (number | null | undefined)[]];

    expect(x).toEqual([0, 1, 2, 3, 4, 5]);
    // t=2 is a's own sample, carrying the real break: null, never NaN and never bridged to undefined.
    expect(va[2]).toBeNull();
    expect(va[1]).toBeUndefined(); // t=1 is an alignment artifact for `a` (only `b` samples there)
  });
});
