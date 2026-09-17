import type uPlot from "uplot";

/**
 * Put each series' newest point in the legend when no cursor is over the
 * chart, through uPlot's own `setLegend`: a legend that reads `—` until
 * hovered says nothing about a live trace. Series need not share times: each
 * takes its own last non-null point.
 */
/**
 * Legend rows are `live` (uPlot shows the value at `cursor.idx`), which stays
 * `null` — every row a placeholder dash — until the cursor moves. After each
 * draw, when nothing is actively hovering (`cursor.idx == null`), point the
 * legend at each trace's own newest point through uPlot's own `setLegend`:
 * the values (and their formatting) come from each series' own `value()`, so
 * hover and idle always agree. Traces are not resampled onto one clock (a
 * fast signal and a slow one keep their own timestamps, joined with nulls
 * where one has no point — `align()`), so "newest" is found per series, not
 * at one shared index: at the single latest instant some traces are still
 * null there. A real hover's own indices are left alone, and the toolbar
 * (driven by `nav`/`following`, not by the legend) is untouched either way.
 */
export function showLatestInLegend(u: uPlot): void {
  if (u.cursor.idx != null) return;
  const n = u.data[0]?.length ?? 0;
  if (!n) return;
  const idxs: Array<number | null> = [n - 1];
  for (let sidx = 1; sidx < u.data.length; sidx++) {
    const col = u.data[sidx] as ReadonlyArray<number | null>;
    let idx: number | null = null;
    for (let k = col.length - 1; k >= 0; k--) {
      if (col[k] != null) {
        idx = k;
        break;
      }
    }
    idxs.push(idx);
  }
  u.setLegend({ idxs });
}

