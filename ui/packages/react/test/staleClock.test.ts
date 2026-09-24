import { afterEach, describe, expect, it, vi } from "vitest";
import type { RigClient, StreamHandlers, Subscription } from "@flyball/client";
import { alarmLevel, staleThresholdS } from "@flyball/client";
import { TelemetryStore } from "../src/store/telemetry.js";

afterEach(() => vi.useRealTimers());

describe("staleness", () => {
  it("a signal not read on a period is never stale by age; an unknown period keeps the 5 s floor", () => {
    expect(staleThresholdS(null)).toBeNull();
    expect(staleThresholdS(undefined)).toBe(5);
    expect(staleThresholdS(2)).toBe(6);
    const signal = { poll_s: null };
    expect(alarmLevel(1, signal, { periodS: null, lastSampleS: 0, nowS: 10_000 })).toBe("ok");
    expect(alarmLevel(1, signal, { periodS: 1, lastSampleS: 0, nowS: 10 })).toBe("stale");
  });

  it("the store's clock keeps running from /api/clock when samples stop (the only poller died)", async () => {
    vi.useFakeTimers({ now: 1_000_000 });
    const open = new Map<string, StreamHandlers>();
    const rig = {
      stream(path: string, h: StreamHandlers): Subscription {
        open.set(path, h);
        return { close: () => undefined };
      },
      clock: async () => ({ now_ns: 100e9, start_time_ns: 0, elapsed_ns: 100e9, tags: {}, speed: 60 }),
      sessions: async () => [],
      events: async () => [],
    } as unknown as RigClient;
    const store = new TelemetryStore(rig);
    const stop = store.subscribeLatest("dev.a", () => undefined);
    open.get("samples")!.onMessage({ samples: [{ node: "dev", time_ns: 100e9, values: { a: 1 } }] });
    store.nowS(); // asks /api/clock
    await vi.advanceTimersByTimeAsync(0);
    expect(store.nowS()).toBeCloseTo(100, 0);
    // Ten wall seconds with no samples: at ×60 the rig's clock is 600 s on.
    await vi.advanceTimersByTimeAsync(10_000);
    expect(store.nowS()!).toBeGreaterThanOrEqual(699);
    stop();
  });
});
