import { createContext, useContext, useMemo, type ReactNode } from "react";
import { RigClient, browserTransport, type Transport } from "@flyball/client";
import { TelemetryStore, type TelemetryStoreOptions } from "./store/telemetry.js";

const RigContext = createContext<{ client: RigClient; store: TelemetryStore } | null>(null);

export interface RigProviderProps {
  /** Absolute origin of the daemon, or omit for same-origin. Ignored when `transport` is given. */
  url?: string;
  /** Supply your own transport (a mock, a test double, another protocol). */
  transport?: Transport;
  /** How much the telemetry store holds; an hour per channel by default. */
  store?: TelemetryStoreOptions;
  children: ReactNode;
}

/**
 * Makes one `RigClient` and one `TelemetryStore` available to every hook
 * below it. The store owns the four live sockets (samples, loops, actuators,
 * events): they open on the first subscriber and close a few seconds after
 * the last leaves, whichever page that was on.
 */
export function RigProvider({ url, transport, store: storeOptions, children }: RigProviderProps) {
  const value = useMemo(() => {
    const client = new RigClient(transport ?? browserTransport(url ?? window.location.origin));
    return { client, store: new TelemetryStore(client, storeOptions) };
    // The store's options are read once; a new object each render must not rebuild it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, transport]);
  // No dispose on unmount: the sockets close on their own once the last subscriber has left
  // (a Strict Mode remount, which would dispose and reopen, must not be counted as leaving).
  return <RigContext.Provider value={value}>{children}</RigContext.Provider>;
}

export function useRig(): RigClient {
  const held = useContext(RigContext);
  if (!held) throw new Error("useRig: no <RigProvider> above this component");
  return held.client;
}

/** The provider's telemetry store: rings of samples, loop ticks, actuator states and events. */
export function useTelemetry(): TelemetryStore {
  const held = useContext(RigContext);
  if (!held) throw new Error("useTelemetry: no <RigProvider> above this component");
  return held.store;
}
