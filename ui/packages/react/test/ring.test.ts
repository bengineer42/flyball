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
    const every = ring.read(emptyView(1), { every: 3 });
    expect(every.t).toEqual([4, 7, 10, 11]);
    const capped = ring.read(emptyView(1), { maxPoints: 3 });
    expect(capped.t.length).toBeLessThanOrEqual(4);
    expect(capped.t[capped.t.length - 1]).toBe(11);
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

  it("carries several columns", () => {
    const ring = new Ring({ width: 2 });
    ring.push(1, [1, Number.NaN]);
    ring.push(2, [2, 4]);
    const out = ring.read(emptyView(2));
    expect(out.cols[1]![0]).toBeNaN();
    expect(out.cols[1]![1]).toBe(4);
  });
});
