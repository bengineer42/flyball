// @vitest-environment jsdom
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
// `@flyball/react`'s barrel re-exports ControllerPanel, which imports `uplot` at module scope --
// a side effect that probes `matchMedia` (unavailable in jsdom) just from importing the package,
// long before any chart renders. Stubbed out, as `packages/react/test/client.test.ts` does.
vi.mock("uplot", () => ({
  default: class {
    static paths = { stepped: () => () => ({}) };
    setData() {}
    setSize() {}
    destroy() {}
  },
}));
import { RigProvider } from "@flyball/react";
import type { AuthInfo, Request, Response, Transport } from "@flyball/client";
import { AuthProvider } from "../src/auth.js";
import { StopButton } from "../src/StopButton.js";

afterEach(cleanup);

const OPERATOR: AuthInfo = {
  v: 2,
  shape: "local",
  scheme: "local",
  user: { id: "local:console", name: "local", kind: "human" },
  verbs: ["operate", "read"],
  anonymous: "none",
  login: { password: false, token: false, passkey: false, sso: null },
};

const READ_ONLY: AuthInfo = {
  v: 2,
  shape: "password",
  scheme: "login",
  user: { id: "local:viewer", name: "viewer", kind: "human" },
  verbs: ["read"],
  anonymous: "read",
  login: { password: true, token: false, passkey: false, sso: null },
};

/** Answers `/api/auth` from `info` and `POST /api/rig/stop` per `stop`; everything else 404s, which
 * is fine -- `StopButton` and `AuthProvider` touch nothing else. */
function fakeTransport(info: AuthInfo, stop: (request: Request) => Response): Transport {
  return {
    base: "",
    async request(request: Request): Promise<Response> {
      if (request.method === "GET" && request.path === "/api/auth") return { status: 200, json: info };
      if (request.method === "POST" && request.path === "/api/rig/stop") return stop(request);
      return { status: 404, json: undefined };
    },
    stream: () => ({ close: () => undefined }),
  };
}

function withProviders(info: AuthInfo, stop: (request: Request) => Response) {
  const transport = fakeTransport(info, stop);
  return render(
    createElement(
      AuthProvider,
      { transport },
      createElement(RigProvider, { transport }, createElement(StopButton)),
    ),
  );
}

describe("StopButton shows iff this caller holds OPERATE", () => {
  it("renders for an operator", async () => {
    withProviders(OPERATOR, () => ({ status: 200, json: {} }));
    await waitFor(() => expect(screen.getByTestId("stop-button")).toBeTruthy());
  });

  it("renders nothing at all for a read-only caller -- not just disabled", async () => {
    withProviders(READ_ONLY, () => ({ status: 200, json: {} }));
    // Give the initial /api/auth answer a tick to land, then confirm the button never appears.
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.queryByTestId("stop-button")).toBeNull();
  });
});

describe("StopButton reports what the stop did, or why it failed", () => {
  it("confirms, then says what each device did and that the rig is latched", async () => {
    const report = {
      at_ns: 1,
      actor: { sub: "local:console", sid: "s1", kind: "human", via: "http", detail: "" },
      reason: "",
      devices: { heater: { state: "stopped", detail: "", written: { "heater.power": 0 } }, fan: { state: "unchanged", detail: "keep", kept: { "fan.speed": 40 } }, valve: { state: "failed", detail: "bus timeout" } },
      program_interrupted: false,
      controllers_manual: [],
      interim: false,
      latched: true,
    };
    withProviders(OPERATOR, () => ({ status: 200, json: report }));
    await waitFor(() => screen.getByTestId("stop-button"));
    fireEvent.click(screen.getByTestId("stop-button"));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByRole("button", { name: "Software stop" }));
    await waitFor(() => expect(screen.getByTestId("stop-result").textContent).toMatch(/software stop/i));
    const text = screen.getByTestId("stop-result").textContent ?? "";
    expect(text).toContain("1 stopped, 1 unchanged, 1 failed (valve: bus timeout)");
    expect(text).toMatch(/latched until a person resets it/);
    expect(text).not.toMatch(/nothing written|safe/i);
    expect(screen.getByTestId("stop-result").className).toMatch(/warning/i);
  });

  it("a refusal is shown as a failure with the server's own reason, never as a stop", async () => {
    withProviders(OPERATOR, () => ({ status: 501, json: { detail: "no stopper on this runner" } }));
    await waitFor(() => screen.getByTestId("stop-button"));
    fireEvent.click(screen.getByTestId("stop-button"));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByRole("button", { name: "Software stop" }));
    const result = await waitFor(() => screen.getByTestId("stop-result"));
    expect(result.textContent).toContain("no stopper on this runner");
    expect(result.textContent).not.toMatch(/not wired up yet/i);
    expect(result.className).not.toMatch(/success/i);
  });

  it("cancelling the confirm dialog makes no request", async () => {
    let called = false;
    withProviders(OPERATOR, () => {
      called = true;
      return { status: 200, json: {} };
    });
    await waitFor(() => screen.getByTestId("stop-button"));
    fireEvent.click(screen.getByTestId("stop-button"));
    await waitFor(() => screen.getByRole("dialog"));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(called).toBe(false);
  });
});
