import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ControllerOut, RigClient, StreamHandlers, Subscription } from "@flyball/client";
import { TelemetryStore, emptyTrace, emptyControllerView } from "../src/store/telemetry.js";

/** A rig whose streams are driven by the test. */
function fakeRig(overrides: Partial<Record<string, unknown>> = {}) {
  const open = new Map<string, StreamHandlers>();
  const closed: string[] = [];
  const rig = {
    stream(path: string, handlers: StreamHandlers): Subscription {
      open.set(path, handlers);
      return { close: () => closed.push(path) };
    },
    sessions: async () => [],
    clock: async () => ({ now_ns: 0, start_time_ns: 0, elapsed_ns: 0, tags: {}, speed: 1 }),
    recording: async () => null,
    controllers: async () => [],
    events: async () => [],
    ...overrides,
  } as unknown as RigClient;
  const send = (path: string, message: unknown) => open.get(path)!.onMessage(message);
  return { rig, open, closed, send };
}

/** A `/ws/samples` frame: one sample per node, values keyed relative to the node. */
const samples = (...items: Array<[node: string, t: number, values: Record<string, number>]>) => ({
  samples: items.map(([node, t, values]) => ({ node, time_ns: t * 1e9, values })),
});

const controller = (t: number, demand: number | null): ControllerOut => ({
  name: "heaters.heater1",
  label: null,
  target: "heaters.heater1",
  source: "furnace.zone1",
  default: true,
  mode: "regulating",
  law: { tag: "PI" },
  feedforward: { tag: "none" },
  demand_unit: "W",
  reference: 100,
  setpoint: 100,
  correction: 0,
  demand,
  expected: null,
  delivered_correction: null,
  reading: { signal: "furnace.zone1", time_ns: t * 1e9, value: 99 },
});

describe("TelemetryStore", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("opens a stream on the first subscriber and closes it after the grace period", () => {
    const { rig, open, closed } = fakeRig();
    const store = new TelemetryStore(rig, { graceMs: 100 });
    const a = store.subscribeLatest("furnace.zone1", () => undefined);
    const b = store.subscribeTrace(["furnace.zone1"], () => undefined);
    expect([...open.keys()]).toEqual(["samples"]);
    expect(store.openSockets()).toBe(1);
    a();
    b();
    vi.advanceTimersByTime(50);
    expect(closed).toEqual([]);
    // Back before the grace period is up (a Strict Mode remount): the socket stays.
    const c = store.subscribeLatest("furnace.zone1", () => undefined);
    vi.advanceTimersByTime(200);
    expect(closed).toEqual([]);
    c();
    vi.advanceTimersByTime(200);
    expect(closed).toEqual(["samples"]);
    expect(store.openSockets()).toBe(0);
  });

  it("expands each node's sample to signal addresses and tells only that signal's subscribers, once per flush", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    const zone = vi.fn();
    const other = vi.fn();
    store.subscribeLatest("furnace.zone1", zone, 0);
    store.subscribeLatest("furnace.zone2", other, 0);
    send("samples", samples(["furnace", 1, { zone1: 20, zone3: 30 }]));
    send("samples", samples(["furnace", 2, { zone1: 21, zone3: 31 }]));
    expect(zone).not.toHaveBeenCalled(); // not before the flush
    vi.advanceTimersByTime(20);
    expect(zone).toHaveBeenCalledTimes(1);
    expect(other).not.toHaveBeenCalled();
    expect(store.latest("furnace.zone1")).toEqual({ t: 2, v: 21 });
    expect(store.latest("furnace.zone3")).toEqual({ t: 2, v: 31 });
    expect(store.latest("furnace.zone2")).toBeUndefined();
    expect(store.version("furnace.zone1")).toBe(2);
    expect(store.read("furnace.zone1", emptyTrace())).toEqual({ t: [1, 2], v: [20, 21] });
    expect(store.signalKeys()).toEqual(["furnace.zone1", "furnace.zone3"]);
    expect(store.nowS()).toBe(2);
  });

  it("feeds a number to the ring and to `latestValue`, but a bool/str/json to `latestValue` only", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    store.subscribeLatest("furnace.zone1", () => undefined, 0);
    send("samples", {
      samples: [
        {
          node: "furnace",
          time_ns: 1e9,
          values: { zone1: 20.5, running: true, mode: "heating", conditions: [{ kind: "offline", level: 40, message: "m", since_ns: 0 }] },
        },
      ],
    });
    // A number: on the ring (for a chart) and as the latest value.
    expect(store.latest("furnace.zone1")).toEqual({ t: 1, v: 20.5 });
    expect(store.latestValue("furnace.zone1")).toEqual({ t: 1, value: 20.5 });
    // A bool, a str (an enum) and json: never on the ring -- a chart must not see them -- but still the latest value.
    expect(store.latest("furnace.running")).toBeUndefined();
    expect(store.latestValue("furnace.running")).toEqual({ t: 1, value: true });
    expect(store.latest("furnace.mode")).toBeUndefined();
    expect(store.latestValue("furnace.mode")).toEqual({ t: 1, value: "heating" });
    expect(store.latest("furnace.conditions")).toBeUndefined();
    expect(store.latestValue("furnace.conditions")?.value).toEqual([{ kind: "offline", level: 40, message: "m", since_ns: 0 }]);
    // Every value, numeric or not, still bumps the signal's version and the node's sample.
    expect(store.version("furnace.running")).toBe(1);
    expect(store.sample("furnace")?.values.mode).toBe("heating");
  });

  it("keys a namespace's sample under its full address", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    const cb = vi.fn();
    store.subscribeSample("hum_sensors.dry", cb, 0);
    send("samples", samples(["hum_sensors.dry", 5, { humidity: 4.1, temperature: 21.9 }], ["hum_sensors.wet", 5, { humidity: 80 }]));
    vi.advanceTimersByTime(20);
    expect(cb).toHaveBeenCalledTimes(1);
    expect(store.latest("hum_sensors.dry.humidity")).toEqual({ t: 5, v: 4.1 });
    expect(store.latest("hum_sensors.wet.humidity")).toEqual({ t: 5, v: 80 });
    expect(store.sample("hum_sensors.dry")).toEqual({ node: "hum_sensors.dry", time_ns: 5e9, values: { humidity: 4.1, temperature: 21.9 } });
    expect(store.sampleVersion("hum_sensors.dry")).toBe(1);
    expect(store.sample("hum_sensors")).toBeUndefined();
  });

  it("holds several nodes in one frame", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    store.subscribeLatest("a.x", () => undefined);
    send("samples", samples(["a", 1, { x: 1 }], ["b", 1, { y: 2 }]));
    expect(store.latest("a.x")).toEqual({ t: 1, v: 1 });
    expect(store.latest("b.y")).toEqual({ t: 1, v: 2 });
  });

  it("paces a subscriber to its cadence and delivers what it missed", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    const cb = vi.fn();
    vi.spyOn(Math, "random").mockReturnValue(0); // subscribers start at a random phase within their period; pin it
    store.subscribeTrace(["a.x"], cb, 100);
    send("samples", samples(["a", 1, { x: 1 }]));
    vi.advanceTimersByTime(20);
    expect(cb).not.toHaveBeenCalled(); // phase 0: the first call is one period in
    vi.advanceTimersByTime(100);
    expect(cb).toHaveBeenCalledTimes(1);
    send("samples", samples(["a", 2, { x: 2 }]));
    send("samples", samples(["a", 3, { x: 3 }]));
    vi.advanceTimersByTime(20);
    expect(cb).toHaveBeenCalledTimes(1); // too soon
    vi.advanceTimersByTime(100);
    expect(cb).toHaveBeenCalledTimes(2);
    expect(store.count("a.x")).toBe(3);
  });

  it("seeds a signal's history by address under the live rows", async () => {
    const series = vi.fn(async (_id: number, address: string) => ({
      signal: { address },
      points: [
        { offset_ns: 0, value: 10 },
        { offset_ns: 1e9, value: 11 },
      ],
      downsample: null,
    }));
    const { rig, send } = fakeRig({
      sessions: async () => [{ id: 7, start_ns: 100e9, end_ns: null }],
      clock: async () => ({ now_ns: 105e9, start_time_ns: 100e9, elapsed_ns: 5e9, tags: {}, speed: 1 }),
      recording: async () => ({ id: 7, start_ns: 100e9, end_ns: null }),
      series,
    });
    const store = new TelemetryStore(rig);
    store.subscribeLatest("furnace.zone1", () => undefined);
    send("samples", samples(["furnace", 104, { zone1: 20 }]));
    const seeded = store.seed(["furnace.zone1"]);
    await vi.runAllTimersAsync();
    await seeded;
    expect(series).toHaveBeenCalledTimes(1);
    expect(series.mock.calls[0]![0]).toBe(7);
    expect(series.mock.calls[0]![1]).toBe("furnace.zone1");
    expect(store.read("furnace.zone1", emptyTrace())).toEqual({ t: [100, 101, 104], v: [10, 11, 20] });
  });

  it("keeps write states by signal address, joined from a sample's `writes` and its value", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    const one = vi.fn();
    const any = vi.fn();
    store.subscribeWrites("heaters.heater1", one, 0);
    store.subscribeWrites(null, any, 0);
    send("samples", {
      samples: [{ node: "heaters", time_ns: 0, values: { heater1: 500 }, writes: { heater1: { requested: null, at_limit: null, controller: "heaters.heater1" } } }],
    });
    send("samples", {
      samples: [{ node: "heaters", time_ns: 0, values: { heater2: 1000 }, writes: { heater2: { requested: 1200, at_limit: "high", controller: null } } }],
    });
    vi.advanceTimersByTime(20);
    expect(one).toHaveBeenCalledTimes(1);
    expect(any).toHaveBeenCalledTimes(1);
    expect(store.write("heaters.heater1")).toEqual({ value: 500, requested: null, at_limit: null, controller: "heaters.heater1" });
    expect(store.write("heaters.heater2")?.at_limit).toBe("high");
    expect(store.writeVersion("heaters.heater1")).toBe(1);
    expect(store.writeVersion()).toBe(2);
    const held = store.writes();
    expect(Object.keys(held)).toEqual(["heaters.heater1", "heaters.heater2"]);
  });

  it("keeps controller ticks by target address with nulls where it had none, replacing a resent tick", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    store.subscribeController("heaters.heater1", () => undefined);
    send("controllers", { controllers: [controller(1, 5)] });
    send("controllers", { controllers: [controller(1, 6)] }); // resent on reconnect
    send("controllers", { controllers: [controller(2, null)] });
    const view = store.readController("heaters.heater1", emptyControllerView());
    expect(view.t).toEqual([1, 2]);
    expect(view.demand).toEqual([6, null]);
    expect(view.reading).toEqual([99, 99]);
    expect(view.expected).toEqual([null, null]);
    expect(store.controller("heaters.heater1")?.demand).toBeNull();
    expect(store.controller("heaters.heater1")?.source).toBe("furnace.zone1");
    expect(Object.keys(store.controllers())).toEqual(["heaters.heater1"]);
  });

  it("seeds controller ticks matched by (target, source) across sessions", async () => {
    const ticks = vi.fn(async (_id: number, name: string) =>
      name === "heaters.heater1" ? [{ controller: name, offset_ns: 1e9, mode: "regulating", correction: 1, reading: 50, setpoint: 100, demand: 40, expected: null, delivered_correction: null }] : [],
    );
    const { rig } = fakeRig({
      sessions: async () => [{ id: 3, start_ns: 100e9, end_ns: null }],
      clock: async () => ({ now_ns: 110e9, start_time_ns: 100e9, elapsed_ns: 10e9, tags: {}, speed: 1 }),
      recording: async () => ({ id: 3, start_ns: 100e9, end_ns: null }),
      controllers: async () => [controller(110, 7)],
      sessionControllers: async () => [
        { name: "heaters.heater1", source: "furnace.zone1", law: {}, feedforward: null },
        { name: "heaters.heater2", source: "furnace.zone2", law: {}, feedforward: null },
      ],
      ticks,
    });
    const store = new TelemetryStore(rig);
    store.subscribeController(null, () => undefined);
    await store.seedControllers();
    expect(ticks).toHaveBeenCalledTimes(1);
    expect(ticks.mock.calls[0]![1]).toBe("heaters.heater1");
    const view = store.readController("heaters.heater1", emptyControllerView());
    expect(view.t).toEqual([101, 110]);
    expect(view.demand).toEqual([40, 7]);
    expect(view.reference).toEqual([100, 100]);
  });

  it("keeps device runs by name and derives a signal's period from its device", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    const one = vi.fn();
    store.subscribeDevices("furnace", one, 0);
    // A run carries no `state` any more: just the run and the runtime's own conditions. `/ws/devices`
    // is gone: a run rides on `/ws/samples`, under `runs`, beside whatever samples also changed.
    const run = (name: string, period_s: number, last: number) => ({ name, period_s, running: true, last_read_ns: last, conditions: [] });
    send("samples", { runs: [run("furnace", 0.5, 1), run("heaters", 2, 1)] });
    vi.advanceTimersByTime(20);
    expect(one).toHaveBeenCalledTimes(1);
    expect(store.deviceRun("furnace")?.period_s).toBe(0.5);
    expect(store.periodOf("furnace.zone1")).toBe(0.5);
    expect(store.periodOf("heaters.heater1")).toBe(2);
    expect(store.periodOf("nothing.here")).toBeUndefined();
    expect(store.devicePeriodsKey()).toBe("furnace=0.5,heaters=2");
    const key = store.devicePeriodsKey();
    send("samples", { runs: [run("heaters", 2, 3)] });
    vi.advanceTimersByTime(20);
    expect(one).toHaveBeenCalledTimes(1); // another device's read does not wake furnace's subscriber
    expect(store.devicePeriodsKey()).toBe(key);
    expect(store.deviceVersion("heaters")).toBe(2);
  });

  it("keeps waits by name as they register and settle", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    const cb = vi.fn();
    store.subscribeWaits(cb, 0);
    const wait = (name: string, outcome: string) => ({ name, message: null, outcome, since_ns: 0, timeout_s: null, prompt: true });
    send("waits", { waits: [wait("step-1", "pending")] });
    send("waits", { waits: [wait("step-1", "fired"), wait("step-2", "pending")] });
    vi.advanceTimersByTime(20);
    expect(cb).toHaveBeenCalledTimes(1);
    expect(store.waits()["step-1"]?.outcome).toBe("fired");
    expect(store.waits()["step-2"]?.outcome).toBe("pending");
    expect(store.waitsVersionNow()).toBe(2);
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

  it("ignores a no-rig notice on any stream", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    store.subscribeLatest("a.x", () => undefined);
    store.subscribeWrites(null, () => undefined); // rides the same "samples" socket
    store.subscribeWaits(() => undefined);
    expect(() => send("samples", { error: "no rig attached" })).not.toThrow();
    expect(() => send("waits", { error: "no rig attached" })).not.toThrow();
    expect(store.signalKeys()).toEqual([]);
  });

  it("reports stream status for opened streams only, `writes`/`devices` under `samples`'s", () => {
    const { rig, open } = fakeRig();
    const store = new TelemetryStore(rig);
    expect(store.openStatuses()).toEqual([]);
    store.subscribeWrites(null, () => undefined);
    expect([...open.keys()]).toEqual(["samples"]);
    expect(store.openStatuses()).toEqual(["connecting"]);
    open.get("samples")!.onOpen?.();
    expect(store.status().samples).toBe("open");
    open.get("samples")!.onClose?.("error");
    expect(store.openStatuses()).toEqual(["closed"]);
  });
});
