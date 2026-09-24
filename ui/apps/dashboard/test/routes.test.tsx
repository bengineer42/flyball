// @vitest-environment jsdom
/**
 * Every route renders: the app bar shows the page's title, the lazy page loads, and the page
 * boundary does not trip. A net under the app frame (Shell, router, App) for the layout rewrite in
 * the UI redesign; the server is a stub that knows `/api/auth` and `/api/devices` and 404s the rest,
 * so each page meets its own fetch errors, which it must survive without throwing.
 */
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
// Importing `@flyball/react` pulls in uPlot, which probes `matchMedia` at module scope (see App.test.tsx).
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

const OPERATOR: AuthInfo = {
  v: 2,
  shape: "local",
  scheme: "local",
  user: { id: "local:console", name: "local", kind: "human" },
  verbs: ["operate", "read"],
  anonymous: "none",
  login: { password: false, token: false, passkey: false, sso: null },
};

const transport: Transport = {
  base: "",
  async request(request: Request): Promise<Response> {
    if (request.method === "GET" && request.path === "/api/auth") return { status: 200, json: OPERATOR };
    if (request.method === "GET" && request.path === "/api/devices") return { status: 200, json: [] };
    return { status: 404, json: { detail: "not in this stub" } };
  },
  stream: () => ({ close: () => undefined }),
};

let errors: unknown[][] = [];
beforeEach(() => {
  errors = [];
  vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
    errors.push(args);
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.location.hash = "";
});

/** The list route of each page, and the title its app bar shows. */
const ROUTES: Array<[hash: string, title: string]> = [
  ["#/dashboards", "Dashboards"],
  ["#/readings", "Readings"],
  ["#/graph", "Graph"],
  ["#/controllers", "Controllers"],
  ["#/programs", "Programs"],
  ["#/events", "Events"],
  ["#/sessions", "Sessions"],
  ["#/simulation", "Simulation"],
  ["#/options", "Options"],
];

describe("every route renders", () => {
  it.each(ROUTES)("%s", async (hash, title) => {
    window.location.hash = hash;
    render(createElement(AuthProvider, { transport }, createElement(RigProvider, { transport }, createElement(App, { onSignIn: () => undefined }))));
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(title));
    // The lazy page has loaded when nothing says "loading…" (the app's own wait and the page fallback both do).
    await waitFor(() => expect(screen.queryByText("loading…")).toBeNull());
    expect(screen.queryByText(/failed to render/i)).toBeNull();
    expect(errors.filter((args) => args[0] === "page failed")).toEqual([]);
  });

  it("the old Config address opens the Options page's Rig file tab", async () => {
    window.location.hash = "#/rig";
    render(createElement(AuthProvider, { transport }, createElement(RigProvider, { transport }, createElement(App, { onSignIn: () => undefined }))));
    await waitFor(() => expect(window.location.hash).toBe("#/options/rig"));
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Options"));
  });

  it.each([
    ["#/overview", "#/dashboards?generated="],
    ["#/inputs", "#/readings"],
    ["#/inputs/furnace.zone1", "#/readings/furnace.zone1"],
    ["#/devices", "#/readings"],
  ])("the old address %s lands on %s", async (from, to) => {
    window.location.hash = from;
    render(createElement(AuthProvider, { transport }, createElement(RigProvider, { transport }, createElement(App, { onSignIn: () => undefined }))));
    await waitFor(() => expect(window.location.hash).toBe(to));
  });

  it("an unknown route, or none, opens the dashboards", async () => {
    window.location.hash = "#/nowhere";
    render(createElement(AuthProvider, { transport }, createElement(RigProvider, { transport }, createElement(App, { onSignIn: () => undefined }))));
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Dashboards"));
  });
});
