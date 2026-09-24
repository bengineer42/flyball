// @vitest-environment jsdom
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

// `uplot` probes `matchMedia` (not in jsdom) on import; these tests draw no chart.
vi.mock("uplot", () => ({
  default: class {
    static paths = { stepped: () => () => ({}) };
    static join = () => [[]];
    setData() {}
    setSize() {}
    destroy() {}
  },
}));
import type { Condition, ControllerOut, DeviceOut, Request, Response, SignalOut, StreamHandlers, Subscription, Transport } from "@flyball/client";
import { RigProvider } from "../src/provider.js";
import { useTraceRef } from "../src/store/hooks.js";
import { Readout } from "../src/panels/Readout.js";
import { Gauge } from "../src/panels/Gauge.js";
import { ControllerPanel } from "../src/panels/ControllerPanel.js";
import { DeviceSignals } from "../src/panels/DeviceSignals.js";

afterEach(cleanup);

// Panels, fed through a real provider whose transport the test drives.

const SIGNAL: SignalOut = {
  name: "t",
  address: "f.t",
  access: "rp",
  label: "",
  quantity: "temperature",
  unit: "°C",
  dimension: null,
  dtype: "float",
  shape: [],
  role: "readout",
  tags: {},
  initial: null,
  range: [0, 100],
  precision: 1,
  warning: null,
  alarm: [10, 90],
  poll_s: 1,
  stale_after_s: 5,
  limits: null,
  quality: "ok",
  readback: null,
  on_no_value: "fire",
  latest: null,
  last_usable: null,
  write: null,
} as SignalOut;

function driven(conditions: Condition[] = []) {
  const open = new Map<string, StreamHandlers>();
  const transport: Transport = {
    async request({ path }: Request): Promise<Response> {
      if (path === "/api/devices" || path === "/api/controllers" || path === "/api/history/sessions") return { status: 200, json: [] };
      if (path === "/api/clock") return { status: 200, json: { start_time_ns: 0, now_ns: 0, elapsed_ns: 0, speed: 1 } };
      if (path === "/api/recording") return { status: 200, json: null };
      if (path === "/api/health") return { status: 200, json: { conditions } };
      if (path.startsWith("/api/events")) return { status: 200, json: [] };
      return { status: 404, json: undefined };
    },
    stream(path: string, handlers: StreamHandlers): Subscription {
      open.set(path, handlers);
      return { close: () => undefined };
    },
  };
  const send = (path: string, message: unknown) => act(() => open.get(path)!.onMessage(message));
  const wrap = (children: ReactNode) => createElement(RigProvider, { transport }, children);
  return { send, wrap };
}

function StoreTile() {
  const source = useTraceRef(["f.t"]);
  return createElement(Readout, { signal: SIGNAL, source, sparkline: false });
}

describe("panels show a reading with no value as a dash and why, never the value before it", () => {
  it("Readout: the rig's stale reading reads '— stale: device silent', stale, with the last usable value on hover", async () => {
    const { send, wrap } = driven();
    render(wrap(createElement(StoreTile)));
    send("/ws/samples", { samples: [{ node: "f", time_ns: 1e9, values: { t: 42 } }] });
    await waitFor(() => expect(screen.getByText("42.0")).toBeTruthy());
    send("/ws/samples", { samples: [{ node: "f", time_ns: 2e9, values: { t: null }, quality: { t: "stale" }, reason: { t: "silent" } }] });
    const badge = await screen.findByTestId("quality-badge");
    expect(badge.textContent).toBe("stale: device silent");
    expect(badge.getAttribute("title")).toMatch(/^last usable 42.0 °C at /);
    expect(screen.queryByText("42.0")).toBeNull();
    expect(screen.getByText("—")).toBeTruthy();
    expect(document.querySelector(".fb-alarm-stale")).not.toBeNull();
  });

  it("Readout: a usable value at a limit carries a quiet caveat mark, not an alarm", async () => {
    const { send, wrap } = driven();
    render(wrap(createElement(StoreTile)));
    send("/ws/samples", { samples: [{ node: "f", time_ns: 1e9, values: { t: 50 }, caveats: { t: { at_limit: "high" } } }] });
    const mark = await screen.findByTestId("caveat-mark");
    expect(mark.textContent).toBe("≥");
    expect(mark.getAttribute("title")).toBe("at the high limit");
    expect(document.querySelector(".fb-alarm-alarm")).toBeNull();
  });

  it("Gauge: no value draws no fill and says why", () => {
    render(createElement(Gauge, { signal: SIGNAL, value: 30, reading: { value: null, quality: "invalid", reason: "open circuit" }, band: "unknown" }));
    expect(screen.getByTestId("quality-badge").textContent).toBe("invalid: open circuit");
    expect(document.querySelector(".fb-alarm-unknown")).not.toBeNull();
    expect(screen.queryByText("30.0")).toBeNull();
  });

  it("ControllerPanel: a measured reading with no value shows its badge, not NaN; frozen shows in the header", async () => {
    const frozen: Condition = { code: "frozen", severity: "warning", message: "f.t has no value", since_ns: 1, scope: "controller", subject: "h.p", details: { quality: "invalid", reason: "open" } };
    const { wrap } = driven([frozen]);
    const controller = {
      name: "h.p",
      label: null,
      output_signal: "h.p",
      measured_signal: "f.t",
      default: false,
      mode: "regulating",
      law: null,
      feedforward: { type: "none" },
      output_unit: "W",
      reference: 50,
      setpoint: 50,
      correction: 0,
      output: 10,
      expected: 10,
      delivered_correction: 0,
      measured: { signal: "f.t", time_ns: 1e9, value: null, quality: "invalid", reason: "open" },
    } as ControllerOut;
    render(wrap(createElement(ControllerPanel, { controller, source: SIGNAL, trends: false })));
    expect(screen.getByTestId("quality-badge").textContent).toBe("invalid: open");
    expect(document.body.textContent).not.toContain("NaN");
    const banner = await screen.findByTestId("loop-banner");
    expect(banner.textContent).toContain("frozen");
    expect(banner.className).toBe("fb-loop-warn");
  });

  it("DeviceSignals: an input bound to a number is shown; a bound one with no value says why", () => {
    const { wrap } = driven();
    const device = {
      name: "blend",
      label: null,
      kind: "device",
      driver: "blend",
      class_name: "Blend",
      link: null,
      poll_s: null,
      signals: [],
      commands: [],
      inputs: {
        dry: { name: "dry", label: "", quantity: "humidity", unit: "%RH", bound: null, constant: 36.5, quality: "ok" },
        wet: { name: "wet", label: "", quantity: "humidity", unit: "%RH", bound: "f.t", quality: "stale", reason: "silent" },
      },
      consumers: {},
      sources: {},
      readable: false,
      writable: true,
      conditions: [],
      run: null,
    } as unknown as DeviceOut;
    render(wrap(createElement(DeviceSignals, { device })));
    expect(screen.getByTestId("input-constant").textContent).toBe("Dry = 36.5 %RH");
    expect(screen.getByText("stale: device silent")).toBeTruthy();
  });

  it("DeviceSignals: a demand the device only reports (access rp, a readback) is read-only: no entry, no Set", () => {
    const { wrap } = driven();
    const readback = { ...SIGNAL, name: "dry", address: "blend.dry", label: "Dry pump flow", role: "demand", access: "rp", unit: "L/min", readback: "sensed" } as SignalOut;
    const settable = { ...SIGNAL, name: "wet", address: "blend.wet", label: "Wet pump flow", role: "demand", access: "rpw", unit: "L/min" } as SignalOut;
    const device = { name: "blend", label: null, kind: "device", driver: "blend", class_name: "Blend", link: null, poll_s: null, signals: [readback, settable], commands: [], inputs: {}, consumers: {}, sources: {}, readable: true, writable: true, conditions: [], run: null } as unknown as DeviceOut;
    render(wrap(createElement(DeviceSignals, { device, sparkline: false })));
    expect(screen.getAllByRole("button", { name: "Set" })).toHaveLength(1); // the settable one only
    expect(screen.queryByLabelText(/Dry pump flow/)).toBeNull();
  });
});
