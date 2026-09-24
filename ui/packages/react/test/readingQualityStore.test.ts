import { afterEach, describe, expect, it, vi } from "vitest";
import type { Condition, ControllerOut, RigClient, StreamHandlers, Subscription } from "@flyball/client";
import { alarmLevel, describeQuality, qualityOfFlag, flagOfQuality } from "@flyball/client";
import { TelemetryStore, emptyTrace, emptyControllerView, PLAYBACK_DEBOUNCE_MS } from "../src/store/telemetry.js";
import { toBreaks, align } from "../src/panels/MultiSeries.js";
import { fallbackGapS } from "../src/panels/TimeSeries.js";
import { noValue } from "../src/panels/quality.js";

// The store and the words for a reading with no value; the panels that show them are in `readingQuality.test.tsx`.

afterEach(() => vi.useRealTimers());

/** A rig whose streams the test drives; `health` answers what the test gives. */
function fakeRig(overrides: Record<string, unknown> = {}) {
  const open = new Map<string, StreamHandlers>();
  const rig = {
    stream(path: string, handlers: StreamHandlers): Subscription {
      open.set(path, handlers);
      return { close: () => undefined };
    },
    sessions: async () => [],
    clock: async () => ({ now_ns: 0, start_time_ns: 0, elapsed_ns: 0, tags: {}, speed: 1 }),
    recording: async () => null,
    controllers: async () => [],
    events: async () => [],
    ...overrides,
  } as unknown as RigClient;
  const send = (path: string, message: unknown) => open.get(path)!.onMessage(message);
  return { rig, send };
}

describe("the store keeps a reading with no value as a break, with why", () => {
  it("a null value is NaN on the ring (a break), null from latest(), and keeps quality, reason and the last usable value", () => {
    vi.useFakeTimers();
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    store.subscribeLatest("f.t", () => undefined, 0);
    send("samples", { samples: [{ node: "f", time_ns: 1e9, values: { t: 20 } }] });
    send("samples", { samples: [{ node: "f", time_ns: 2e9, values: { t: null }, quality: { t: "stale" }, reason: { t: "silent" } }] });
    expect(store.latest("f.t")).toEqual({ t: 2, v: null });
    expect(store.reading("f.t")).toEqual({ t: 2, value: null, quality: "stale", reason: "silent", lastUsable: { t: 1, value: 20 } });
    send("samples", { samples: [{ node: "f", time_ns: 3e9, values: { t: 21 }, caveats: { t: { at_limit: "high" } } }] });
    const view = store.read("f.t", emptyTrace());
    expect(view.t).toEqual([1, 2, 3]);
    expect(toBreaks(view.v)).toEqual([20, null, 21]); // the chart breaks at 2 only
    expect(store.reading("f.t")).toEqual({ t: 3, value: 21, quality: "ok", caveats: { at_limit: "high" } });
  });

  it("a no-value reading that arrived before the seed still learns the rig's last usable value", async () => {
    vi.useFakeTimers();
    const devices = async () => [{ signals: [{ address: "f.t", latest: { time_ns: 9e9, value: null, quality: "invalid", reason: "open" }, last_usable: { time_ns: 4e9, value: 19.5, quality: "ok" } }] }];
    const { rig, send } = fakeRig({ devices });
    const store = new TelemetryStore(rig);
    store.subscribeLatest("f.t", () => undefined, 0);
    send("samples", { samples: [{ node: "f", time_ns: 9e9, values: { t: null }, quality: { t: "invalid" }, reason: { t: "open" } }] });
    expect(store.reading("f.t")?.lastUsable).toBeUndefined();
    void store.seed(["f.t"]);
    vi.advanceTimersByTime(1);
    for (let i = 0; i < 8; i++) await Promise.resolve();
    expect(store.reading("f.t")).toEqual({ t: 9, value: null, quality: "invalid", reason: "open", lastUsable: { t: 4, value: 19.5 } });
  });

  it("a history point with no value (flag) is a break in playback, with its quality read off the flag", async () => {
    vi.useFakeTimers();
    const series = vi.fn(async () => ({
      signal: { address: "f.t", dtype: "float" },
      points: [
        { offset_ns: 1e9, value: 5, flag: null },
        { offset_ns: 2e9, value: null, flag: 1 },
      ],
      downsample: null,
    }));
    const { rig } = fakeRig({ series, sessionSignals: async () => [{ address: "f.t" }], sessionControllers: async () => [] });
    const store = new TelemetryStore(rig);
    store.subscribeLatest("f.t", () => undefined, 0);
    store.playback(2, { id: 1, startS: 0, windowS: 60 });
    vi.advanceTimersByTime(PLAYBACK_DEBOUNCE_MS + 1);
    for (let i = 0; i < 8; i++) await Promise.resolve();
    expect(series).toHaveBeenCalledTimes(1);
    expect(store.latest("f.t")).toEqual({ t: 2, v: null });
    expect(store.reading("f.t")).toEqual({ t: 2, value: null, quality: "invalid", lastUsable: { t: 1, value: 5 } });
    expect(toBreaks(store.read("f.t", emptyTrace()).v)).toEqual([5, null]);
  });

  it("a re-applied tick's measured is alignment (undefined), a tick with no reading a break (null)", async () => {
    vi.useFakeTimers();
    const tick = (t: number, measured: number | null, reapplied = false) => ({ controller: "h.p", offset_ns: t * 1e9, mode: "regulating", correction: 0, measured, setpoint: 1, output: 2, expected: null, delivered_correction: null, reapplied });
    const ticks = vi.fn(async () => [tick(1, 10), tick(2, null, true), tick(3, null), tick(4, 11)]);
    const { rig, send } = fakeRig({ ticks, sessionSignals: async () => [], sessionControllers: async () => [{ name: "h.p", measured: "f.t" }] });
    const store = new TelemetryStore(rig);
    store.subscribeController(null, () => undefined, 0);
    send("controllers", { controllers: [{ name: "h.p", measured_signal: "f.t", measured: null, reference: 1 } as unknown as ControllerOut] });
    store.playback(4, { id: 1, startS: 0, windowS: 60 });
    vi.advanceTimersByTime(PLAYBACK_DEBOUNCE_MS + 1);
    for (let i = 0; i < 8; i++) await Promise.resolve();
    const view = store.readController("h.p", emptyControllerView());
    expect(view.measured).toEqual([10, undefined, null, 11]);
    // `align` keeps the difference: uPlot joins across `undefined` and breaks at `null`.
    expect(align([{ t: view.t, v: view.measured }])[1]).toEqual([10, undefined, null, 11]);
  });

  it("holds the rig's conditions: band_unknown is its own level, a controller's frozen is there to read, and an edge clears it", async () => {
    vi.useFakeTimers();
    const conditions: Condition[] = [
      { code: "band_unknown", severity: "error", message: "no value", since_ns: 1, subject_kind: "signal", subject: "f.t" },
      { code: "frozen", severity: "warning", message: "f.t has no value", since_ns: 1, subject_kind: "controller", subject: "h.p", details: { quality: "invalid", reason: "open" } },
    ];
    const { rig, send } = fakeRig({ health: async () => ({ conditions }) });
    const store = new TelemetryStore(rig);
    store.subscribeEvents(() => undefined, 0);
    expect(store.bandOf("f.t")).toBeUndefined(); // not read yet
    store.seedBands();
    for (let i = 0; i < 4; i++) await Promise.resolve();
    expect(store.bandOf("f.t")).toBe("unknown");
    expect(store.bandOf("f.other")).toBe("ok");
    expect(store.conditionsOf("h.p").map((c) => c.code)).toEqual(["frozen"]);
    send("events", { events: [{ time_ns: 5, severity: "error", subject_kind: "signal", subject: "f.t", code: "band_unknown", message: "", details: {}, edge: "cleared" }] });
    expect(store.bandOf("f.t")).toBe("ok");
  });
});

describe("levels and words", () => {
  const banded = { warning: [0, 10] as [number, number], alarm: [-5, 20] as [number, number] };
  it("band_unknown is its own level, never alarm; stale comes from the reading's quality", () => {
    expect(alarmLevel(null, banded, "unknown", "invalid")).toBe("unknown");
    expect(alarmLevel(null, banded, "ok", "stale")).toBe("stale");
    expect(alarmLevel(null, banded, undefined, "not_applicable")).toBe("ok");
  });
  it("a stored flag and a quality map to each other", () => {
    expect(qualityOfFlag(4)).toEqual({ quality: "stale", reason: "device_offline" });
    expect(qualityOfFlag(17)).toEqual({ quality: "ok", caveats: { at_limit: "high" } });
    expect(flagOfQuality("stale", "device_offline")).toBe(4);
    expect(flagOfQuality("ok", undefined, { at_limit: "low" })).toBe(16);
    expect(flagOfQuality("ok")).toBe(0);
  });
  it("noValue: a dash and why for a fault, an ellipsis while pending, nothing with a value", () => {
    const fmt = (v: unknown) => String(v);
    expect(noValue({ value: null, quality: "invalid", reason: "open", lastUsable: { t: 0, value: 3 } }, fmt)).toMatchObject({ glyph: "—", label: "invalid: open" });
    expect(noValue({ value: null, quality: "pending" }, fmt)).toMatchObject({ glyph: "…", label: "pending", hint: "no usable reading yet" });
    expect(noValue({ value: 3, quality: "ok" }, fmt)).toBeNull();
    expect(describeQuality("stale", "device_hung")).toBe("stale: device hung");
  });
  it("a chart's fallback gap is the rig's stale_after_s; null (not judged) never breaks; an older server's is 3·poll_s", () => {
    expect(fallbackGapS({ poll_s: 1, stale_after_s: 5 })).toBe(5);
    expect(fallbackGapS({ poll_s: 1, stale_after_s: null })).toBeUndefined();
    expect(fallbackGapS({ poll_s: 2 })).toBe(6);
    expect(fallbackGapS({ poll_s: null })).toBeUndefined();
  });
});

