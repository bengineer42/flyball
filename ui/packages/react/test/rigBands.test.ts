import { describe, expect, it } from "vitest";
import type { Event, RigClient, StreamHandlers, Subscription } from "@flyball/client";
import { alarmLevel } from "@flyball/client";
import { TelemetryStore } from "../src/store/telemetry.js";

/** The rig's band alarms reach the store from `/api/health` and are kept by the events' raised/cleared edges. */
function fakeRig(conditions: unknown[]) {
  const open = new Map<string, StreamHandlers>();
  const rig = {
    stream(path: string, handlers: StreamHandlers): Subscription {
      open.set(path, handlers);
      return { close: () => undefined };
    },
    health: async () => ({ conditions }),
    events: async () => [],
    sessions: async () => [],
  } as unknown as RigClient;
  return { rig, send: (path: string, message: unknown) => open.get(path)!.onMessage(message) };
}

const edge = (t: number, code: string, subject: string, e: "raised" | "cleared"): Event =>
  ({ time_ns: t * 1e9, code, severity: code === "band_alarm" ? "error" : "warning", scope: "signal", subject, message: "", edge: e, details: {} }) as unknown as Event;

const settle = () => new Promise((r) => setTimeout(r, 0));

describe("the rig's band alarms in the store", () => {
  it("is unknown until the rig's conditions have been read, then follows them", async () => {
    const { rig } = fakeRig([{ code: "band_alarm", severity: "error", scope: "signal", subject: "furnace.zone1", message: "", since_ns: 0 }]);
    const store = new TelemetryStore(rig);
    expect(store.bandOf("furnace.zone1")).toBeUndefined();
    store.seedBands();
    await settle();
    expect(store.bandOf("furnace.zone1")).toBe("alarm");
    expect(store.bandOf("furnace.zone2")).toBe("ok");
  });

  it("is kept by raised and cleared edges; a clear drops only the level it names", async () => {
    const { rig, send } = fakeRig([]);
    const store = new TelemetryStore(rig);
    store.seedBands();
    await settle();
    const stop = store.subscribeEvents(() => undefined);
    send("events", { events: [edge(1, "band_warning", "furnace.zone2", "raised")] });
    expect(store.bandOf("furnace.zone2")).toBe("warn");
    send("events", { events: [edge(2, "band_alarm", "furnace.zone2", "raised")] });
    send("events", { events: [edge(3, "band_warning", "furnace.zone2", "cleared")] });
    expect(store.bandOf("furnace.zone2")).toBe("alarm");
    send("events", { events: [edge(4, "band_alarm", "furnace.zone2", "cleared")] });
    expect(store.bandOf("furnace.zone2")).toBe("ok");
    stop();
  });
});

describe("alarmLevel with the rig's band", () => {
  const signal = { warning: [0, 10] as [number, number], alarm: [-5, 20] as [number, number] };
  it("shows the rig's word over the value's own check", () => {
    expect(alarmLevel(50, signal, undefined, "ok")).toBe("ok");
    expect(alarmLevel(5, signal, undefined, "alarm")).toBe("alarm");
  });
  it("checks the value itself only without a rig feed", () => {
    expect(alarmLevel(50, signal)).toBe("alarm");
  });
  it("stale still comes first", () => {
    expect(alarmLevel(5, signal, { periodS: 1, lastSampleS: 0, nowS: 100 }, "alarm")).toBe("stale");
  });
});
