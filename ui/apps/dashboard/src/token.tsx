/**
 * The daemon's bearer token (`--token`/`FLYBALL_TOKEN`): kept in this browser's `localStorage` (try/catch,
 * so a private window or disabled storage just does not persist it), read once from the page's own
 * `?token=` on load. Lives above `<RigProvider>` in `main.tsx` -- changing it rebuilds the client and every
 * socket, so entering a token after a 401 reconnects everything at once.
 */
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

const STORAGE_KEY = "flyball.token";

function readStored(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

function writeStored(token: string | null): void {
  try {
    if (token) window.localStorage.setItem(STORAGE_KEY, token);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* private mode, quota, disabled storage: the choice just does not persist */
  }
}

/**
 * `?token=` on the page's own URL, taken once and dropped from the visible address so it is not left in
 * history or in a link someone copies -- how a shared "open this with the token" link hands it over.
 */
function tokenFromLocation(): string | null {
  const url = new URL(window.location.href);
  const fromQuery = url.searchParams.get("token");
  if (!fromQuery) return null;
  url.searchParams.delete("token");
  window.history.replaceState(window.history.state, "", url.toString());
  return fromQuery;
}

export interface TokenState {
  /** The token in force, or `""` for an open daemon (none needed, or none entered yet). */
  token: string;
  setToken(token: string): void;
}

const TokenContext = createContext<TokenState | null>(null);

export function useToken(): TokenState {
  const ctx = useContext(TokenContext);
  if (!ctx) throw new Error("useToken: no <TokenProvider> above this component");
  return ctx;
}

export function TokenProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string>(() => {
    const fromUrl = tokenFromLocation();
    if (fromUrl) writeStored(fromUrl);
    return fromUrl ?? readStored() ?? "";
  });

  const value = useMemo<TokenState>(
    () => ({
      token,
      setToken(next: string) {
        setTokenState(next);
        writeStored(next || null);
      },
    }),
    [token],
  );

  return <TokenContext.Provider value={value}>{children}</TokenContext.Provider>;
}
