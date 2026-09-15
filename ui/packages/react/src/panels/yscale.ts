/**
 * How a chart's y axis is scaled: fit the data (`"auto"`), the channel's
 * declared range (`"range"`), or fixed bounds. Shared by every chart.
 */
export type YScale = "auto" | "range" | { min: number; max: number };

/** uPlot `range` for a y scale, or undefined to let uPlot autoscale. */
export function yRange(
  scale: YScale | undefined,
  declared: [number, number] | null | undefined,
): (() => [number, number]) | undefined {
  if (!scale || scale === "auto") return undefined;
  if (scale === "range") return declared ? () => declared : undefined;
  return () => [scale.min, scale.max];
}
