import { createContext, useContext, useMemo, type ReactNode } from "react";
import { RigClient, browserTransport, pageBase, type Transport } from "@flyball/client";
import { TelemetryStore, type TelemetryStoreOptions } from "./store/telemetry.js";

const RigContext = createContext<{ client: RigClient; store: TelemetryStore } | null>(null);

export interface RigProviderProps {
  /** Absolute origin of the daemon, or omit for same-origin. Ignored when `transport` is given. */
  url?: string;
  /** The daemon's bearer token, when it was started with `--token`: a header on every request, `?token=` on
   * every socket. Ignored when `transport` is given -- bring your own auth on a custom transport. Changing it
   * rebuilds the client and its store, so a token entered after a 401 reconnects everything at once. */
  token?: string;
  /** Supply your own transport (a mock, a test double, another protocol). */
  transport?: Transport;
  /** How much the telemetry store holds; an hour per signal by default. */
  store?: TelemetryStoreOptions;
  children: ReactNode;
}

/**
 * Makes one `RigClient` and one `TelemetryStore` available to every hook
 * below it. The store owns the live sockets (samples, writes, controllers,
 * devices, waits, events): they open on the first subscriber and close a
 * few seconds after the last leaves, whichever page that was on.
 */
export function RigProvider({ url, token, transport, store: storeOptions, children }: RigProviderProps) {
  const value = useMemo(() => {
    const client = new RigClient(transport ?? browserTransport(url ?? pageBase(), token));
    return { client, store: new TelemetryStore(client, storeOptions) };
    // The store's options are read once; a new object each render must not rebuild it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, token, transport]);
  // No dispose on unmount: the sockets close on their own once the last subscriber has left
  // (a Strict Mode remount, which would dispose and reopen, must not be counted as leaving).
  return <RigContext.Provider value={value}>{children}</RigContext.Provider>;
}

export function useRig(): RigClient {
  const held = useContext(RigContext);
  if (!held) throw new Error("useRig: no <RigProvider> above this component");
  return held.client;
}

/** The provider's telemetry store: rings of samples and controller ticks, write states, device runs, waits and events. */
export function useTelemetry(): TelemetryStore {
  const held = useContext(RigContext);
  if (!held) throw new Error("useTelemetry: no <RigProvider> above this component");
  return held.store;
}
