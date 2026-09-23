// @vitest-environment jsdom
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

// `ControllerPanel` (and the `MultiSeries` its expanded trend now renders through) import `uplot`
// at module scope, which probes `matchMedia` (unavailable in jsdom) as a side effect of import
// alone -- stubbed out, same as `canOperate.test.tsx`.
vi.mock("uplot", () => ({
  default: class {
    static paths = { stepped: () => () => ({}) };
    cursor = { idx: null };
    data = [[]];
    setData() {}
    setSize() {}
    setLegend() {}
    setScale() {}
    destroy() {}
  },
}));
// jsdom has no ResizeObserver; both `MiniTrend` and `MultiSeries` (its expanded stand-in) use one
// to follow their host's laid-out size.
class FakeResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", FakeResizeObserver);
import type { ControllerOut, Request, Response, SignalOut, StreamHandlers, Subscription, Transport } from "@flyball/client";
import { RigProvider } from "../src/provider.js";
import { ControllerPanel } from "../src/panels/ControllerPanel.js";

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

afterEach(cleanup);

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
  role: "output",
  tags: {},
  initial: null,
  range: [0, 100],
  precision: 1,
  warn: null,
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
  feedforward: { tag: "none" },
  output_unit: "W",
  reference: 50,
  setpoint: 50,
  correction: 0,
  output: 10,
  expected: 10,
  delivered_correction: 0,
  measured: null,
};

/**
 * The Controllers page's own charts (the faceplate's Process/Drive trends)
 * had no click-to-expand at all: this closes that gap by having a click open
 * the same full-screen overlay every other chart uses (`MultiSeries`'s own),
 * rather than building a second one.
 */
describe("ControllerPanel's trends open the same full-screen overlay as any other chart", () => {
  it("opens on a click, with the controller and trend named in the title, and closes on Escape", () => {
    render(withRig(createElement(ControllerPanel, { controller: CONTROLLER, source: SOURCE, trends: true })));

    expect(screen.queryByRole("dialog")).toBeNull();
    const trends = document.querySelectorAll(".fb-loop-mini");
    expect(trends.length).toBe(2); // Process, Drive

    fireEvent.click(trends[0]!);
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-label")).toContain("process");
    // The mini trend itself is gone while its full-size stand-in is open (one chart, not two).
    expect(document.querySelectorAll(".fb-loop-mini").length).toBe(1);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.querySelectorAll(".fb-loop-mini").length).toBe(2);
  });

  it("stays open through a double-click: the second click must not dismiss it", () => {
    // Double-clicking a controller's trend on a real rig showed nothing happening: the first
    // click opened the overlay, the second landed on its backdrop and closed it again.
    render(withRig(createElement(ControllerPanel, { controller: CONTROLLER, source: SOURCE, trends: true })));

    fireEvent.click(document.querySelectorAll(".fb-loop-mini")[0]!);
    fireEvent.click(document.querySelector(".fb-chart-expanded")!); // the tail of the double-click
    expect(screen.queryByRole("dialog")).not.toBeNull();
  });

  it("still dismisses on a backdrop click once the gesture has settled", () => {
    vi.useFakeTimers();
    try {
      render(withRig(createElement(ControllerPanel, { controller: CONTROLLER, source: SOURCE, trends: true })));
      fireEvent.click(document.querySelectorAll(".fb-loop-mini")[0]!);
      vi.setSystemTime(Date.now() + 1000);
      fireEvent.click(document.querySelector(".fb-chart-expanded")!);
      expect(screen.queryByRole("dialog")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("opens the Drive trend separately from the Process trend", () => {
    render(withRig(createElement(ControllerPanel, { controller: CONTROLLER, source: SOURCE, trends: true })));
    const trends = document.querySelectorAll(".fb-loop-mini");
    fireEvent.click(trends[1]!);
    expect(screen.getByRole("dialog").getAttribute("aria-label")).toContain("drive");
  });
});
