/**
 * The rig, as React sees it.
 *
 * One hook owns the transport, the live state, the chart buffer and the
 * command surface, so no component builds a transport of its own and there is
 * exactly one socket per session. Components receive `RigHandle` and call it;
 * they never touch fetch or the WebSocket.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { LiveRig } from "../api/client";
import { MockRig } from "../api/mock";
import {
  NO_CAPABILITIES,
  type Capabilities,
  type LinkStatus,
  type RigTransport,
} from "../api/transport";
import type { RigState, Warning } from "../api/types";
import { append, sampleOf, type Sample } from "./history";
import type { Settings } from "./settings";

const WARNING_LIMIT = 50;

export interface CommandState {
  /** The command in flight, for disabling buttons and showing a spinner. */
  pending: string | null;
  /** The last failure, kept until dismissed or superseded. */
  error: string | null;
  /** The last success, for a short-lived confirmation. */
  done: string | null;
}

export interface RigHandle {
  transport: RigTransport;
  status: LinkStatus;
  state: RigState | null;
  capabilities: Capabilities;
  history: Sample[];
  warnings: Warning[];
  command: CommandState;
  /** Run a command, capturing failure as prose rather than throwing at React. */
  run(label: string, action: (rig: RigTransport) => Promise<unknown>): Promise<boolean>;
  clearError(): void;
  clearWarnings(): void;
  clearHistory(): void;
}

export function useRig(settings: Settings): RigHandle {
  const [status, setStatus] = useState<LinkStatus>("connecting");
  const [state, setState] = useState<RigState | null>(null);
  const [history, setHistory] = useState<Sample[]>([]);
  const [warnings, setWarnings] = useState<Warning[]>([]);
  const [capabilities, setCapabilities] = useState<Capabilities>(NO_CAPABILITIES);
  const [command, setCommand] = useState<CommandState>({ pending: null, error: null, done: null });

  // The mock is stateful, so it must survive re-renders; only a change of
  // source or address may replace the transport.
  const mock = useRef<MockRig | null>(null);
  const transport = useMemo<RigTransport>(() => {
    if (settings.source === "mock") {
      if (!mock.current) mock.current = new MockRig(settings.mock);
      return mock.current;
    }
    return new LiveRig(settings.apiBase);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.source, settings.apiBase]);

  // Mock options are live-editable without tearing down the simulation.
  useEffect(() => {
    if (settings.source === "mock") mock.current?.configure(settings.mock);
  }, [settings.source, settings.mock]);

  const capacity = settings.historyPoints;
  const capacityRef = useRef(capacity);
  capacityRef.current = capacity;

  useEffect(() => {
    setState(null);
    setHistory([]);
    setStatus("connecting");

    let live = true;
    void transport.capabilities().then((next) => {
      if (live) setCapabilities(next);
    });

    const subscription = transport.subscribe({
      onState: (next) => {
        setState(next);
        setHistory((buffer) => append(buffer, sampleOf(next), capacityRef.current));
      },
      onWarning: (warning) =>
        setWarnings((current) => [warning, ...current].slice(0, WARNING_LIMIT)),
      onStatus: setStatus,
    });

    return () => {
      live = false;
      subscription.close();
    };
  }, [transport]);

  const run = useCallback(
    async (label: string, action: (rig: RigTransport) => Promise<unknown>): Promise<boolean> => {
      setCommand({ pending: label, error: null, done: null });
      try {
        await action(transport);
        setCommand({ pending: null, error: null, done: label });
        return true;
      } catch (error) {
        setCommand({ pending: null, error: (error as Error).message, done: null });
        return false;
      }
    },
    [transport],
  );

  return {
    transport,
    status,
    state,
    capabilities,
    history,
    warnings,
    command,
    run,
    clearError: useCallback(() => setCommand((c) => ({ ...c, error: null, done: null })), []),
    clearWarnings: useCallback(() => setWarnings([]), []),
    clearHistory: useCallback(() => setHistory([]), []),
  };
}
