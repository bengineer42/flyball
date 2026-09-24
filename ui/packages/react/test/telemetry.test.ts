import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ControllerOut, RigClient, StreamHandlers, Subscription } from "@flyball/client";
import { TelemetryStore, emptyTrace, emptyControllerView, controllerSetpointKey, controllerNameFromSetpointKey, PLAYBACK_DEBOUNCE_MS, PLAYBACK_MARGIN_S } from "../src/store/telemetry.js";

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

const controller = (t: number, output: number | null): ControllerOut => ({
  name: "heaters.heater1",
  label: null,
  output_signal: "heaters.heater1",
  measured_signal: "furnace.zone1",
  default: true,
  mode: "regulating",
  law: { type: "pi" },
  feedforward: { type: "none" },
  output_unit: "W",
  reference: 100,
  setpoint: 100,
  correction: 0,
  output,
  expected: null,
  delivered_correction: null,
  measured: { signal: "furnace.zone1", time_ns: t * 1e9, value: 99 },
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

  it("earliestS is null before any signal has a point, then the oldest first row across every signal loaded", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    store.subscribeLatest("furnace.zone1", () => undefined, 0);
    store.subscribeLatest("furnace.zone2", () => undefined, 0);
    expect(store.earliestS()).toBeNull();
    send("samples", samples(["furnace", 10, { zone1: 20 }]));
    vi.advanceTimersByTime(20);
    expect(store.earliestS()).toBe(10);
    // A signal seeded further back (a longer-lived one, or history landing) pulls the earliest back with it.
    send("samples", samples(["furnace", 5, { zone2: 15 }]));
    vi.advanceTimersByTime(20);
    expect(store.earliestS()).toBe(5);
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
      sessionSignals: async () => [{ address: "furnace.zone1" }],
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

  it("seeds a value the stream never carried from the rig's own latest, and only that", async () => {
    const signal = (name: string, address: string, latest: { time_ns: number; value: unknown } | null) => ({ name, address, latest, access: "rp", dtype: "str" });
    const { rig, send } = fakeRig({
      devices: async () => [
        {
          name: "blender",
          signals: [signal("mode", "blender.mode", { time_ns: 90e9, value: "blend" }), signal("humidity", "blender.humidity", { time_ns: 90e9, value: 40 }), signal("stop", "blender.last.stop", null)],
        },
      ],
    });
    const store = new TelemetryStore(rig);
    store.subscribeLatest("blender.mode", () => undefined);
    send("samples", samples(["blender", 104, { humidity: 55 }])); // live before the seed lands: kept
    const seeded = store.seed(["blender.mode", "blender.humidity", "blender.last.stop"]);
    await vi.runAllTimersAsync();
    await seeded;
    expect(store.latestValue("blender.mode")).toEqual({ t: 90, value: "blend" });
    expect(store.latestValue("blender.humidity")).toEqual({ t: 104, value: 55 });
    expect(store.latestValue("blender.last.stop")).toBeUndefined();
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

  it("keeps controller ticks by output address with nulls where it had none, replacing a resent tick", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    store.subscribeController("heaters.heater1", () => undefined);
    send("controllers", { controllers: [controller(1, 5)] });
    send("controllers", { controllers: [controller(1, 6)] }); // resent on reconnect
    send("controllers", { controllers: [controller(2, null)] });
    const view = store.readController("heaters.heater1", emptyControllerView());
    expect(view.t).toEqual([1, 2]);
    expect(view.output).toEqual([6, null]);
    expect(view.measured).toEqual([99, 99]);
    expect(view.expected).toEqual([null, null]);
    expect(store.controller("heaters.heater1")?.output).toBeNull();
    expect(store.controller("heaters.heater1")?.measured_signal).toBe("furnace.zone1");
    expect(Object.keys(store.controllers())).toEqual(["heaters.heater1"]);
  });

  it("keys a controller's setpoint distinctly from any signal address", () => {
    expect(controllerSetpointKey("heaters.heater1")).toBe("controller-setpoint:heaters.heater1");
    expect(controllerNameFromSetpointKey("controller-setpoint:heaters.heater1")).toBe("heaters.heater1");
    expect(controllerNameFromSetpointKey("heaters.heater1")).toBeNull(); // a plain signal address is never mistaken for one
  });

  it("reads a controller's setpoint through `read`, as a plain trace with gaps as NaN", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    const key = controllerSetpointKey("heaters.heater1");
    store.subscribeController("heaters.heater1", () => undefined);
    send("controllers", { controllers: [controller(1, 5)] }); // setpoint 100 (see `controller()`)
    send("controllers", { controllers: [{ ...controller(2, 5), reference: null, setpoint: null, output: null, correction: null }] }); // setpointOf finds nothing
    const view = store.read(key, emptyTrace());
    expect(view.t).toEqual([1, 2]);
    expect(view.v[0]).toBe(100);
    expect(Number.isNaN(view.v[1])).toBe(true);
  });

  it("wakes a `subscribeTrace` subscriber on a controller tick when given its setpoint key, mixed with real addresses", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    const cb = vi.fn();
    const stop = store.subscribeTrace(["furnace.zone1", controllerSetpointKey("heaters.heater1")], cb, 0);
    send("samples", samples(["furnace", 1, { zone1: 20 }]));
    vi.advanceTimersByTime(20);
    expect(cb).toHaveBeenCalledTimes(1);
    send("controllers", { controllers: [controller(1, 5)] });
    vi.advanceTimersByTime(20);
    expect(cb).toHaveBeenCalledTimes(2);
    stop();
    send("controllers", { controllers: [controller(2, 5)] });
    vi.advanceTimersByTime(20);
    expect(cb).toHaveBeenCalledTimes(2); // unsubscribed from both streams
  });

  it("seeds controller ticks matched by (output, measured signal) across sessions", async () => {
    const ticks = vi.fn(async (_id: number, name: string) =>
      name === "heaters.heater1" ? [{ controller: name, offset_ns: 1e9, mode: "regulating", correction: 1, measured: 50, setpoint: 100, output: 40, expected: null, delivered_correction: null }] : [],
    );
    const { rig } = fakeRig({
      sessions: async () => [{ id: 3, start_ns: 100e9, end_ns: null }],
      clock: async () => ({ now_ns: 110e9, start_time_ns: 100e9, elapsed_ns: 10e9, tags: {}, speed: 1 }),
      recording: async () => ({ id: 3, start_ns: 100e9, end_ns: null }),
      controllers: async () => [controller(110, 7)],
      sessionControllers: async () => [
        { name: "heaters.heater1", measured: "furnace.zone1", law: {}, feedforward: null },
        { name: "heaters.heater2", measured: "furnace.zone2", law: {}, feedforward: null },
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
    expect(view.output).toEqual([40, 7]);
    expect(view.reference).toEqual([100, 100]);
  });

  it("keeps device runs by name", () => {
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
    send("samples", { runs: [run("heaters", 2, 3)] });
    vi.advanceTimersByTime(20);
    expect(one).toHaveBeenCalledTimes(1); // another device's read does not wake furnace's subscriber
    expect(store.deviceVersion("heaters")).toBe(2);
  });

  it("keeps activities by name as they register and settle", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    const cb = vi.fn();
    store.subscribeActivities(cb, 0);
    const activity = (name: string, outcome: string) => ({ name, message: null, outcome, since_ns: 0, timeout_s: null, prompt: true });
    send("activities", { activities: [activity("step-1", "pending")] });
    send("activities", { activities: [activity("step-1", "fired"), activity("step-2", "pending")] });
    vi.advanceTimersByTime(20);
    expect(cb).toHaveBeenCalledTimes(1);
    expect(store.activities()["step-1"]?.outcome).toBe("fired");
    expect(store.activities()["step-2"]?.outcome).toBe("pending");
    expect(store.activitiesVersionNow()).toBe(2);
  });

  it("caps events and drops the resent ones", () => {
    const { rig, send } = fakeRig();
    const store = new TelemetryStore(rig);
    store.subscribeEvents(() => undefined);
    const ev = (t: number) => ({ time_ns: t, severity: "info", subject_kind: "rig", subject: "s", code: "k", message: "m", details: null });
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
    store.subscribeActivities(() => undefined);
    expect(() => send("samples", { error: "no rig attached" })).not.toThrow();
    expect(() => send("activities", { error: "no rig attached" })).not.toThrow();
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
    // Shown as "connecting" (reconnecting) immediately, not "closed" (offline) -- a drop and an
    // immediate reopen is normal churn; only escalate to offline if it stays down a while.
    expect(store.openStatuses()).toEqual(["connecting"]);
    vi.advanceTimersByTime(2999);
    expect(store.openStatuses()).toEqual(["connecting"]);
    vi.advanceTimersByTime(1);
    expect(store.openStatuses()).toEqual(["closed"]);
  });

  it("a socket the store let go closes late without leaving its stream 'connecting'", () => {
    const { rig, open, closed } = fakeRig();
    const store = new TelemetryStore(rig, { graceMs: 100 });
    const stop = store.subscribeController(null, () => undefined);
    open.get("controllers")!.onOpen?.();
    stop();
    vi.advanceTimersByTime(200);
    expect(closed).toEqual(["controllers"]);
    expect(store.status().controllers).toBe("idle");
    open.get("controllers")!.onClose?.("closed"); // the browser's close event, after the fact
    vi.advanceTimersByTime(5000);
    expect(store.status().controllers).toBe("idle");
    expect(store.openStatuses()).toEqual([]);
  });

  it("a reconnect before the offline grace elapses cancels it, staying open", () => {
    const { rig, open } = fakeRig();
    const store = new TelemetryStore(rig);
    store.subscribeWrites(null, () => undefined);
    open.get("samples")!.onOpen?.();
    open.get("samples")!.onClose?.("error");
    vi.advanceTimersByTime(1000);
    open.get("samples")!.onOpen?.();
    expect(store.openStatuses()).toEqual(["open"]);
    vi.advanceTimersByTime(5000); // the cancelled offline timer must not fire late and override "open"
    expect(store.openStatuses()).toEqual(["open"]);
  });

  describe("playback", () => {
    /** A session that started at t=0 and holds `a.x` = t for every whole second, and one controller's ticks. */
    const session = { id: 7, startS: 0, windowS: 10 };
    const historyRig = () => {
      const series = vi.fn(async (_id: number, address: string, q: { start_ns: number; end_ns: number }) => {
        const points: Array<{ offset_ns: number; value: unknown }> = [];
        // `a.mode` is an enum the session kept only when it changed: "heating" at t=20 and nothing since; every other address is t itself.
        if (address === "a.mode") {
          if (q.start_ns <= 20e9 && 20e9 < q.end_ns) points.push({ offset_ns: 20e9, value: "heating" });
        } else for (let t = Math.ceil(q.start_ns / 1e9); t * 1e9 < q.end_ns; t++) points.push({ offset_ns: t * 1e9, value: t });
        return { signal: { address, dtype: address === "a.mode" ? "enum" : "float" }, points, downsample: null };
      });
      const ticks = vi.fn(async (_id: number, _name: string, q: { start_ns: number; end_ns: number }) => {
        const out = [];
        for (let t = Math.ceil(q.start_ns / 1e9); t * 1e9 < q.end_ns; t++) out.push({ controller: "heaters.heater1", offset_ns: t * 1e9, mode: "regulating", correction: 0, measured: t, setpoint: 100, output: 2 * t, expected: null, delivered_correction: null });
        return out;
      });
      const fake = fakeRig({
        series,
        ticks,
        sessionSignals: async () => [{ address: "a.x" }, { address: "a.mode" }, { address: "furnace.zone1" }],
        sessionControllers: async () => [{ name: "heaters.heater1", measured: "furnace.zone1" }],
      });
      return { ...fake, series, ticks };
    };
    const flushFetch = async () => {
      vi.advanceTimersByTime(PLAYBACK_DEBOUNCE_MS + 1);
      for (let i = 0; i < 8; i++) await Promise.resolve();
      vi.advanceTimersByTime(20);
    };

    it("serves the window ending at `atS` from the live ring when it reaches back far enough, without a request", () => {
      const { rig, send, series } = historyRig();
      const store = new TelemetryStore(rig);
      const cb = vi.fn();
      store.subscribeLatest("a.x", cb, 0);
      for (let t = 1; t <= 100; t++) send("samples", samples(["a", t, { x: t }]));
      vi.advanceTimersByTime(20);
      const calls = cb.mock.calls.length;
      store.playback(50, session);
      vi.advanceTimersByTime(20);
      expect(cb.mock.calls.length).toBe(calls + 1); // woken once by the seek
      expect(store.playbackAtS()).toBe(50);
      expect(store.latest("a.x")).toEqual({ t: 50, v: 50 });
      expect(store.latestValue("a.x")).toEqual({ t: 50, value: 50 });
      const view = store.read("a.x", emptyTrace());
      expect(view.t[0]).toBe(50 - session.windowS - PLAYBACK_MARGIN_S < 1 ? 1 : 50 - session.windowS - PLAYBACK_MARGIN_S);
      expect(view.t[view.t.length - 1]).toBe(50);
      expect(store.count("a.x")).toBe(view.t.length);
      expect(series).not.toHaveBeenCalled();
      // The live clock and the live ring carry on underneath.
      expect(store.nowS()).toBe(100);
    });

    it("live samples fill the ring silently while paused; resume wakes once and shows them with no gap", () => {
      const { rig, send } = historyRig();
      const store = new TelemetryStore(rig);
      const cb = vi.fn();
      store.subscribeLatest("a.x", cb, 0);
      for (let t = 1; t <= 100; t++) send("samples", samples(["a", t, { x: t }]));
      vi.advanceTimersByTime(20);
      store.playback(50, session);
      vi.advanceTimersByTime(20);
      const calls = cb.mock.calls.length;
      const version = store.version("a.x");
      for (let t = 101; t <= 110; t++) send("samples", samples(["a", t, { x: t }]));
      vi.advanceTimersByTime(20);
      expect(cb.mock.calls.length).toBe(calls); // not woken: the panel shows the past
      expect(store.latest("a.x")).toEqual({ t: 50, v: 50 });
      store.playback(null);
      vi.advanceTimersByTime(20);
      expect(cb.mock.calls.length).toBe(calls + 1);
      expect(store.version("a.x")).toBeGreaterThan(version);
      expect(store.playbackAtS()).toBeNull();
      expect(store.latest("a.x")).toEqual({ t: 110, v: 110 });
      expect(store.read("a.x", emptyTrace()).t.length).toBe(110);
    });

    it("fetches the window from the session when the live ring does not reach it, once per settled seek", async () => {
      const { rig, send, series } = historyRig();
      const store = new TelemetryStore(rig);
      const cb = vi.fn();
      store.subscribeLatest("a.x", cb, 0);
      for (let t = 500; t <= 520; t++) send("samples", samples(["a", t, { x: t }]));
      vi.advanceTimersByTime(20);
      // Three seeks inside the debounce: one request, for the last.
      store.playback(300, session);
      store.playback(200, session);
      store.playback(100, session);
      expect(store.latest("a.x")).toBeUndefined(); // nothing yet: not the live value
      expect(series).not.toHaveBeenCalled();
      await flushFetch();
      expect(series).toHaveBeenCalledTimes(1);
      const [, address, query] = series.mock.calls[0]!;
      expect(address).toBe("a.x");
      expect(query.start_ns).toBe((100 - session.windowS - PLAYBACK_MARGIN_S) * 1e9);
      expect(query.end_ns).toBe(100 * 1e9 + 1); // half-open: the point on `atS` is kept
      expect(store.latest("a.x")).toEqual({ t: 100, v: 100 });
      expect(store.read("a.x", emptyTrace()).t.length).toBe(session.windowS + PLAYBACK_MARGIN_S + 1);
      expect(cb).toHaveBeenCalled();
    });

    it("drops a fetch that lands after playback moved on, and asks nothing of a signal the session did not declare", async () => {
      const { rig, send, series } = historyRig();
      const store = new TelemetryStore(rig);
      store.subscribeLatest("a.x", () => undefined, 0);
      store.subscribeLatest("b.y", () => undefined, 0);
      send("samples", samples(["a", 500, { x: 500 }], ["b", 500, { y: 1 }]));
      store.playback(100, session);
      vi.advanceTimersByTime(PLAYBACK_DEBOUNCE_MS + 1);
      await Promise.resolve();
      store.playback(null); // resumed while the request is in flight
      await flushFetch();
      expect(series).toHaveBeenCalledTimes(1);
      expect(series.mock.calls.every(([, address]) => address === "a.x")).toBe(true); // b.y undeclared: never asked
      expect(store.latest("a.x")).toEqual({ t: 500, v: 500 }); // live again, the stale window discarded
      expect(store.latestValue("b.y")).toEqual({ t: 500, value: 1 });
    });

    it("a subscriber arriving while paused gets the past too, and a bool/str reads what the session recorded", async () => {
      const { rig, send, series } = historyRig();
      const store = new TelemetryStore(rig);
      store.subscribeLatest("b.y", () => undefined, 0); // opens the socket; undeclared, so never fetched
      send("samples", { samples: [{ node: "a", time_ns: 500e9, values: { x: 500, mode: "off" } }] });
      store.playback(100, session);
      await flushFetch();
      expect(series).toHaveBeenCalledTimes(0); // nobody asked for `a.x` yet
      store.subscribeLatest("a.x", () => undefined, 0);
      await flushFetch();
      expect(series).toHaveBeenCalledTimes(1);
      expect(store.latest("a.x")).toEqual({ t: 100, v: 100 });
      expect(store.latestValue("a.mode")).toBeUndefined(); // nobody asked for it yet
      store.subscribeLatest("a.mode", () => undefined, 0);
      await flushFetch();
      // Nothing in the window [30, 100]: the row before it is still what the mode read at `atS`, so it is asked for and found.
      expect(series.mock.calls.filter(([, a]) => a === "a.mode").map(([, , q]) => [q.start_ns, q.end_ns])).toEqual([[30e9, 100e9 + 1], [0, 30e9]]);
      expect(store.latestValue("a.mode")).toEqual({ t: 20, value: "heating" });
      expect(store.latest("a.mode")).toBeUndefined(); // never on a ring
      expect(store.reading("a.x")).toMatchObject({ t: 100, value: 100, quality: "ok" });
      store.playback(null);
      expect(store.reading("a.mode")).toMatchObject({ t: 500, value: "off", quality: "ok" });
      expect(store.latestValue("a.mode")).toEqual({ t: 500, value: "off" });
    });

    it("a controller keeps its live state but reads and trends from the window", async () => {
      const { rig, send, ticks } = historyRig();
      const store = new TelemetryStore(rig);
      store.subscribeLatest("furnace.zone1", () => undefined, 0);
      store.subscribeController(null, () => undefined, 0);
      for (let t = 90; t <= 100; t++) {
        send("samples", samples(["furnace", t, { zone1: t }]));
        send("controllers", { controllers: [controller(t, 2 * t)] });
      }
      vi.advanceTimersByTime(20);
      expect(store.controller("heaters.heater1")?.measured?.value).toBe(99);
      // Within what the live ring holds (it starts at 90; the window from 50 - 70 asks for a fetch).
      store.playback(95, session);
      expect(store.readController("heaters.heater1", emptyControllerView()).t).toEqual([]); // fetch pending
      await flushFetch();
      expect(ticks).toHaveBeenCalledTimes(1);
      const trend = store.readController("heaters.heater1", emptyControllerView());
      expect(trend.t[trend.t.length - 1]).toBe(95);
      expect(trend.output[trend.t.length - 1]).toBe(190);
      const c = store.controller("heaters.heater1")!;
      expect(c.measured).toEqual({ signal: "furnace.zone1", time_ns: 95e9, value: 95, quality: "ok" });
      expect(c.output).toBe(200); // commanded: live
      expect(store.controller("heaters.heater1")).toBe(c); // the same object until something changes
      store.playback(null);
      expect(store.controller("heaters.heater1")?.measured?.value).toBe(99);
      expect(store.readController("heaters.heater1", emptyControllerView()).t.length).toBe(11);
    });
  });
});
