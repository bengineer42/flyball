// @vitest-environment jsdom
/** A viewer (read only) sees the write controls off, and opening Programs sends no write; an operator gets them live. */
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
vi.mock("uplot", () => ({ default: class { static paths = { stepped: () => () => ({}) }; setData() {} setSize() {} destroy() {} } }));
import { RigProvider } from "@flyball/react";
import type { AuthInfo, Request, Response, Transport } from "@flyball/client";
import { AuthProvider } from "../src/auth.js";
import { App } from "../src/App.js";

const who = (verbs: string[]): AuthInfo => ({ v: 2, shape: "local", scheme: "local", user: { id: "local:console", name: "local", kind: "human" }, verbs, anonymous: "none", login: { password: false, token: false, passkey: false, sso: null } });

function open(hash: string, verbs: string[]): Request[] {
  window.location.hash = hash;
  const sent: Request[] = [];
  const transport: Transport = {
    base: "",
    async request(r: Request): Promise<Response> {
      sent.push(r);
      if (r.path === "/api/auth") return { status: 200, json: who(verbs) };
      if (r.path === "/api/devices" || r.path === "/api/programs/library" || r.path === "/api/controllers" || r.path === "/api/sessions") return { status: 200, json: [] };
      if (r.path === "/api/programs/library/import") return { status: 200, json: [] };
      if (r.path === "/api/programs/running") return { status: 200, json: { running: false, failed: false, step: 0, steps: 0, command: null, error: null } };
      return { status: 404, json: { detail: "not in this stub" } };
    },
    stream: () => ({ close: () => undefined }),
  };
  render(createElement(AuthProvider, { transport }, createElement(RigProvider, { transport }, createElement(App, { onSignIn: () => undefined }))));
  return sent;
}

const button = async (id: string) => (await screen.findByTestId(id)) as HTMLButtonElement;
const imports = (sent: Request[]) => sent.filter((r) => r.path === "/api/programs/library/import");

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

describe("viewer gating", () => {
  it("Programs: a viewer's rescan is off and opening the page imports nothing", async () => {
    const sent = open("#/programs", ["read"]);
    await waitFor(async () => expect((await button("rescan")).disabled).toBe(true));
    expect(imports(sent)).toHaveLength(0);
  });

  it("Programs: an operator's rescan is live and the page imports on arrival", async () => {
    const sent = open("#/programs", ["operate", "read"]);
    await waitFor(async () => expect((await button("rescan")).disabled).toBe(false));
    expect(imports(sent).length).toBeGreaterThan(0);
  });

  it("Rig file: a viewer cannot add a device or a controller", async () => {
    open("#/options/rig", ["read"]);
    await waitFor(async () => expect((await button("add-device")).disabled).toBe(true));
    expect((await button("add-controller")).disabled).toBe(true);
  });

  it("Rig file: an operator can", async () => {
    open("#/options/rig", ["operate", "read"]);
    await waitFor(async () => expect((await button("add-device")).disabled).toBe(false));
  });
});
