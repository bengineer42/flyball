import uPlot from "uplot";

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

/** uPlot `range` for a y scale, or undefined to let uPlot autoscale. */
export function yRange(
  scale: YScale | undefined,
  declared: [number, number] | null | undefined,
): (() => [number, number]) | undefined {
  if (!scale || scale === "auto") return undefined;
  if (scale === "range") return declared ? () => declared : undefined;
  return () => [scale.min, scale.max];
}
