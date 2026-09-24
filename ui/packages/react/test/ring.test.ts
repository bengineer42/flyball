import { describe, expect, it } from "vitest";
import { Ring, emptyView } from "../src/store/ring.js";

const fill = (ring: Ring, n: number, from = 0) => {
  for (let i = from; i < from + n; i++) ring.push(i, [i * 10]);
};

describe("Ring", () => {
  it("keeps rows oldest first and grows by doubling up to the cap", () => {
    const ring = new Ring({ initial: 4, cap: 16 });
    fill(ring, 5);
    expect(ring.length).toBe(5);
    expect(ring.capacity).toBe(8);
    expect(ring.firstT()).toBe(0);
    expect(ring.lastT()).toBe(4);
    expect(ring.last()).toBe(40);
    fill(ring, 20, 5);
    expect(ring.capacity).toBe(16);
    expect(ring.length).toBe(16);
    expect(ring.firstT()).toBe(9); // the oldest went
    expect(ring.lastT()).toBe(24);
  });

  it("reads a window, thinned to a cap, with the newest row always kept", () => {
    const ring = new Ring({ initial: 8, cap: 8 });
    fill(ring, 12); // wraps: holds 4..11
    const all = ring.read(emptyView(1));
    expect(all.t).toEqual([4, 5, 6, 7, 8, 9, 10, 11]);
    expect(all.cols[0]).toEqual([40, 50, 60, 70, 80, 90, 100, 110]);
    const from = ring.read(emptyView(1), { fromS: 9 });
    expect(from.t).toEqual([9, 10, 11]);
    // Bucketed by fixed time, not a row-count stride: no `windowS` on this ring, so the span
    // is the held range (4..11 = 7), target points = floor(8/3) = 2, bucket width = 7/2 = 3.5.
    // floor(t/3.5): 4,5,6 -> 1 (keep last, 6); 7,8,9,10 -> 2 (keep last, 10); 11 -> 3 (keep 11).
    const every = ring.read(emptyView(1), { every: 3 });
    expect(every.t).toEqual([6, 10, 11]);
    const capped = ring.read(emptyView(1), { maxPoints: 3 });
    expect(capped.t.length).toBeLessThanOrEqual(4);
    expect(capped.t[capped.t.length - 1]).toBe(11);
  });

  it("does not reshuffle which rows represent a closed bucket as new rows arrive", () => {
    // windowS fixed, so bucket width is fixed too -- the actual property that matters: a bucket
    // that already has all its real data (anything but the very newest, still-filling one) keeps
    // exactly the same representative row across reads, however many new rows have since arrived
    // at the live edge. A row-count stride (the bug this replaces) fails this even when anchored
    // to the newest row -- see Ring.read's docstring for why.
    const ring = new Ring({ initial: 16, cap: 4096, windowS: 200 });
    for (let t = 0; t < 100; t++) ring.push(t, [t * 10]);
    const before = ring.read(emptyView(1), { maxPoints: 20 });
    const beforeByBucket = new Map(before.t.map((t, i) => [t, before.cols[0]![i]]));
    for (let t = 100; t < 130; t++) ring.push(t, [t * 10]);
    const after = ring.read(emptyView(1), { maxPoints: 20 });
    const afterByBucket = new Map(after.t.map((t, i) => [t, after.cols[0]![i]]));
    // Every bucket from `before`, except the last (newest, still open when more rows arrive),
    // must appear in `after` with the identical timestamp and value -- not merely a similar one.
    const closed = before.t.slice(0, -1);
    expect(closed.length).toBeGreaterThan(5); // the setup is pointless if there is nothing to check
    for (const t of closed) {
      expect(afterByBucket.has(t)).toBe(true);
      expect(afterByBucket.get(t)).toBe(beforeByBucket.get(t));
    }
  });

  it("breaks the line across a real raw gap without erasing it over decimation-induced spacing", () => {
    // windowS fixed at 3600 (a realistic store setting) so bucketing spreads out the same way
    // a live sparkline sees it: many real rows packed into few buckets makes each bucket
    // representative tens of seconds from the last, far more than a short poll period. A
    // `maxGapS` sized for that poll period must not treat every bucket boundary as dead time.
    const ring = new Ring({ initial: 16, cap: 8192, windowS: 3600 });
    for (let t = 0; t < 200; t++) ring.push(t, [t]); // one row a second, densely packed
    const dense = ring.read(emptyView(1), { maxPoints: 20, maxGapS: 3 });
    expect(dense.t.some(Number.isNaN)).toBe(false); // t never NaN
    expect(dense.cols[0]!.every((v) => !Number.isNaN(v))).toBe(true); // no bucket boundary read as a gap
    // Now push a real gap: nothing for 50s, then resumes.
    ring.push(250, [250]);
    for (let t = 251; t < 260; t++) ring.push(t, [t]);
    const withGap = ring.read(emptyView(1), { maxPoints: 20, maxGapS: 3 });
    expect(withGap.cols[0]!.some((v) => Number.isNaN(v))).toBe(true); // the real gap does break
  });

  it("reuses the caller's arrays", () => {
    const ring = new Ring();
    fill(ring, 3);
    const out = emptyView(1);
    const t = out.t;
    ring.read(out);
    ring.push(3, [30]);
    ring.read(out);
    expect(out.t).toBe(t);
    expect(out.t).toEqual([0, 1, 2, 3]);
  });

  it("drops rows older than the window", () => {
    const ring = new Ring({ windowS: 5 });
    fill(ring, 20);
    expect(ring.firstT()).toBe(14);
    expect(ring.length).toBe(6);
  });

  it("replaces a row at the newest time and restarts when time goes backwards", () => {
    const ring = new Ring();
    ring.push(1, [1]);
    ring.push(1, [2]);
    expect(ring.length).toBe(1);
    expect(ring.last()).toBe(2);
    ring.push(0.5, [3]);
    expect(ring.length).toBe(1);
    expect(ring.lastT()).toBe(0.5);
  });

  it("prepends history under the live rows without duplicates", () => {
    const ring = new Ring({ initial: 4, cap: 64 });
    ring.push(10, [100]);
    ring.push(11, [110]);
    ring.prepend([7, 8, 9, 10, 11], [[70, 80, 90, 999, 999]]);
    const out = ring.read(emptyView(1));
    expect(out.t).toEqual([7, 8, 9, 10, 11]);
    expect(out.cols[0]).toEqual([70, 80, 90, 100, 110]);
  });

  it("sizes the thinning bucket from the caller's spanS, not the ring's full windowS", () => {
    // A store ring holds 3600s but a chart only shows the newest 60s. Without spanS, the
    // bucket width comes from the full 3600s window (3600/400 = 9s) and most of the 60
    // samples actually on screen fall into the same handful of buckets. With spanS set to
    // the chart's own span, the bucket width matches what is visible and every sample in
    // range keeps its own bucket.
    const ring = new Ring({ initial: 16, cap: 8192, windowS: 3600 });
    for (let t = 0; t < 200; t++) ring.push(t, [t]);
    const withoutSpan = ring.read(emptyView(1), { fromS: 140, maxPoints: 400 });
    expect(withoutSpan.t.length).toBeLessThan(60);
    const withSpan = ring.read(emptyView(1), { fromS: 140, maxPoints: 400, spanS: 60 });
    expect(withSpan.t.length).toBe(60);
    expect(withSpan.t).toEqual(Array.from({ length: 60 }, (_, i) => 140 + i));
  });

  it("carries several columns", () => {
    const ring = new Ring({ width: 2 });
    ring.push(1, [1, Number.NaN]);
    ring.push(2, [2, 4]);
    const out = ring.read(emptyView(2));
    expect(out.cols[1]![0]).toBeNaN();
    expect(out.cols[1]![1]).toBe(4);
  });
});
