// @vitest-environment jsdom
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
// `@flyball/react`'s barrel re-exports ControllerPanel, which imports `uplot` at module scope --
// a side effect that probes `matchMedia` (unavailable in jsdom) just from importing the package,
// long before any chart renders. Stubbed out, as `StopButton.test.tsx` does.
vi.mock("uplot", () => ({
  default: class {
    static paths = { stepped: () => () => ({}) };
    setData() {}
    setSize() {}
    destroy() {}
  },
}));
// F3: a page that throws during render must not take the app bar (and the stop button) down with
// it. The Overview page (the default route) is swapped for one that always throws.
vi.mock("../src/pages/Overview.js", () => ({
  Overview: () => {
    throw new Error("boom from Overview");
  },
}));
import { RigProvider } from "@flyball/react";
import type { AuthInfo, Request, Response, Transport } from "@flyball/client";
import { AuthProvider } from "../src/auth.js";
import { App } from "../src/App.js";

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

/** Answers `/api/auth` from `info` and `GET /api/devices` from `devices`; everything else 404s
 * (streams close immediately) -- enough for `App`'s error/starting/loading branches, which return
 * before any page (and its own fetches) ever mounts. */
function fakeTransport(info: AuthInfo, devices: (request: Request) => Response): { transport: Transport; devicesCalls: number } {
  const state = { devicesCalls: 0 };
  const transport: Transport = {
    base: "",
    async request(request: Request): Promise<Response> {
      if (request.method === "GET" && request.path === "/api/auth") return { status: 200, json: info };
      if (request.method === "GET" && request.path === "/api/devices") {
        state.devicesCalls++;
        return devices(request);
      }
      return { status: 404, json: undefined };
    },
    stream: () => ({ close: () => undefined }),
  };
  return { transport, devicesCalls: state.devicesCalls };
}

function withProviders(info: AuthInfo, devices: (request: Request) => Response) {
  const { transport } = fakeTransport(info, devices);
  return render(
    createElement(
      AuthProvider,
      { transport },
      createElement(RigProvider, { transport }, createElement(App, { onSignIn: () => undefined })),
    ),
  );
}

describe("App: the runner starting", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  it("shows a non-alarming starting state, not the 'cannot reach' alert, for a 503 RigError", async () => {
    withProviders(OPERATOR, () => ({ status: 503, json: { detail: "The rig's runner is starting" } }));
    await waitFor(() => expect(screen.getByText(/starting/i)).toBeTruthy());
    expect(screen.queryByText(/cannot reach the rig/i)).toBeNull();
  });

  it("retries GET /api/devices on a backoff while starting, and stops alerting once it loads", async () => {
    let calls = 0;
    withProviders(OPERATOR, () => {
      calls++;
      if (calls < 3) return { status: 503, json: { detail: "The rig's runner is starting" } };
      return { status: 200, json: [] };
    });
    await waitFor(() => expect(screen.getByText(/starting/i)).toBeTruthy());
    const before = calls;

    // First retry, ~1s out.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });
    await waitFor(() => expect(calls).toBeGreaterThan(before));

    // Second retry, ~2s after the first (the backoff doubles): the third call succeeds.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });
    await waitFor(() => expect(screen.queryByText(/starting/i)).toBeNull());
    expect(screen.queryByText(/cannot reach the rig/i)).toBeNull();
  });

  it("a non-503 error still shows the permanent 'cannot reach' alert", async () => {
    withProviders(OPERATOR, () => ({ status: 500, json: { detail: "boom" } }));
    await waitFor(() => expect(screen.getByText(/cannot reach the rig/i)).toBeTruthy());
    expect(screen.queryByText(/^starting/i)).toBeNull();
  });

});

describe("App: a page that throws during render (F3)", () => {
  it("keeps the app bar and the stop button mounted instead of unmounting the whole root", async () => {
    withProviders(OPERATOR, () => ({ status: 200, json: [] }));
    // Before the fix, React 18 unmounts the whole root on an uncaught render error: the stop
    // button (inside the same tree as the page) would disappear along with the crashed page.
    await waitFor(() => expect(screen.getByTestId("stop-button")).toBeTruthy());
    expect(screen.getByText(/failed to render/i)).toBeTruthy();
    expect(screen.getByText(/boom from Overview/)).toBeTruthy();
  });
});
