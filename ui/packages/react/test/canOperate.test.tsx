// @vitest-environment jsdom
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

// `ControllerPanel` imports `uplot` at module scope, which probes `matchMedia` (unavailable in
// jsdom) as a side effect of import alone -- stubbed out since these tests never render a trend.
vi.mock("uplot", () => ({
  default: class {
    static paths = { stepped: () => () => ({}) };
    setData() {}
    setSize() {}
    destroy() {}
  },
}));
import type { ControllerOut, Request, Response, SignalOut, StreamHandlers, Subscription, Transport } from "@flyball/client";
import { RigProvider } from "../src/provider.js";
import { WritePanel } from "../src/panels/WritePanel.js";
import { ControllerPanel } from "../src/panels/ControllerPanel.js";

/** A transport with sane empty answers for everything `WritePanel`/`ControllerPanel` seed from on mount. */
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

const SIGNAL: SignalOut = {
  name: "demand",
  address: "heater.demand",
  access: "rw",
  label: "",
  quantity: "power",
  unit: "W",
  dimension: null,
  dtype: "float",
  shape: [],
  role: "demand",
  tags: {},
  initial: null,
  range: null,
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

describe("WritePanel gates on canOperate", () => {
  it("leaves the entry enabled by default (canOperate omitted)", () => {
    render(withRig(createElement(WritePanel, { signal: SIGNAL })));
    expect((screen.getByLabelText("Demand demand") as HTMLInputElement).disabled).toBe(false);
  });

  it("disables the entry and Set button when canOperate is false, without hiding them", () => {
    render(withRig(createElement(WritePanel, { signal: SIGNAL, canOperate: false })));
    const entry = screen.getByLabelText("Demand demand") as HTMLInputElement;
    const set = screen.getByRole("button", { name: "Set" }) as HTMLButtonElement;
    expect(entry.disabled).toBe(true);
    expect(set.disabled).toBe(true);
    expect(set.hidden).toBe(false);
    expect(entry.hidden).toBe(false);
  });
});

describe("ControllerPanel gates controls/headerControls on canOperate", () => {
  it("leaves caller-supplied controls clickable by default", () => {
    render(
      withRig(
        createElement(ControllerPanel, {
          controller: CONTROLLER,
          source: { ...SIGNAL, address: "chamber.temp", unit: "°C" },
          trends: false,
          controls: createElement("button", { type: "button" }, "Move"),
          headerControls: createElement("button", { type: "button" }, "Stop"),
        }),
      ),
    );
    expect(screen.getByRole("button", { name: "Move" }).closest("[aria-disabled]")).toBeNull();
    expect(screen.getByRole("button", { name: "Stop" }).closest("[aria-disabled]")).toBeNull();
  });

  it("dims and blocks clicks into controls/headerControls when canOperate is false, without hiding them", () => {
    render(
      withRig(
        createElement(ControllerPanel, {
          controller: CONTROLLER,
          source: { ...SIGNAL, address: "chamber.temp", unit: "°C" },
          trends: false,
          canOperate: false,
          controls: createElement("button", { type: "button" }, "Move"),
          headerControls: createElement("button", { type: "button" }, "Stop"),
        }),
      ),
    );
    const move = screen.getByRole("button", { name: "Move" });
    const stop = screen.getByRole("button", { name: "Stop" });
    const moveWrap = move.closest("[aria-disabled='true']") as HTMLElement | null;
    const stopWrap = stop.closest("[aria-disabled='true']") as HTMLElement | null;
    expect((move as HTMLElement).hidden).toBe(false);
    expect((stop as HTMLElement).hidden).toBe(false);
    expect(moveWrap).not.toBeNull();
    expect(stopWrap).not.toBeNull();
    expect(moveWrap!.style.pointerEvents).toBe("none");
    expect(stopWrap!.style.opacity).toBe("0.5");
  });
});
