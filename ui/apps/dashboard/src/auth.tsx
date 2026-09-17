/**
 * The door, as the app sees it: who this browser is to the runner (`GET /api/auth`), sign in, sign out.
 * The browser keeps no secret -- a login sets an `HttpOnly` cookie the runner issues, which then rides on
 * every request, socket and download by itself. Lives above `<RigProvider>` in `main.tsx`: signing in or out
 * bumps `epoch`, which rebuilds the client and every socket (a socket the runner closed with 4401 is never
 * retried on its own).
 *
 * `?token=` on the page's own URL is the old way of handing a runner's token to a browser: it is posted to
 * the login once and dropped from the visible address, so a shared "open this with the token" link still
 * works and leaves nothing in history.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { RigClient, RigError, browserTransport, type AuthInfo, type PasskeyListOut, type PasskeyOut, type Transport } from "@flyball/client";

export interface AuthState {
  /** What the runner said, or `null` before the first answer arrived. */
  info: AuthInfo | null;
  /** Why the first answer did not arrive: the runner is unreachable, not refusing. */
  error: string | null;
  /** Rebuilds `<RigProvider>` when it changes: after a sign in or out. */
  epoch: number;
  /** The runner has neither a password nor a token: nobody is refused, no door is drawn. */
  open: boolean;
  /** This browser may drive the rig. */
  canOperate: boolean;
  /** This browser must sign in before it sees anything. */
  mustSignIn: boolean;
  /** This browser signed in (a session cookie), so there is something to sign out of. */
  signedIn: boolean;
  login(secret: string): Promise<void>;
  loginWithPasskey(): Promise<void>;
  logout(): Promise<void>;
  /** How many times a request came back 401 while this browser was an anonymous reader: an attempt to operate. */
  denied: number;
  /** A request came back 401: the session ended, or an anonymous reader tried to operate. */
  unauthorized(): void;
  /** Read `GET /api/auth` again. */
  refresh(): Promise<void>;
  /** This runner's registered passkeys, and whether they survive a restart. */
  listPasskeys(): Promise<PasskeyListOut>;
  /** Register a new passkey for this browser's authenticator, labelled for the operator's own use. */
  registerPasskey(label: string): Promise<PasskeyOut>;
  /** Forget a passkey; anyone still using it is refused from their next request. */
  deletePasskey(id: number): Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth: no <AuthProvider> above this component");
  return ctx;
}

function tokenFromLocation(): string | null {
  const url = new URL(window.location.href);
  const fromQuery = url.searchParams.get("token");
  if (!fromQuery) return null;
  url.searchParams.delete("token");
  window.history.replaceState(window.history.state, "", url.toString());
  return fromQuery;
}

const OPEN: Pick<AuthInfo, "password" | "token"> = { password: false, token: false };

export function AuthProvider({ children }: { children: ReactNode }) {
  // Its own client: the one in <RigProvider> below is rebuilt on every sign in, and this one must outlive it.
  const client = useMemo(() => new RigClient(browserTransport()), []);
  const [info, setInfo] = useState<AuthInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [epoch, setEpoch] = useState(0);
  const [denied, setDenied] = useState(0);
  const pending = useRef<Promise<void> | null>(null);
  const level = useRef<AuthInfo["level"] | null>(null);
  level.current = info?.level ?? null;

  // Counts sign-ins and -outs: an answer to "who am I" that was asked before one finished is stale (the
  // question left without the new cookie), and is dropped rather than undoing what the login just said.
  const changed = useRef(0);

  const refresh = useCallback(async () => {
    const asked = changed.current;
    try {
      const answer = await client.auth();
      if (asked !== changed.current) return;
      setInfo(answer);
      setError(null);
    } catch (e) {
      if (asked === changed.current) setError(e instanceof Error ? e.message : String(e));
    }
  }, [client]);

  const login = useCallback(
    async (secret: string) => {
      const answer = await client.login(secret); // a wrong one throws RigError(401); the page shows it
      changed.current++;
      setInfo(answer);
      setError(null);
      setEpoch((n) => n + 1);
    },
    [client],
  );

  const loginWithPasskey = useCallback(async () => {
    const answer = await client.loginWithPasskey(); // a wrong/cancelled ceremony throws; the page shows it
    changed.current++;
    setInfo(answer);
    setError(null);
    setEpoch((n) => n + 1);
  }, [client]);

  const logout = useCallback(async () => {
    const answer = await client.logout();
    changed.current++;
    setInfo(answer);
    setEpoch((n) => n + 1);
  }, [client]);

  // A 401 anywhere: ask the runner who we are now (coalesced, since a page's every poll may say so at once).
  // From a reader it is an attempt to operate, which the app nudges about; otherwise a session that ended.
  const unauthorized = useCallback(() => {
    if (level.current === "read") setDenied((n) => n + 1);
    if (pending.current) return;
    pending.current = refresh().finally(() => {
      pending.current = null;
    });
  }, [refresh]);

  useEffect(() => {
    const handed = tokenFromLocation();
    if (handed) {
      login(handed).catch((e: unknown) => {
        if (e instanceof RigError && e.status === 401) void refresh(); // a stale link: the login page instead
        else setError(e instanceof Error ? e.message : String(e));
      });
    } else void refresh();
  }, [login, refresh]);

  const value = useMemo<AuthState>(() => {
    const door = info ?? { ...OPEN, level: "operate" as const, scheme: "anonymous" as const, anonymous: "none" as const };
    return {
      info,
      error,
      epoch,
      denied,
      open: !door.password && !door.token,
      canOperate: door.level === "operate",
      mustSignIn: info !== null && door.level === "none",
      signedIn: door.scheme === "password" || door.scheme === "passkey",
      login,
      loginWithPasskey,
      logout,
      unauthorized,
      refresh,
      listPasskeys: client.listPasskeys.bind(client),
      registerPasskey: client.registerPasskey.bind(client),
      deletePasskey: client.deletePasskey.bind(client),
    };
  }, [info, error, epoch, denied, login, loginWithPasskey, logout, unauthorized, refresh, client]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** A transport that tells `onUnauthorized` about every 401 it sees, so the app can react in one place. */
export function watchUnauthorized(transport: Transport, onUnauthorized: () => void): Transport {
  return {
    ...transport,
    async request(request) {
      const response = await transport.request(request);
      if (response.status === 401) onUnauthorized();
      return response;
    },
    stream(path, handlers) {
      return transport.stream(path, {
        ...handlers,
        onClose(reason, code) {
          if (code === 4401) onUnauthorized();
          handlers.onClose?.(reason, code);
        },
      });
    },
  };
}
