/**
 * The door, as the app sees it: who this browser is to the runner (`GET /api/auth`, AuthInfo v2),
 * sign in, sign out. The browser keeps no secret -- a login sets an `HttpOnly` cookie the runner
 * (or `flyball run`'s front) issues, which then rides on every request, socket and download by
 * itself. Lives above `<RigProvider>` in `main.tsx`: signing in or out bumps `epoch`, which
 * rebuilds the client and every socket (a socket the runner closed with 4401 is never retried on
 * its own).
 *
 * A shared credential never travels in this page's own URL any more: `?token=` is gone (a bare
 * runner trades its token for a cookie through `POST /api/auth/login {token}`, or a one-time
 * `/api/auth/link` the browser is sent to directly, which sets the cookie before this app even
 * loads). The login form takes a password or a pasted token, whichever `info.login` offers.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { RigClient, browserTransport, OPERATE, type AuthInfo, type PasskeyListOut, type PasskeyOut, type Transport } from "@flyball/client";

export interface AuthState {
  /** What the runner said, or `null` before the first answer arrived. */
  info: AuthInfo | null;
  /** Why the first answer did not arrive: the runner is unreachable, not refusing. */
  error: string | null;
  /** Rebuilds `<RigProvider>` when it changes: after a sign in or out. */
  epoch: number;
  /** This rig's door is `local`: nobody is refused, no door is drawn. */
  open: boolean;
  /** This browser holds the operator verb: it may drive the rig, and sees the stop button. */
  canOperate: boolean;
  /** This browser must sign in before it sees anything. */
  mustSignIn: boolean;
  /** This browser signed in (a session, not the open `local` shape), so there is something to sign out of. */
  signedIn: boolean;
  /** `info.v !== 2`: this client and the runner disagree on the auth wire shape. Never guess at an unknown one. */
  versionMismatch: boolean;
  login(secret: string): Promise<void>;
  loginWithPasskey(): Promise<void>;
  logout(): Promise<void>;
  /** How many times a request came back 401 while this browser held no operator verb: an attempt to operate. */
  denied: number;
  /** A request came back 401: the session ended, or a caller without OPERATE tried to operate. */
  unauthorized(): void;
  /** Read `GET /api/auth` again. */
  refresh(): Promise<void>;
  // The passkey members are for Phase 3 (passkeys in the Go front); nothing calls them while
  // `info.login.passkey` is false, as it always is in Phase 1.
  /** This runner's registered passkeys, and whether they survive a restart. */
  listPasskeys(): Promise<PasskeyListOut>;
  /** Register a new passkey for this browser's authenticator, labelled for the operator's own use. */
  registerPasskey(label: string): Promise<PasskeyOut>;
  /** Forget a passkey; anyone still using it is refused from their next request. */
  deletePasskey(id: number): Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

/** A caller's identity, as far as a stream's authorisation is concerned: who, and with what verbs. Two
 * answers with the same key see the same rig the same way; a different key means the streams held by
 * the current `TelemetryStore` may no longer be this caller's to hold. */
function identityKey(info: AuthInfo | null): string {
  if (!info) return "";
  return `${info.scheme}:${info.user?.id ?? ""}:${info.verbs.join(",")}`;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth: no <AuthProvider> above this component");
  return ctx;
}

/** What `AuthProvider` assumes before the first `/api/auth` answer arrives: an open, `local`-shape door. */
const OPEN_DOOR: AuthInfo = {
  v: 2,
  shape: "local",
  scheme: "local",
  user: { id: "local:console", name: "local", kind: "human" },
  verbs: [OPERATE, "read"],
  anonymous: "none",
  login: { password: false, token: false, passkey: false, sso: null },
};

export function AuthProvider({ children, transport }: { children: ReactNode; transport?: Transport }) {
  // Its own client: the one in <RigProvider> below is rebuilt on every sign in, and this one must outlive it.
  // `transport` is for tests (a fake, so no real network); the app never passes one.
  const client = useMemo(() => new RigClient(transport ?? browserTransport()), [transport]);
  const [info, setInfo] = useState<AuthInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [epoch, setEpoch] = useState(0);
  const [denied, setDenied] = useState(0);
  const pending = useRef<Promise<void> | null>(null);
  const verbs = useRef<string[]>([]);
  verbs.current = info?.verbs ?? [];

  // Counts sign-ins and -outs: an answer to "who am I" that was asked before one finished is stale (the
  // question left without the new cookie), and is dropped rather than undoing what the login just said.
  const changed = useRef(0);

  // Who `refresh` last saw, so it can tell an out-of-band identity change (an outside revocation, a
  // different cookie) from an answer that just repeats the same door. `login`/`loginWithPasskey`/`logout`
  // already bump `epoch` themselves and keep this in step; `refresh` is the only path that would
  // otherwise leave a stale `TelemetryStore` (and its dead sockets) behind a door nobody signed out of.
  const identity = useRef(identityKey(null));

  const refresh = useCallback(async () => {
    const asked = changed.current;
    try {
      const answer = await client.auth();
      if (asked !== changed.current) return;
      const next = identityKey(answer);
      if (next !== identity.current) {
        identity.current = next;
        setEpoch((n) => n + 1);
      }
      setInfo(answer);
      setError(null);
    } catch (e) {
      if (asked === changed.current) setError(e instanceof Error ? e.message : String(e));
    }
  }, [client]);

  const login = useCallback(
    async (secret: string) => {
      // A `password`-shape front offers a password; a bare runner offers a pasted token. Nothing
      // else decides which body to send -- info.login says exactly what this door takes.
      const body = info?.login.token && !info?.login.password ? { token: secret } : { password: secret };
      const answer = await client.login(body); // a wrong one throws RigError(401); the page shows it
      changed.current++;
      identity.current = identityKey(answer);
      setInfo(answer);
      setError(null);
      setEpoch((n) => n + 1);
    },
    [client, info],
  );

  const loginWithPasskey = useCallback(async () => {
    const answer = await client.loginWithPasskey(); // a wrong/cancelled ceremony throws; the page shows it
    changed.current++;
    identity.current = identityKey(answer);
    setInfo(answer);
    setError(null);
    setEpoch((n) => n + 1);
  }, [client]);

  const logout = useCallback(async () => {
    const answer = await client.logout();
    changed.current++;
    identity.current = identityKey(answer);
    setInfo(answer);
    setEpoch((n) => n + 1);
  }, [client]);

  // A 401 anywhere: ask the runner who we are now (coalesced, since a page's every poll may say so at once).
  // From a caller without OPERATE it is an attempt to operate, which the app nudges about; otherwise a
  // session that ended, or (via watchUnauthorized below) a socket that dropped abnormally.
  const unauthorized = useCallback(() => {
    if (!verbs.current.includes(OPERATE)) setDenied((n) => n + 1);
    if (pending.current) return;
    pending.current = refresh().finally(() => {
      pending.current = null;
    });
  }, [refresh]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo<AuthState>(() => {
    const door = info ?? OPEN_DOOR;
    return {
      info,
      error,
      epoch,
      denied,
      open: door.shape === "local",
      canOperate: door.verbs.includes(OPERATE),
      mustSignIn: door.shape !== "local" && door.user === null && door.anonymous === "none",
      signedIn: door.user !== null && door.scheme !== "local",
      versionMismatch: info !== null && info.v !== 2,
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

/**
 * A transport that tells `onUnauthorized` about every 401 it sees and every socket that drops
 * abnormally (4401: refused; 1006: no close frame at all, e.g. the runner restarted or the
 * network dropped), so the app re-checks `/api/auth` in one place rather than guessing why a
 * stream went quiet.
 */
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
          if (code === 4401 || code === 1006) onUnauthorized();
          handlers.onClose?.(reason, code);
        },
      });
    },
  };
}
