// @vitest-environment jsdom
/** The faceplate says what latches or holds its controller, and offers Reset for its own fault latch only. */
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
vi.mock("uplot", () => ({ default: class { static paths = { stepped: () => () => ({}) }; setData() {} setSize() {} destroy() {} } }));
import type { Condition, ControllerOut, Request, Response, SignalOut, Transport } from "@flyball/client";
import { RigProvider } from "../src/provider.js";
import { ControllerPanel } from "../src/panels/ControllerPanel.js";

afterEach(cleanup);

const transport: Transport = {
  base: "",
  async request({ path }: Request): Promise<Response> {
    if (path === "/api/clock") return { status: 200, json: { start_time_ns: 0, now_ns: 0, elapsed_ns: 0, speed: 1 } };
    return { status: 200, json: [] };
  },
  stream: () => ({ close: () => undefined }),
};

const SOURCE = { name: "temp", address: "chamber.temp", access: "r", label: "", quantity: "temperature", unit: "°C", dimension: null, dtype: "float", shape: [], role: "measurement", initial: null, range: null, precision: 1, warning: null, alarm: null, poll_s: 1, limits: null, latest: null, write: null } as unknown as SignalOut;
const BASE = { name: "heater.demand", label: null, output_signal: "heater.demand", measured_signal: "chamber.temp", default: false, mode: "manual", law: null, feedforward: { type: "none" }, output_unit: "W", reference: 50, setpoint: 50, correction: 0, output: 0, expected: 0, delivered_correction: 0, measured: null } as unknown as ControllerOut;

const panel = (controller: ControllerOut, extra: Record<string, unknown> = {}) =>
  render(createElement(RigProvider, { transport }, createElement(ControllerPanel, { controller, source: SOURCE, trends: false, ...extra })));

describe("the faceplate's latch line", () => {
  it("names the stop and its own fault latch, with Reset for the latter only", () => {
    const resets: string[] = [];
    panel({ ...BASE, latched: ["stop", "on_fault:heater.demand"] }, { onReset: (c: string) => resets.push(c) });
    const line = screen.getByTestId("controller-latch");
    expect(line.textContent).toContain("Latched by the software stop");
    expect(line.textContent).toContain("Latched by its fault action");
    expect(screen.getAllByTestId("controller-reset")).toHaveLength(1);
    fireEvent.click(screen.getByTestId("controller-reset"));
    expect(resets).toEqual(["on_fault:heater.demand"]);
  });

  it("shows a permissive holding it, and no Reset to a viewer", () => {
    const held: Condition = { code: "not_permitted", severity: "warning", message: "door open: held", since_ns: 0, scope: "controller", subject: "heater.demand" };
    panel({ ...BASE, latched: ["on_fault:heater.demand"] }, { conditions: [held], onReset: () => undefined, canOperate: false });
    expect(screen.getByTestId("controller-latch").textContent).toContain("Held: door open: held");
    expect(screen.queryByTestId("controller-reset")).toBeNull();
  });

  it("reads its own fault latch from its conditions when the snapshot carries none", () => {
    const latched: Condition = { code: "latched", severity: "error", message: "on_fault: manual", since_ns: 0, scope: "controller", subject: "heater.demand" };
    panel({ ...BASE }, { conditions: [latched], onReset: () => undefined });
    expect(screen.getByTestId("controller-latch").textContent).toContain("Latched by its fault action");
    expect(screen.getAllByTestId("controller-reset")).toHaveLength(1);
  });

  it("is absent when nothing holds it", () => {
    panel({ ...BASE, latched: [] });
    expect(screen.queryByTestId("controller-latch")).toBeNull();
  });
});
