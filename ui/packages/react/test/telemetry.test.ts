import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RigClient, StreamHandlers, Subscription } from "@flyball/client";
import { TelemetryStore, emptyTrace, emptyLoopView } from "../src/store/telemetry.js";

/** A rig whose streams are driven by the test. */
function fakeRig() {
  const open = new Map<string, StreamHandlers>();
  const closed: string[] = [];
  const rig = {
    stream(path: string, handlers: StreamHandlers): Subscription {
      open.set(path, handlers);
      return { close: () => closed.push(path) };
    },
    sessions: async () => [],
    clock: async () => ({ now_ns: 0, start_time_ns: 0, elapsed_ns: 0, tags: {} }),
    recording: async () => null,
    loops: async () => [],
    events: async () => [],
  } as unknown as RigClient;
  const send = (path: string, message: unknown) => open.get(path)!.onMessage(message);
  return { rig, open, closed, send };
}

const sample = (source: string, seq: number, t: number, values: Record<string, number>) => ({ source, seq, time_ns: t * 1e9, values });

describe("TelemetryStore", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("opens a stream on the first subscriber and closes it after the grace period", () => {
    const { rig, open, closed } = fakeRig();
    const store = new TelemetryStore(rig, { graceMs: 100 });
    const a = store.subscribeLatest("z.t", () => undefined);
    const b = store.subscribeTrace(["z.t"], () => undefined);
    expect([...open.keys()]).toEqual(["samples"]);
    expect(store.openSockets()).toBe(1);
    a();
    b();
    vi.advanceTimersByTime(50);
    expect(closed).toEqual([]);
    // Back before the grace period is up (a Strict Mode remount): the socket stays.
    const c = store.subscribeLatest("z.t", () => undefined);
    vi.advanceTimersByTime(200);
    expect(closed).toEqual([]);
    c();
    vi.advanceTimersByTime(200);
    expect(closed).toEqual(["samples"]);
    expect(store.openSockets()).toBe(0);
  });

  it("holds samples per channel and tells only the subscribers of that channel, once per flush", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    const zone = vi.fn();
    const other = vi.fn();
    store.subscribeLatest("zone1.temperature", zone, 0);
    store.subscribeLatest("zone2.temperature", other, 0);
    send("samples", sample("zone1", 1, 1, { temperature: 20 }));
    send("samples", sample("zone1", 2, 2, { temperature: 21 }));
    expect(zone).not.toHaveBeenCalled(); // not before the flush
    vi.advanceTimersByTime(20);
    expect(zone).toHaveBeenCalledTimes(1);
    expect(other).not.toHaveBeenCalled();
    expect(store.latest("zone1.temperature")).toEqual({ t: 2, v: 21 });
    expect(store.version("zone1.temperature")).toBe(2);
    expect(store.read("zone1.temperature", emptyTrace())).toEqual({ t: [1, 2], v: [20, 21] });
    expect(store.nowS()).toBe(2);
  });

  it("paces a subscriber to its cadence and delivers what it missed", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    const cb = vi.fn();
    vi.spyOn(Math, "random").mockReturnValue(0); // subscribers start at a random phase within their period; pin it
    store.subscribeTrace(["a.x"], cb, 100);
    send("samples", sample("a", 1, 1, { x: 1 }));
    vi.advanceTimersByTime(20);
    expect(cb).not.toHaveBeenCalled(); // phase 0: the first call is one period in
    vi.advanceTimersByTime(100);
    expect(cb).toHaveBeenCalledTimes(1);
    send("samples", sample("a", 2, 2, { x: 2 }));
    send("samples", sample("a", 3, 3, { x: 3 }));
    vi.advanceTimersByTime(20);
    expect(cb).toHaveBeenCalledTimes(1); // too soon
    vi.advanceTimersByTime(100);
    expect(cb).toHaveBeenCalledTimes(2);
    expect(store.count("a.x")).toBe(3);
  });

  it("flags a seq gap for a while", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    store.subscribeLatest("a.x", () => undefined);
    send("samples", sample("a", 1, 1, { x: 1 }));
    expect(store.lagging()).toBe(false);
    send("samples", sample("a", 5, 2, { x: 1 }));
    expect(store.lagging()).toBe(true);
    vi.advanceTimersByTime(6000);
    expect(store.lagging()).toBe(false);
  });

  it("keeps loop ticks with nulls where the loop had none, replacing a resent tick", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    store.subscribeLoop("h", () => undefined);
    const loop = (t: number, demand: number | null) => ({
      name: "h",
      label: null,
      channel: { source: "z", measurand: "t", unit: "°C", label: "t", range: null, precision: null },
      default: true,
      mode: "regulating",
      law: {},
      feedforward: { kind: "none" },
      demand_unit: "W",
      reference: 100,
      setpoint: 100,
      correction: null,
      demand,
      expected: null,
      delivered_correction: null,
      reading: { time_ns: t * 1e9, value: 99 },
    });
    send("loops", { loops: [loop(1, 5)] });
    send("loops", { loops: [loop(1, 6)] }); // resent on reconnect
    send("loops", { loops: [loop(2, null)] });
    const view = store.readLoop("h", emptyLoopView());
    expect(view.t).toEqual([1, 2]);
    expect(view.demand).toEqual([6, null]);
    expect(view.reading).toEqual([99, 99]);
    expect(view.correction).toEqual([null, null]);
    expect(store.loop("h")?.demand).toBeNull();
  });

  it("caps events and drops the resent ones", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    store.subscribeEvents(() => undefined);
    const ev = (t: number) => ({ time_ns: t, level: "INFO", scope: "rig", subject: "s", kind: "k", message: "m", details: null });
    send("events", { events: [ev(1), ev(2)] });
    const held = store.events();
    send("events", { events: [ev(1), ev(2)] });
    expect(store.events()).toBe(held); // nothing new: same array
    send("events", { events: [ev(3)] });
    expect(store.events().map((e) => e.time_ns)).toEqual([1, 2, 3]);
  });

  it("reports stream status for opened streams only", () => {
    const { rig, open } = fakeRig();
    const store = new TelemetryStore(rig);
    expect(store.openStatuses()).toEqual([]);
    store.subscribeActuators(null, () => undefined);
    expect(store.openStatuses()).toEqual(["connecting"]);
    open.get("actuators")!.onOpen?.();
    expect(store.status().actuators).toBe("open");
    open.get("actuators")!.onClose?.();
    expect(store.openStatuses()).toEqual(["closed"]);
  });
});
