/** Keep one point in `every`, always including the newest so a live chart's right edge is current. */
export function thin<T>(values: T[], every: number | undefined): T[] {
  if (!every || every <= 1 || values.length <= 2) return values;
  const out: T[] = [];
  for (let i = 0; i < values.length; i += every) out.push(values[i]!);
  if ((values.length - 1) % every !== 0) out.push(values[values.length - 1]!);
  return out;
}
