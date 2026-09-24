// @vitest-environment jsdom
/**
 * Every page can be reached by clicking from every other, both ways. The earlier tests checked
 * that the bar links *out* to each page, never that each page links *back*: after the sidebar went,
 * nothing led back to the dashboards from any other page, and no test noticed. This walks the
 * links the app itself renders -- the app bar on every page, plus Options › Pages -- as a graph.
 */
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
import { PAGES, type Page } from "../src/router.js";

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

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

/** The page a link leads to: `#/dashboards/wall?x` → `dashboards`. */
const pageOf = (href: string): string => href.replace(/^#\/?/, "").split(/[/?]/)[0] ?? "";

/** Every in-app link on `hash` once it has rendered, in the app bar (and, on Options › Pages, the page too). */
async function linksOn(hash: string, title: string, inPage = false): Promise<Set<string>> {
  window.location.hash = hash;
  render(createElement(AuthProvider, { transport }, createElement(RigProvider, { transport }, createElement(App, { onSignIn: () => undefined }))));
  await waitFor(() => expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(title));
  await waitFor(() => expect(screen.queryByText("loading…")).toBeNull());
  await waitFor(() => expect(document.querySelector("header [data-testid=home-button]")).not.toBeNull());
  const scope = inPage ? document : document.querySelector("header")!;
  const hrefs = [...scope.querySelectorAll("a[href^='#/']")].map((a) => pageOf(a.getAttribute("href")!));
  cleanup();
  return new Set(hrefs);
}

// Detail-only (a device needs a name); simulation only on a simulated rig, which this stub is not.
const LISTED = PAGES.filter((p) => p.id !== "devices" && p.id !== "simulation");

describe("every page is reachable by clicking, both ways", () => {
  it("from every page, the app bar leads back to the dashboards and to Options", async () => {
    for (const { id, label } of LISTED) {
      const links = await linksOn(`#/${id}`, label);
      expect([id, links.has("dashboards")]).toEqual([id, true]);
      expect([id, links.has("options")]).toEqual([id, true]);
    }
  });

  it("from the dashboards, every page is reachable through the bar and Options › Pages", async () => {
    const edges = new Map<string, Set<string>>();
    for (const { id, label } of LISTED) edges.set(id, await linksOn(`#/${id}`, label));
    // Options' Pages tab is a tab, not a link: its cards count as Options' own links.
    const pages = await linksOn("#/options/pages", "Options", true);
    edges.set("options", new Set([...edges.get("options")!, ...pages]));
    const seen = new Set<string>(["dashboards"]);
    const queue = ["dashboards"];
    while (queue.length) for (const next of edges.get(queue.shift()!) ?? []) if (!seen.has(next) && edges.has(next)) (seen.add(next), queue.push(next));
    expect([...seen].sort()).toEqual(LISTED.map((p) => p.id as Page).sort());
  });
});
