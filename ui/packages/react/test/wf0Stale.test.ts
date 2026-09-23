import { describe, expect, it } from "vitest";
import { alarmLevel, staleAfterS } from "@flyball/client";

// wf0: the stale rule (`max(3 * period, 5s)`) used the device's poll period
// (`DeviceOut.run.period_s` -- the device's *fastest* signal), not the
// signal's own `poll_s` (`SignalOut.poll_s`, wire.ts). A device whose
// signals poll at different rates -- the humidity rig's chamber sensor
// every 1 s, dry/wet every 5 s -- judged every signal against the fastest
// one, so a slow signal read 4 s ago (fine, its own 5 s poll_s gives a 15 s
// stale threshold) showed as stale because the device polls some other
// signal every 1 s (a 5 s threshold via the 5 s floor, not the signal's).

describe("alarmLevel stale threshold", () => {
  it("uses the signal's own poll_s over the device's period when both are given", () => {
    const slowSignal = { poll_s: 5 }; // this signal's own period: max(3*5, 5) = 15 s
    const fresh = { periodS: 1, lastSampleS: 0, nowS: 8 }; // 8 s old; device's fastest signal polls at 1 s
    // Judged against the device period alone, staleAfterS(1) = 5 s and 8 s old would be "stale".
    expect(alarmLevel(1, slowSignal, fresh)).toBe("ok");
  });

  it("still goes stale once past its own signal's threshold", () => {
    const slowSignal = { poll_s: 5 }; // threshold: max(3*5, 5) = 15 s
    const fresh = { periodS: 1, lastSampleS: 0, nowS: 16 };
    expect(alarmLevel(1, slowSignal, fresh)).toBe("stale");
  });

  it("falls back to the device period when the signal carries no poll_s", () => {
    const noOwnPeriod = {}; // e.g. `alarmLevel(null, {}, fresh)` (Loop.tsx's device-offline check)
    const fresh = { periodS: 1, lastSampleS: 0, nowS: 8 }; // threshold: max(3*1, 5) = 5 s
    expect(alarmLevel(1, noOwnPeriod, fresh)).toBe("stale");
  });

  it("treats a null signal poll_s the same as absent (falls back to the device period)", () => {
    const nullPeriod = { poll_s: null };
    const fresh = { periodS: 1, lastSampleS: 0, nowS: 8 };
    expect(alarmLevel(1, nullPeriod, fresh)).toBe("stale");
  });
});

describe("staleAfterS", () => {
  it("is the 5s floor below a 5/3 s period, 3x the period above it", () => {
    expect(staleAfterS(1)).toBe(5);
    expect(staleAfterS(5)).toBe(15);
    expect(staleAfterS(null)).toBe(5);
    expect(staleAfterS(undefined)).toBe(5);
  });
});
