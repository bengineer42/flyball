/**
 * A ring of time-stamped rows: one `Float64Array` for the times and one per
 * value column, oldest first, wrapping once full. Capacity doubles on
 * overflow up to `cap`; past that the oldest row goes. Rows older than
 * `windowS` behind the newest are dropped on push, so memory is bounded by
 * time as well as by count. Nothing here allocates per push.
 */
export interface RingOptions {
  /** Columns beside time; 1 for a signal, 5 for a controller tick. */
  width?: number;
  /** Rows to start with. */
  initial?: number;
  /** Rows at most; the oldest is overwritten past this. */
  cap?: number;
  /** Seconds of history to keep behind the newest row; unbounded when omitted. */
  windowS?: number;
}

/** Plain arrays a reader fills, reused between reads so a chart's refresh allocates nothing new. */
export interface RingView {
  t: number[];
  cols: number[][];
}

export const emptyView = (width: number): RingView => ({ t: [], cols: Array.from({ length: width }, () => []) });

export class Ring {
  readonly width: number;
  readonly cap: number;
  readonly windowS: number;
  private t: Float64Array;
  private cols: Float64Array[];
  private head = 0;
  private count = 0;

  constructor({ width = 1, initial = 256, cap = 65536, windowS = Number.POSITIVE_INFINITY }: RingOptions = {}) {
    this.width = width;
    this.cap = Math.max(initial, cap);
    this.windowS = windowS;
    this.t = new Float64Array(initial);
    this.cols = Array.from({ length: width }, () => new Float64Array(initial));
  }

  get length(): number {
    return this.count;
  }

  get capacity(): number {
    return this.t.length;
  }

  private at(i: number): number {
    return (this.head + i) % this.t.length;
  }

  /** Time of the oldest row, or undefined when empty. */
  firstT(): number | undefined {
    return this.count ? this.t[this.head] : undefined;
  }

  /** Time of the newest row, or undefined when empty. */
  lastT(): number | undefined {
    return this.count ? this.t[this.at(this.count - 1)] : undefined;
  }

  /** The newest row's value in column `col`, or undefined when empty. */
  last(col = 0): number | undefined {
    return this.count ? this.cols[col]![this.at(this.count - 1)] : undefined;
  }

  timeAt(i: number): number {
    return this.t[this.at(i)]!;
  }

  valueAt(i: number, col = 0): number {
    return this.cols[col]![this.at(i)]!;
  }

  clear(): void {
    this.head = 0;
    this.count = 0;
  }

  /**
   * Append one row. A row at the newest row's time replaces it (a socket
   * resends the current state on connect); a row older than the newest starts
   * the ring afresh, since a clock that went backwards is a new timeline
   * (a simulated rig restarted) and a chart cannot draw both.
   */
  push(time: number, values: ArrayLike<number>): void {
    if (this.count) {
      const last = this.lastT()!;
      if (time === last) {
        const i = this.at(this.count - 1);
        for (let c = 0; c < this.width; c++) this.cols[c]![i] = values[c]!;
        return;
      }
      if (time < last) this.clear();
    }
    if (this.count === this.t.length) {
      if (this.t.length < this.cap) this.grow(Math.min(this.cap, this.t.length * 2));
      else this.dropOldest();
    }
    const i = this.at(this.count);
    this.t[i] = time;
    for (let c = 0; c < this.width; c++) this.cols[c]![i] = values[c]!;
    this.count++;
    // Rows that fell out of the window.
    if (Number.isFinite(this.windowS)) {
      const cutoff = time - this.windowS;
      while (this.count > 1 && this.t[this.head]! < cutoff) this.dropOldest();
    }
  }

  private dropOldest(): void {
    this.head = (this.head + 1) % this.t.length;
    this.count--;
  }

  private grow(capacity: number): void {
    const t = new Float64Array(capacity);
    const cols = this.cols.map(() => new Float64Array(capacity));
    for (let i = 0; i < this.count; i++) {
      const j = this.at(i);
      t[i] = this.t[j]!;
      for (let c = 0; c < this.width; c++) cols[c]![i] = this.cols[c]![j]!;
    }
    this.t = t;
    this.cols = cols;
    this.head = 0;
  }

  /** Index of the first row at or after `time` (binary search); `count` when none. */
  indexAtOrAfter(time: number): number {
    let lo = 0;
    let hi = this.count;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (this.timeAt(mid) < time) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  /**
   * Copy rows from `fromS` on into `out`, thinned to roughly `maxPoints` (raised
   * by `every`, coarser still if given) with the newest row always kept. The
   * arrays in `out` are reused: their lengths are set, not their identities.
   *
   * Thinning is by fixed time bucket, not a row-count stride: `bucketWidth`
   * (derived once per call from `windowS`/the held span and the target point
   * count, never from "now" or a row index) partitions time into
   * `floor(t / bucketWidth)` buckets, and the *last* row seen in each bucket
   * is kept. A bucket's boundaries are anchored to absolute time, so a
   * bucket's identity never changes call to call -- what changes between two
   * redraws is only a new bucket appearing at the live edge and an old one
   * falling out of the window, never a reshuffle of which rows represent the
   * middle of the series.
   *
   * A row-count stride (keep one row in every N, counting from some
   * reference point) cannot give this guarantee regardless of which end it
   * counts from: between two redraws, however many raw rows arrived shifts
   * the stride's phase by that many positions, and unless that count happens
   * to be an exact multiple of the stride, the kept set changes almost
   * completely -- anchoring to the newest row instead of the window's start
   * was tried first and still had this flaw (see brain/plans/ui-fixes.md for
   * the worked example). Bucketing by fixed time avoids it because a
   * bucket's boundary is never relative to anything that moves.
   *
   * A short window (few rows since `fromS`) is never thinned to just its
   * first and last row: with fewer real samples than target buckets, most
   * buckets hold at most one row, so every row keeps its own bucket -- no
   * special case needed, it falls out of the bucket width being small
   * relative to the actual sample spacing.
   */
  read(out: RingView, { fromS = Number.NEGATIVE_INFINITY, every = 1, maxPoints = Number.POSITIVE_INFINITY }: { fromS?: number; every?: number; maxPoints?: number } = {}): RingView {
    const start = fromS === Number.NEGATIVE_INFINITY ? 0 : this.indexAtOrAfter(fromS);
    const n = this.count - start;
    const { t, cols } = out;
    let k = 0;
    if (n > 0) {
      const lastLogical = this.count - 1;
      const thinning = Number.isFinite(maxPoints) || (every && every > 1);
      if (!thinning) {
        for (let i = start; i <= lastLogical; i++, k++) {
          const j = this.at(i);
          t[k] = this.t[j]!;
          for (let c = 0; c < this.width; c++) cols[c]![k] = this.cols[c]![j]!;
        }
      } else {
        const span = Number.isFinite(this.windowS) ? this.windowS : Math.max(this.t[this.at(lastLogical)]! - this.t[this.at(start)]!, 1e-9);
        const targetPoints = Number.isFinite(maxPoints) ? maxPoints : n;
        const points = Math.max(1, Math.floor(targetPoints / Math.max(1, every)));
        const bucketWidth = Math.max(span / points, 1e-9);
        let bucket = Number.NaN;
        for (let i = start; i <= lastLogical; i++) {
          const j = this.at(i);
          const ti = this.t[j]!;
          const b = Math.floor(ti / bucketWidth);
          if (b !== bucket) {
            bucket = b;
            k++;
          }
          const row = k - 1;
          t[row] = ti;
          for (let c = 0; c < this.width; c++) cols[c]![row] = this.cols[c]![j]!;
        }
      }
    }
    t.length = k;
    for (let c = 0; c < this.width; c++) cols[c]!.length = k;
    return out;
  }

  /**
   * Put `rows` (ascending times) in front of what is held: history fetched
   * after live rows started arriving. Rows at or after the oldest live row
   * are dropped, so the seam has no duplicates or steps backwards.
   */
  prepend(times: ArrayLike<number>, columns: ArrayLike<number>[]): void {
    const live = this.read(emptyView(this.width));
    const firstLive = live.t.length ? live.t[0]! : Number.POSITIVE_INFINITY;
    this.clear();
    const row = new Array<number>(this.width);
    for (let i = 0; i < times.length; i++) {
      const time = times[i]!;
      if (time >= firstLive) break;
      for (let c = 0; c < this.width; c++) row[c] = columns[c]![i]!;
      this.push(time, row);
    }
    for (let i = 0; i < live.t.length; i++) {
      for (let c = 0; c < this.width; c++) row[c] = live.cols[c]![i]!;
      this.push(live.t[i]!, row);
    }
  }
}
