// @vitest-environment jsdom
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
// `@flyball/react`'s barrel imports `uplot`, which probes `matchMedia` at import (see StopButton.test.tsx).
vi.mock("uplot", () => ({
  default: class {
    static paths = { stepped: () => () => ({}) };
    setData() {}
    setSize() {}
    destroy() {}
  },
}));
import type { AuthInfo, Request, Response, Transport } from "@flyball/client";
import { AuthProvider } from "../src/auth.js";
import { ConnectModelCard } from "../src/pages/Rig.js";

afterEach(cleanup);

function fakeTransport(info: AuthInfo): Transport {
  return {
    base: "",
    async request(request: Request): Promise<Response> {
      if (request.method === "GET" && request.path === "/api/auth") return { status: 200, json: info };
      return { status: 404, json: undefined };
    },
    stream: () => ({ close: () => undefined }),
  };
}

const NO_LOGIN = { password: false, token: false, passkey: false, sso: null };
const info = (shape: AuthInfo["shape"], login: Partial<AuthInfo["login"]> = {}): AuthInfo => ({
  v: 2,
  shape,
  scheme: shape === "local" ? "local" : "anonymous",
  user: null,
  verbs: ["read"],
  anonymous: "read",
  login: { ...NO_LOGIN, ...login },
});

/** The card's advice line and its client config block, once `/api/auth` has answered with `answer`. */
async function card(answer: AuthInfo): Promise<{ advice: string; config: string }> {
  render(createElement(AuthProvider, { transport: fakeTransport(answer) }, createElement(ConnectModelCard)));
  const advice = await waitFor(() => screen.getByTestId("connect-advice"));
  await waitFor(() => expect(advice.getAttribute("data-shape")).toBe(answer.shape));
  return { advice: advice.textContent ?? "", config: screen.getByTestId("connect-config").textContent ?? "" };
}

describe("Connect a model follows the door's shape", () => {
  it("a proxy front: a named token, never 'open' or 'no headers'", async () => {
    const { advice, config } = await card(info("proxy"));
    expect(advice).toMatch(/flyball token create/);
    expect(advice).not.toMatch(/open|no headers|--token/);
    expect(config).toContain("Bearer <token>");
  });

  it("a password front: a named token, not the runner's --token", async () => {
    const { advice, config } = await card(info("password", { password: true }));
    expect(advice).toMatch(/flyball token create/);
    expect(advice).not.toMatch(/--token|start it with/);
    expect(config).toContain("Bearer <token>");
  });

  it("a bare runner: its own token", async () => {
    const { advice, config } = await card(info("bare", { token: true }));
    expect(advice).toMatch(/--token/);
    expect(config).toContain("Bearer <token>");
  });

  it("the local shape: no headers", async () => {
    const { advice, config } = await card(info("local"));
    expect(advice).toMatch(/no headers/);
    expect(config).not.toContain("Authorization");
  });
});
