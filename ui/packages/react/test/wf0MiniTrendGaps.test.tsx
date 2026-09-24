// @vitest-environment jsdom
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";

// wf0: `ControllerPanel`'s collapsed `MiniTrend` (the Process/Drive faceplate sparklines) had its
// own separate uPlot instance with `spanGaps: true`, the same bug `MultiSeries` had -- a real gap
// (NaN) would draw as a straight line. Fixed the same way: `spanGaps: false` plus `toBreaks`
// (reused from `MultiSeries.tsx`) converting a NaN to a real `null` break before `setData`.
// `uplot` probes `matchMedia` at import time (unavailable in jsdom) -- stubbed the same way
// `controllerTrendExpand.test.tsx` does, but this mock also records what it was constructed and
// fed with, so the test can inspect the actual data/options `MiniTrend` hands to uPlot.
const instances: Array<{ options: unknown; data: unknown; setDataCalls: unknown[] }> = [];
vi.mock("uplot", () => ({
  default: class {
    static paths = { stepped: () => () => ({}) };
    cursor = { idx: null };
    data: unknown;
    options: unknown;
    setDataCalls: unknown[] = [];
    constructor(options: unknown, data: unknown) {
      this.options = options;
      this.data = data;
      instances.push({ options, data, setDataCalls: this.setDataCalls });
    }
    setData(d: unknown) {
      this.data = d;
      this.setDataCalls.push(d);
    }
    setSize() {}
    setLegend() {}
    setScale() {}
    destroy() {}
  },
}));
class FakeResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", FakeResizeObserver);
import type { ControllerOut, Request, Response, SignalOut, StreamHandlers, Subscription, Transport } from "@flyball/client";
import { RigProvider } from "../src/provider.js";
import { ControllerPanel } from "../src/panels/ControllerPanel.js";
import type { ControllerTrace } from "../src/hooks/useControllers.js";

function fakeTransport(): Transport {
  return {
    async request({ path }: Request): Promise<Response> {
      if (path === "/api/devices") return { status: 200, json: [] };
      if (path === "/api/controllers") return { status: 200, json: [] };
      if (path === "/api/clock") return { status: 200, json: { start_time_ns: 0, now_ns: 0, elapsed_ns: 0, tags: {}, speed: 1 } };
      if (path === "/api/history/sessions") return { status: 200, json: [] };
      if (path === "/api/recording") return { status: 200, json: null };
      return { status: 404, json: undefined };
    },
    stream(_path: string, _handlers: StreamHandlers): Subscription {
      return { close: () => undefined };
    },
  };
}

afterEach(() => {
  cleanup();
  instances.length = 0;
});

function withRig(children: React.ReactNode) {
  return createElement(RigProvider, { transport: fakeTransport() }, children);
}

const SOURCE: SignalOut = {
  name: "temp",
  address: "chamber.temp",
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
  alarm: null,
  poll_s: null,
  limits: null,
  latest: null,
  write: null,
};

const CONTROLLER: ControllerOut = {
  name: "heater.demand",
  label: null,
  output_signal: "heater.demand",
  measured_signal: "chamber.temp",
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
  measured: null,
};

// A real dead-time break landing as NaN in the process reading -- the same sentinel the ring's
// gap insertion and a controller setpoint's null-as-NaN conversion use elsewhere.
const HISTORY_WITH_GAP: ControllerTrace = {
  t: [0, 1, 2, 3],
  reference: [50, 50, 50, 50],
  measured: [48, Number.NaN, 52, 49],
  output: [10, 10, 10, 10],
  expected: [10, 10, 10, 10],
  correction: [0, 0, 0, 0],
};

describe("ControllerPanel's MiniTrend breaks on a real gap instead of spanning it", () => {
  it("draws spanGaps: false on every mini-trend series", () => {
    render(withRig(createElement(ControllerPanel, { controller: CONTROLLER, source: SOURCE, trends: true, history: HISTORY_WITH_GAP })));
    expect(instances.length).toBeGreaterThan(0); // Process and Drive both constructed
    for (const { options } of instances) {
      const series = (options as { series: Array<{ spanGaps?: boolean }> }).series;
      for (const s of series.slice(1)) expect(s.spanGaps).toBe(false); // series[0] is the x placeholder, no spanGaps
    }
  });

  it("converts a real-gap NaN in the process reading to null before setData, not leaving it as NaN", () => {
    render(withRig(createElement(ControllerPanel, { controller: CONTROLLER, source: SOURCE, trends: true, history: HISTORY_WITH_GAP })));
    // The Process mini trend is the first instance constructed (Process, then Drive).
    const process = instances[0]!;
    const lastCall = process.setDataCalls.at(-1) as (number | null)[][] | undefined;
    expect(lastCall).toBeDefined();
    const readingSeries = lastCall![1]!; // [t, reading, setpoint]
    expect(readingSeries).toContain(null);
    expect(readingSeries.some((v) => typeof v === "number" && Number.isNaN(v))).toBe(false);
  });
});
