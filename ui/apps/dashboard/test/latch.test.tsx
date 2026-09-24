// @vitest-environment jsdom
/** The latch banner says what holds the rig and resets it; the stop confirmation lists what the stop writes; the report opens per device. */
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
vi.mock("uplot", () => ({ default: class { static paths = { stepped: () => () => ({}) }; setData() {} setSize() {} destroy() {} } }));
import { RigProvider } from "@flyball/react";
import type { AuthInfo, Request, Response, Transport } from "@flyball/client";
import { AuthProvider } from "../src/auth.js";
import { LatchBannerFor } from "../src/LatchBanner.js";
import { StopButton } from "../src/StopButton.js";

afterEach(cleanup);

const OPERATOR: AuthInfo = { v: 2, shape: "local", scheme: "local", user: { id: "local:console", name: "local", kind: "human" }, verbs: ["operate", "read"], anonymous: "none", login: { password: false, token: false, passkey: false, sso: null } };

function transport(extra: (r: Request) => Response | undefined, asked: Request[] = []): Transport {
  return {
    base: "",
    async request(r: Request): Promise<Response> {
      asked.push(r);
      if (r.path === "/api/auth") return { status: 200, json: OPERATOR };
      return extra(r) ?? { status: 404, json: { detail: "no" } };
    },
    stream: () => ({ close: () => undefined }),
  };
}

const wrap = (t: Transport, child: unknown) => createElement(AuthProvider, { transport: t }, createElement(RigProvider, { transport: t }, child as never));

const HEALTH = {
  ok: true,
  rig: "furnace",
  uptime_s: 1,
  devices: {},
  controllers: {},
  conditions: [],
  alarms: { warn: 0, alarm: 0, unknown: 0, max_level: 0 },
  activities: [],
  recording: false,
  stopped: { by: "local:console", at_ns: 1_790_000_000_000_000_000, reason: "operator" },
  latches: [
    { subject_kind: "rig", subject: "furnace", cause: "stop" },
    { subject_kind: "controller", subject: "heaters.heater1", cause: "on_fault:heaters.heater1" },
  ],
};

describe("the latch banner", () => {
  it("names each latch and resets the one asked for", async () => {
    const asked: Request[] = [];
    let refreshed = 0;
    const t = transport((r) => (r.path === "/api/rig/reset" ? { status: 200, json: {} } : undefined), asked);
    render(wrap(t, createElement(LatchBannerFor, { health: HEALTH as never, onReset: () => refreshed++ })));
    const banner = await screen.findByTestId("latch-banner");
    expect(banner.textContent).toMatch(/Rig stopped by local:console at .*: operator/);
    expect(banner.textContent).toMatch(/Latched by heaters\.heater1's fault action \(heaters\.heater1\)/);
    await waitFor(() => expect((screen.getByTestId("reset-on_fault:heaters.heater1") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByTestId("reset-on_fault:heaters.heater1"));
    fireEvent.click(await screen.findByRole("button", { name: "Reset" }));
    await waitFor(() => expect(refreshed).toBe(1));
    expect(asked.filter((r) => r.path === "/api/rig/reset").map((r) => r.body)).toEqual([{ cause: "on_fault:heaters.heater1" }]);
  });

  it("is absent when nothing is latched", () => {
    const t = transport(() => undefined);
    render(wrap(t, createElement(LatchBannerFor, { health: { ...HEALTH, stopped: null, latches: [] } as never, onReset: () => undefined })));
    expect(screen.queryByTestId("latch-banner")).toBeNull();
  });
});

describe("the software stop", () => {
  it("lists what it will write before, and each device's outcome after", async () => {
    const plan = {
      stopped: null,
      outputs: [
        { address: "heaters.heater1", device: "heaters", stop: 0, origin: "off", command: null, controller: "heaters.heater1", covered_if_flyball_dies: false, warnings: [] },
        { address: "fan.speed", device: "fan", stop: "keep", origin: "you_said", command: null, controller: null, covered_if_flyball_dies: false, warnings: ["left energised"] },
      ],
    };
    const report = {
      at_ns: 1,
      actor: { sub: "local:console", sid: "s", kind: "human", via: "http", detail: "" },
      reason: "",
      devices: { heaters: { state: "stopped", message: "", written: { "heaters.heater1": 0 } }, fan: { state: "unchanged", message: "keep", kept: { "fan.speed": 40 } } },
      program_interrupted: true,
      controllers_manual: ["heaters.heater1"],
      interim: false,
      latched: true,
    };
    const t = transport((r) => (r.path === "/api/rig/stop" ? { status: 200, json: r.method === "GET" ? plan : report } : undefined));
    render(wrap(t, createElement(StopButton)));
    fireEvent.click(await screen.findByTestId("stop-button"));
    const listed = await screen.findByTestId("stop-plan");
    expect(listed.textContent).toContain("heaters.heater1off (0)");
    expect(listed.textContent).toContain("fan.speedkept (you said)left energised");
    fireEvent.click(screen.getByRole("button", { name: "Software stop" }));
    fireEvent.click(await screen.findByTestId("stop-details"));
    const table = await screen.findByTestId("stop-report");
    expect(table.textContent).toContain("heaters.heater1 = 0");
    expect(table.textContent).toContain("fan.speed = 40");
    expect(screen.getByText(/The running program was interrupted\. In manual: heaters\.heater1\. The rig is latched/)).toBeTruthy();
  });
});
