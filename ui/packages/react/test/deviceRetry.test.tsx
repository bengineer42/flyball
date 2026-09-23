// @vitest-environment jsdom
/** An offline device backing off shows it is retrying, and offers "Retry now" rather than hiding the button while `running` stays true. */
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
vi.mock("uplot", () => ({ default: class {} }));
import type { DeviceOut, DeviceSchema, Request, Response, Transport } from "@flyball/client";
import { RigProvider } from "../src/provider.js";
import { DevicePanel } from "../src/panels/DevicePanel.js";

const transport: Transport = { base: "", request: async (_: Request): Promise<Response> => ({ status: 404, json: undefined }), stream: () => ({ close: () => undefined }) };
afterEach(cleanup);

const run = (over: Partial<DeviceOut["run"] & object>) => ({ period_s: 1, running: true, last_read_ns: 1e18, read_s: 0.01, missed: 0, reading_since_ns: null, consecutive_failures: 0, next_retry_ns: null, ...over });
const device = (r: ReturnType<typeof run>) => ({ name: "furnace", label: "Tube furnace", class_name: "SimDaq", driver: "sim_daq", kind: "device", signals: [], commands: [], conditions: [], run: r, inputs: {} }) as unknown as DeviceOut;
const schema = { name: "furnace", label: null, class_name: "SimDaq", driver: "sim_daq", description: null, readable: true, writable: false, config: {}, signals: {}, commands: {} } as unknown as DeviceSchema;

function show(r: ReturnType<typeof run>) {
  render(createElement(RigProvider, { transport }, createElement(DevicePanel, { device: device(r), schema, onRun: async () => undefined, onRestart: async () => undefined })));
}

describe("a device's run line", () => {
  it("polling: no restart button", () => {
    show(run({}));
    expect(screen.getByText(/polling/)).toBeTruthy();
    expect(screen.queryByTestId("device-restart")).toBeNull();
  });
  it("offline and backing off: says so and offers Retry now", () => {
    show(run({ consecutive_failures: 4, next_retry_ns: 1.1e18 }));
    expect(screen.getByText(/retrying · 4 failed/)).toBeTruthy();
    expect(screen.getByText(/next try/)).toBeTruthy();
    expect(screen.getByTestId("device-restart").textContent).toBe("Retry now");
  });
  it("stopped: Restart", () => {
    show(run({ running: false }));
    expect(screen.getByTestId("device-restart").textContent).toBe("Restart");
  });
});
