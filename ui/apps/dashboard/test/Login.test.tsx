// @vitest-environment jsdom
import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { AuthInfo, Request, Response, Transport } from "@flyball/client";
import { AuthProvider } from "../src/auth.js";
import { AuthChip, LoginPage } from "../src/Login.js";

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

const OPERATOR_SESSION: AuthInfo = {
  v: 2,
  shape: "password",
  scheme: "session",
  user: { id: "local:admin", name: "admin", kind: "human" },
  verbs: ["operate", "read"],
  anonymous: "none",
  login: { password: true, token: false, passkey: false, sso: null },
};

describe("AuthChip shows the v !== 2 mismatch ahead of everything else", () => {
  it("renders the mismatch chip when info.v is not 2, even on an otherwise-open answer", async () => {
    const mismatched = { ...OPERATOR_SESSION, v: 1, shape: "local" as const } as unknown as AuthInfo;
    render(createElement(AuthProvider, { transport: fakeTransport(mismatched) }, createElement(AuthChip, { onSignIn: () => undefined })));
    await waitFor(() => expect(screen.getByTestId("auth-version-mismatch")).toBeTruthy());
  });

  it("renders the ordinary signed-in chip once v is 2", async () => {
    render(createElement(AuthProvider, { transport: fakeTransport(OPERATOR_SESSION) }, createElement(AuthChip, { onSignIn: () => undefined })));
    await waitFor(() => expect(screen.getByTestId("auth-chip")).toBeTruthy());
    expect(screen.queryByTestId("auth-version-mismatch")).toBeNull();
  });
});

describe("LoginPage follows the door's shape and login fields", () => {
  const answer = (shape: AuthInfo["shape"], login: Partial<AuthInfo["login"]>): AuthInfo => ({
    ...OPERATOR_SESSION,
    shape,
    scheme: "anonymous",
    user: null,
    verbs: [],
    login: { password: false, token: false, passkey: false, sso: null, ...login },
  });
  const page = async (info: AuthInfo) => {
    render(createElement(AuthProvider, { transport: fakeTransport(info) }, createElement(LoginPage)));
    const form = await waitFor(() => screen.getByTestId("login"));
    await waitFor(() => expect(form.getAttribute("data-shape")).toBe(info.shape));
    return form;
  };

  it("a proxy front: sign in at the proxy; no token, no field to type into", async () => {
    const form = await page(answer("proxy", {}));
    expect(form.textContent).toMatch(/proxy/i);
    expect(form.textContent).not.toMatch(/--token|its token/);
    expect(screen.queryByTestId("login-secret")).toBeNull();
    expect(screen.queryByTestId("login-submit")).toBeNull();
  });

  it("a password front: the password", async () => {
    const form = await page(answer("password", { password: true }));
    expect(form.textContent).toMatch(/password/i);
    expect(screen.getByTestId("login-secret")).toBeTruthy();
  });

  it("a bare runner: the token it was started with", async () => {
    const form = await page(answer("bare", { token: true }));
    expect(form.textContent).toMatch(/--token/);
    expect(screen.getByTestId("login-secret")).toBeTruthy();
  });
});
