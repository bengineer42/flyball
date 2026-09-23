// @vitest-environment jsdom
import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { AuthInfo, Request, Response, Transport } from "@flyball/client";
import { AuthProvider } from "../src/auth.js";
import { AuthChip } from "../src/Login.js";

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
