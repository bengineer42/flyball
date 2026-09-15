import { createContext, useContext, useMemo, type ReactNode } from "react";
import { RigClient, browserTransport, type Transport } from "@flyball/client";

const RigContext = createContext<RigClient | null>(null);

export interface RigProviderProps {
  /** Absolute origin of the daemon, or omit for same-origin. Ignored when `transport` is given. */
  url?: string;
  /** Supply your own transport (a mock, a test double, another protocol). */
  transport?: Transport;
  children: ReactNode;
}

/** Makes one `RigClient` available to every hook below it. */
export function RigProvider({ url, transport, children }: RigProviderProps) {
  const client = useMemo(
    () => new RigClient(transport ?? browserTransport(url ?? window.location.origin)),
    [url, transport],
  );
  return <RigContext.Provider value={client}>{children}</RigContext.Provider>;
}

export function useRig(): RigClient {
  const client = useContext(RigContext);
  if (!client) throw new Error("useRig: no <RigProvider> above this component");
  return client;
}
