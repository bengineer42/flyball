import { describe, expect, it } from "vitest";
import { breakGaps } from "../src/panels/thin.js";

describe("breakGaps", () => {
  it("returns the inputs unchanged when there is no gap", () => {
    const t = [0, 1, 2, 3];
    const v = [10, 11, 12, 13];
    const [t2, v2] = breakGaps(t, v, 2);
    expect(t2).toBe(t);
    expect(v2).toBe(v);
  });

  it("returns the inputs unchanged when maxGapS is undefined", () => {
    const t = [0, 1, 10];
    const v = [1, 2, 3];
    const [t2, v2] = breakGaps(t, v, undefined);
    expect(t2).toBe(t);
    expect(v2).toBe(v);
  });

  it("inserts a NaN midpoint at a gap wider than maxGapS, keeping both real points", () => {
    const t = [0, 1, 20, 21];
    const v = [10, 11, 12, 13];
    const [t2, v2] = breakGaps(t, v, 5);
    expect(t2).toEqual([0, 1, 10.5, 20, 21]);
    expect(v2.slice(0, 2)).toEqual([10, 11]);
    expect(v2[2]).toBeNaN();
    expect(v2.slice(3)).toEqual([12, 13]);
  });

  it("handles more than one gap", () => {
    const t = [0, 10, 11, 30];
    const v = [1, 2, 3, 4];
    const [t2, v2] = breakGaps(t, v, 5);
    expect(t2).toEqual([0, 5, 10, 11, 20.5, 30]);
    expect(v2).toEqual([1, Number.NaN, 2, 3, Number.NaN, 4]);
  });
});
