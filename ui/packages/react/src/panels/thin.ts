/**
 * Keep one point in `every`, always including the newest so a live chart's
 * right edge is current. `every` is a floor for how thin the series may get:
 * a short series (a sparse loop tick trace, say) is never thinned down to
 * just its first and last point (a straight line with no shape) merely
 * because the caller's decimation factor happens to exceed its length --
 * the effective step is capped so at least a handful of interior points
 * survive whenever the data has that many to give. A series more than twice
 * `every`'s length is unaffected: this only guards the short-series edge case.
 */
export function thin<T>(values: T[], every: number | undefined): T[] {
  if (!every || every <= 1 || values.length <= 2) return values;
  const step = Math.min(every, Math.max(1, Math.floor((values.length - 1) / 2)));
  if (step <= 1) return values;
  const out: T[] = [];
  for (let i = 0; i < values.length; i += step) out.push(values[i]!);
  if ((values.length - 1) % step !== 0) out.push(values[values.length - 1]!);
  return out;
}

/** Points a chart of `widthPx` is worth drawing: two per pixel, between 300 and 4 000 (DESIGN-SPEC §6). */
export const pointCap = (widthPx: number): number => Math.max(300, Math.min(4000, 2 * Math.round(widthPx)));
