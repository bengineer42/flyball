// @vitest-environment jsdom
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import type { AuthInfo, Request, Response, Transport } from "@flyball/client";
import { AuthProvider, useAuth, watchUnauthorized } from "../src/auth.js";

afterEach(cleanup);

/** A transport whose `/api/auth*` answers come from a table, recording every request made. */
function fakeTransport(routes: Record<string, unknown | ((request: Request) => unknown)>, status = 200): { transport: Transport; asked: Request[] } {
  const asked: Request[] = [];
  const transport: Transport = {
    base: "",
    async request(request): Promise<Response> {
      asked.push(request);
      const key = `${request.method} ${request.path}`;
      if (!(key in routes)) return { status: 404, json: { detail: `${key} not found` } };
      const entry = routes[key];
      const json = typeof entry === "function" ? (entry as (r: Request) => unknown)(request) : entry;
      return { status, json };
    },
    stream: () => ({ close: () => undefined }),
  };
  return { transport, asked };
}

const OPEN: AuthInfo = {
  v: 2,
  shape: "local",
  scheme: "local",
  user: { id: "local:console", name: "local", kind: "human" },
  verbs: ["operate", "read"],
  anonymous: "none",
  login: { password: false, token: false, passkey: false, sso: null },
};

const READ_ONLY_SESSION: AuthInfo = {
  v: 2,
  shape: "password",
  scheme: "session",
  user: { id: "local:viewer", name: "viewer", kind: "human" },
  verbs: ["read"],
  anonymous: "read",
  login: { password: true, token: false, passkey: false, sso: null },
};

const OPERATOR_SESSION: AuthInfo = {
  v: 2,
  shape: "password",
  scheme: "session",
  user: { id: "local:admin", name: "admin", kind: "human" },
  verbs: ["operate", "read"],
  anonymous: "none",
  login: { password: true, token: false, passkey: false, sso: null },
};

const PROXY_OPERATOR: AuthInfo = {
  v: 2,
  shape: "proxy",
  scheme: "proxy",
  user: { id: "proxy:example.com#alice", name: "alice", kind: "human" },
  verbs: ["operate", "read"],
  anonymous: "none",
  login: { password: false, token: false, passkey: false, sso: null },
};

const BARE_ANONYMOUS: AuthInfo = {
  v: 2,
  shape: "bare",
  scheme: "anonymous",
  user: null,
  verbs: [],
  anonymous: "none",
  login: { password: false, token: true, passkey: false, sso: null },
};

function Probe() {
  const { info, canOperate, signedIn, mustSignIn, open, versionMismatch, login, error } = useAuth();
  return (
    <div>
      <span data-testid="canOperate">{String(canOperate)}</span>
      <span data-testid="signedIn">{String(signedIn)}</span>
      <span data-testid="mustSignIn">{String(mustSignIn)}</span>
      <span data-testid="open">{String(open)}</span>
      <span data-testid="versionMismatch">{String(versionMismatch)}</span>
      <span data-testid="scheme">{info?.scheme ?? "none"}</span>
      <span data-testid="error">{error ?? ""}</span>
      <button data-testid="do-login" onClick={() => void login("secret")}>
        login
      </button>
    </div>
  );
}

async function renderWith(info: AuthInfo) {
  const { transport, asked } = fakeTransport({ "GET /api/auth": info });
  render(createElement(AuthProvider, { transport }, createElement(Probe)));
  await waitFor(() => expect(screen.getByTestId("scheme").textContent).toBe(info.scheme));
  return { transport, asked };
}

describe("canOperate reads off verbs, nothing else", () => {
  it("true when verbs includes operate", async () => {
    await renderWith(OPERATOR_SESSION);
    expect(screen.getByTestId("canOperate").textContent).toBe("true");
  });

  it("false for a read-only session, even though it is signed in", async () => {
    await renderWith(READ_ONLY_SESSION);
    expect(screen.getByTestId("canOperate").textContent).toBe("false");
  });

  it("false for an anonymous bare caller with no verbs at all", async () => {
    await renderWith(BARE_ANONYMOUS);
    expect(screen.getByTestId("canOperate").textContent).toBe("false");
  });
});

describe("signedIn: a user, and not the open local shape", () => {
  it("true for a password-shape session", async () => {
    await renderWith(OPERATOR_SESSION);
    expect(screen.getByTestId("signedIn").textContent).toBe("true");
  });

  it("true for a proxy identity", async () => {
    await renderWith(PROXY_OPERATOR);
    expect(screen.getByTestId("signedIn").textContent).toBe("true");
  });

  it("false for the open local shape, even though it always carries a user", async () => {
    await renderWith(OPEN);
    expect(screen.getByTestId("signedIn").textContent).toBe("false");
    expect(screen.getByTestId("open").textContent).toBe("true");
  });

  it("false, with no user, for an anonymous bare caller", async () => {
    await renderWith(BARE_ANONYMOUS);
    expect(screen.getByTestId("signedIn").textContent).toBe("false");
    expect(screen.getByTestId("mustSignIn").textContent).toBe("true");
  });
});

describe("login posts the credential this door's info.login offers", () => {
  it("{password} for a password-shape front not yet signed in", async () => {
    const notSignedIn: AuthInfo = { ...READ_ONLY_SESSION, scheme: "anonymous", user: null };
    const { transport, asked } = await renderWith(notSignedIn);
    // Extend the route table for the login POST the button is about to make.
    transport.request = async (request: Request) => {
      asked.push(request);
      if (request.method === "POST" && request.path === "/api/auth/login") return { status: 200, json: OPERATOR_SESSION };
      return { status: 404, json: undefined };
    };
    await act(async () => {
      screen.getByTestId("do-login").click();
    });
    await waitFor(() => expect(screen.getByTestId("canOperate").textContent).toBe("true"));
    const login = asked.find((r) => r.path === "/api/auth/login");
    expect(login?.body).toEqual({ password: "secret" });
  });

  it("{token} for a bare runner", async () => {
    const { transport, asked } = await renderWith(BARE_ANONYMOUS);
    transport.request = async (request: Request) => {
      asked.push(request);
      if (request.method === "POST" && request.path === "/api/auth/login") return { status: 200, json: { ...BARE_ANONYMOUS, scheme: "session", user: { id: "local:console", name: "", kind: "human" }, verbs: ["operate", "read"] } };
      return { status: 404, json: undefined };
    };
    await act(async () => {
      screen.getByTestId("do-login").click();
    });
    await waitFor(() => expect(screen.getByTestId("canOperate").textContent).toBe("true"));
    const login = asked.find((r) => r.path === "/api/auth/login");
    expect(login?.body).toEqual({ token: "secret" });
  });
});

describe("v !== 2: a version this client does not understand", () => {
  it("versionMismatch is true, so the app shows the mismatch rather than guessing", async () => {
    // A real /api/auth answer is JSON off the wire, so nothing stops an old front sending v: 1;
    // the type only promises v: 2 for what *this* client understands.
    const v1 = { ...OPERATOR_SESSION, v: 1 } as unknown as AuthInfo;
    await renderWith(v1);
    expect(screen.getByTestId("versionMismatch").textContent).toBe("true");
  });

  it("versionMismatch is false once the front and this client agree", async () => {
    await renderWith(OPERATOR_SESSION);
    expect(screen.getByTestId("versionMismatch").textContent).toBe("false");
  });
});

describe("watchUnauthorized re-checks /api/auth on a 401 and on an abnormal socket close", () => {
  it("401 on a request calls back", async () => {
    const onUnauthorized = vi.fn();
    const inner: Transport = {
      base: "",
      async request(): Promise<Response> {
        return { status: 401, json: undefined };
      },
      stream: () => ({ close: () => undefined }),
    };
    const watched = watchUnauthorized(inner, onUnauthorized);
    await watched.request({ method: "GET", path: "/api/health" });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("a 4401 socket close calls back", () => {
    const onUnauthorized = vi.fn();
    let closeHandler: ((reason: "closed" | "error", code?: number) => void) | undefined;
    const inner: Transport = {
      base: "",
      async request(): Promise<Response> {
        return { status: 200, json: undefined };
      },
      stream(_path, handlers) {
        closeHandler = handlers.onClose;
        return { close: () => undefined };
      },
    };
    const watched = watchUnauthorized(inner, onUnauthorized);
    watched.stream("/ws/samples", { onMessage: () => undefined });
    closeHandler?.("error", 4401);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("a 1006 (abnormal close, no close frame -- e.g. the runner restarted) also calls back", () => {
    const onUnauthorized = vi.fn();
    let closeHandler: ((reason: "closed" | "error", code?: number) => void) | undefined;
    const inner: Transport = {
      base: "",
      async request(): Promise<Response> {
        return { status: 200, json: undefined };
      },
      stream(_path, handlers) {
        closeHandler = handlers.onClose;
        return { close: () => undefined };
      },
    };
    const watched = watchUnauthorized(inner, onUnauthorized);
    watched.stream("/ws/samples", { onMessage: () => undefined });
    closeHandler?.("error", 1006);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("an ordinary close (no code, or a clean 1000) does not call back", () => {
    const onUnauthorized = vi.fn();
    let closeHandler: ((reason: "closed" | "error", code?: number) => void) | undefined;
    const inner: Transport = {
      base: "",
      async request(): Promise<Response> {
        return { status: 200, json: undefined };
      },
      stream(_path, handlers) {
        closeHandler = handlers.onClose;
        return { close: () => undefined };
      },
    };
    const watched = watchUnauthorized(inner, onUnauthorized);
    watched.stream("/ws/samples", { onMessage: () => undefined });
    closeHandler?.("closed", 1000);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});
