// @vitest-environment jsdom
/** The Programs page's "Run a step": live for an operator, off for a reader; the dialog opens with Run off until a step is in. */
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
vi.mock("uplot", () => ({ default: class { static paths = { stepped: () => () => ({}) }; setData() {} setSize() {} destroy() {} } }));
import { RigProvider } from "@flyball/react";
import type { AuthInfo, Request, Response, Transport } from "@flyball/client";
import { AuthProvider } from "../src/auth.js";
import { App } from "../src/App.js";

const who = (verbs: string[]): AuthInfo => ({ v: 2, shape: "local", scheme: "local", user: { id: "local:console", name: "local", kind: "human" }, verbs, anonymous: "none", login: { password: false, token: false, passkey: false, sso: null } });

function open(verbs: string[]) {
  window.location.hash = "#/programs";
  const transport: Transport = {
    base: "",
    async request(r: Request): Promise<Response> {
      if (r.path === "/api/auth") return { status: 200, json: who(verbs) };
      if (r.path === "/api/devices" || r.path === "/api/programs/library" || r.path === "/api/controllers") return { status: 200, json: [] };
      if (r.path === "/api/programs/running") return { status: 200, json: { running: false, failed: false, step: 0, steps: 0, command: null, error: null } };
      return { status: 404, json: { detail: "not in this stub" } };
    },
    stream: () => ({ close: () => undefined }),
  };
  render(createElement(AuthProvider, { transport }, createElement(RigProvider, { transport }, createElement(App, { onSignIn: () => undefined }))));
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

describe("Run a step", () => {
  it("is live for an operator and opens a dialog whose Run waits for a step", async () => {
    open(["operate", "read"]);
    const button = (await screen.findByTestId("run-step")) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByTestId("run-step-dialog")).toBeTruthy());
    expect((screen.getByTestId("run-step-go") as HTMLButtonElement).disabled).toBe(true);
  });

  it("is shown but off for a viewer who may only read", async () => {
    open(["read"]);
    expect(((await screen.findByTestId("run-step")) as HTMLButtonElement).disabled).toBe(true);
  });
});
