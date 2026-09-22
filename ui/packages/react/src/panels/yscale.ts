import uPlot from "uplot";
import { fixed, tickDigits } from "@flyball/client";

/**
 * How a chart's y axis is scaled: fit the data (`"auto"`), the signal's
 * declared range (`"range"`), or fixed bounds. Shared by every chart.
 */
export type YScale = "auto" | "range" | { min: number; max: number };

/**
 * A y axis' own width, measured from its longest tick label rather than a
 * flat pixel count: a fixed `size` (uPlot's own default is 50px too) reserves
 * the same width whether the values are "0" or "12345.67", which comes to
 * dominate a chart's width once several narrow charts share a row. uPlot
 * re-runs this up to three times, feeling out how the size affects tick
 * spacing and hence the labels themselves; the last pass just repeats the
 * prior size so it settles instead of oscillating.
 */
export const axisSize: uPlot.Axis.Size = (self, values, axisIdx, cycleNum) => {
  const axis = self.axes[axisIdx]!;
  if (cycleNum > 1) return (axis as unknown as { _size: number })._size;
  const ticksSize = axis.ticks?.size ?? 10;
  const gap = axis.gap ?? 5;
  const longest = (values ?? []).reduce((a, b) => (b.length > a.length ? b : a), "");
  let textWidth = 0;
  if (longest) {
    self.ctx.font = (axis.font as unknown as [string, number, number])[0];
    textWidth = self.ctx.measureText(longest).width / uPlot.pxRatio;
  }
  return Math.ceil(Math.max(24, textWidth) + ticksSize + gap + 4);
};

/**
 * Keeps only the ticks closest to the scale's current min and max, dropping
 * the rest: a sparse "top and bottom value" y axis instead of uPlot's dense
 * default. uPlot doesn't offer a "give me exactly N ticks" option, so this
 * filters its own computed splits down to the two nearest the live bounds.
 */
export const edgeTicks: uPlot.Axis.Filter = (u, splits, axisIdx) => {
  const scaleKey = u.axes[axisIdx]!.scale ?? "y";
  const { min, max } = u.scales[scaleKey] ?? {};
  if (min == null || max == null || splits.length === 0) return splits;
  let lo = 0,
    hi = 0;
  for (let i = 1; i < splits.length; i++) {
    if (Math.abs(splits[i]! - min) < Math.abs(splits[lo]! - min)) lo = i;
    if (Math.abs(splits[i]! - max) < Math.abs(splits[hi]! - max)) hi = i;
  }
  return splits.map((s, i) => (i === lo || i === hi ? s : null));
};

/**
 * An axis' tick labels at a signal's own precision, instead of uPlot's own
 * significant-figure guess (which over-shows digits on a near-flat trace) --
 * but never fewer decimals than tell one tick from the next. Defensive
 * against `null`: uPlot's own size-convergence pass (`axesCalc`) and a
 * `filter` like `edgeTicks` (which nulls out every split but the two it
 * keeps) both hand this callback splits arrays that carry `null` entries, so
 * a plain `.toFixed()` throws mid-layout and leaves the canvas unsized --
 * the chart opens to an empty box. `Number.isFinite` catches `null` and
 * `NaN` alike.
 */
export const axisValues =
  (precision: number): uPlot.Axis.Values =>
  (_u, splits) => {
    const decimals = tickDigits(splits, precision);
    return splits.map((v) => (Number.isFinite(v) ? fixed(v, decimals) : ""));
  };

/** uPlot `range` for a y scale, or undefined to let uPlot autoscale. */
export function yRange(
  scale: YScale | undefined,
  declared: [number, number] | null | undefined,
): (() => [number, number]) | undefined {
  if (!scale || scale === "auto") return undefined;
  if (scale === "range") return declared ? () => declared : undefined;
  return () => [scale.min, scale.max];
}
