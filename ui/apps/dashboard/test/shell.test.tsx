// @vitest-environment jsdom
/**
 * The app frame after the sidebar went (D-053): every former sidebar destination is reached from
 * the app bar -- dashboards by tab, Events / Sessions / Programs by always-present chips, the rest
 * through the gear -- and the stop slot is there whether or not the viewer may operate.
 */
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
vi.mock("uplot", () => ({
  default: class {
    static paths = { stepped: () => () => ({}), spline: () => () => ({}) };
    setData() {}
    setSize() {}
    setScale() {}
    redraw() {}
    destroy() {}
  },
}));
import { RigProvider } from "@flyball/react";
import type { AuthInfo, Request, Response, Transport } from "@flyball/client";
import { AuthProvider } from "../src/auth.js";
import { App } from "../src/App.js";

const who = (verbs: string[]): AuthInfo => ({
  v: 2,
  shape: "local",
  scheme: "local",
  user: { id: "local:console", name: "local", kind: "human" },
  verbs,
  anonymous: "none",
  login: { password: false, token: false, passkey: false, sso: null },
});

const DASHBOARD = { id: 1, name: "wall", rig: "t", created_ns: 1, sha256: "", body: { schema_version: 3, name: "wall", rig: "t", grid: { cols: 24, row_height: 24 }, widgets: [] } };

function transport(info: AuthInfo): Transport {
  return {
    base: "",
    async request(request: Request): Promise<Response> {
      if (request.method === "GET" && request.path === "/api/auth") return { status: 200, json: info };
      if (request.method === "GET" && request.path === "/api/devices") return { status: 200, json: [] };
      if (request.method === "GET" && request.path.startsWith("/api/dashboards") && !request.path.includes("/wall")) return { status: 200, json: [DASHBOARD] };
      return { status: 404, json: { detail: "not in this stub" } };
    },
    stream: () => ({ close: () => undefined }),
  };
}

function open(hash: string, verbs = ["operate", "read"]) {
  window.location.hash = hash;
  const t = transport(who(verbs));
  render(createElement(AuthProvider, { transport: t }, createElement(RigProvider, { transport: t }, createElement(App, { onSignIn: () => undefined }))));
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

describe("the app bar is the navigation", () => {
  it("has no sidebar", async () => {
    open("#/events");
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Events"));
    expect(document.querySelector(".MuiDrawer-root")).toBeNull();
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("the chips are there when nothing is happening, each a link to its page", async () => {
    open("#/events");
    const href = async (id: string) => (await screen.findByTestId(id)).getAttribute("href");
    expect(await href("conditions-chip")).toBe("#/events");
    expect(await href("recording-chip")).toBe("#/sessions");
    expect(await href("program-chip")).toBe("#/programs");
    expect(screen.getByTestId("program-chip").textContent).toMatch(/no program|idle/);
  });

  it("the gear opens Options, whose Pages tab links every page the bar does not", async () => {
    open("#/options/pages");
    expect((await screen.findByTestId("options-gear")).getAttribute("href")).toBe("#/options");
    const pages = await screen.findByTestId("options-pages");
    for (const id of ["inputs", "devices", "controllers", "graph", "overview", "programs", "events", "sessions"])
      expect(within(pages).getByTestId(`options-page-${id}`).getAttribute("href")).toBe(`#/${id}`);
    expect(within(pages).queryByTestId("options-page-simulation")).toBeNull();
  });

  it("the dashboards page shows a tab per saved dashboard after the generated one, and [+]", async () => {
    open("#/dashboards?generated");
    const tabs = await screen.findByTestId("dashboard-tabs");
    await waitFor(() => expect(within(tabs).getByTestId("dashboard-tab-wall").getAttribute("href")).toBe("#/dashboards/wall"));
    expect(within(tabs).getByTestId("dashboard-tab-generated").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("dashboard-add")).toBeTruthy();
  });
});

describe("the stop slot", () => {
  it("holds the stop button for an operator", async () => {
    open("#/events");
    await waitFor(() => expect(within(screen.getByTestId("stop-slot")).getByTestId("stop-button")).toBeTruthy());
  });

  it("is still there, empty, for a viewer who may only read", async () => {
    open("#/events", ["read"]);
    const slot = await screen.findByTestId("stop-slot");
    expect(within(slot).queryByTestId("stop-button")).toBeNull();
  });
});
