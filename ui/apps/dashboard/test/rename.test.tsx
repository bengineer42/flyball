// @vitest-environment jsdom
/**
 * Renaming a dashboard edits its label (`name` is the key, `label` what a person sees): Rename
 * saves a new version under the same name with the new label, so its link, place and home stay.
 */
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
import type { AuthInfo, DashboardDocument, Request, Response, Transport } from "@flyball/client";
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

const BODY: DashboardDocument = { schema_version: 6, name: "wall", label: null, rig: "t", grid: { cols: 24, row_height: 24 }, widgets: [], readonly: false, order: null };

function stub(calls: Request[]): Transport {
  let body = BODY;
  const row = () => ({ id: 1, name: "wall", label: body.label ?? "Wall", rig: "t", created_ns: 1, sha256: "", body });
  return {
    base: "",
    async request(request: Request): Promise<Response> {
      calls.push(request);
      if (request.method === "GET" && request.path === "/api/auth") return { status: 200, json: OPERATOR };
      if (request.method === "GET" && request.path === "/api/devices") return { status: 200, json: [] };
      if (request.method === "GET" && request.path === "/api/dashboards") return { status: 200, json: [row()] };
      if (request.method === "GET" && request.path === "/api/dashboards/wall") return { status: 200, json: { ...row(), problems: [] } };
      if (request.method === "PUT" && request.path === "/api/dashboards/wall") {
        body = request.body as DashboardDocument;
        return { status: 201, json: { ...row(), problems: [] } };
      }
      return { status: 404, json: { detail: "not in this stub" } };
    },
    stream: () => ({ close: () => undefined }),
  };
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

describe("renaming a dashboard", () => {
  it("saves the new label under the same name, and the tab shows it", async () => {
    window.location.hash = "#/dashboards/wall";
    const calls: Request[] = [];
    const t = stub(calls);
    render(createElement(AuthProvider, { transport: t }, createElement(RigProvider, { transport: t }, createElement(App, { onSignIn: () => undefined }))));
    const tabs = await screen.findByTestId("dashboard-tabs");
    await waitFor(() => expect(within(tabs).getByTestId("dashboard-tab-wall").textContent).toBe("Wall"));
    fireEvent.click(await screen.findByTestId("more", undefined, { timeout: 5000 }));
    await waitFor(() => expect(screen.getByTestId("menu-rename").getAttribute("aria-disabled")).not.toBe("true"));
    fireEvent.click(screen.getByTestId("menu-rename"));
    const field = await screen.findByRole("textbox", { name: "dashboard name" });
    expect(screen.getByText("What its tab shows; its link stays “wall”.")).toBeTruthy();
    fireEvent.change(field, { target: { value: "Wall display" } });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.path).toBe("/api/dashboards/wall");
    expect((put.body as DashboardDocument).label).toBe("Wall display");
    expect(calls.some((c) => c.path.endsWith("/rename")), "the key is not moved").toBe(false);
    await waitFor(() => expect(within(tabs).getByTestId("dashboard-tab-wall").textContent).toBe("Wall display"));
    expect(window.location.hash).toBe("#/dashboards/wall");
  });
});
