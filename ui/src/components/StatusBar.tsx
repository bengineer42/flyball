/**
 * What the rig is doing, in one line, on every screen.
 *
 * Connection state is deliberately separate from rig state: a live socket to a
 * daemon with no rig attached is not the same as a dead socket, and an
 * operator needs to tell those apart before touching anything.
 */

import type { Capabilities, LinkStatus } from "../api/transport";
import type { RigState } from "../api/types";
import { formatDuration } from "../domain/units";
import { Badge } from "./primitives";

export interface StatusBarProps {
  status: LinkStatus;
  state: RigState | null;
  capabilities: Capabilities;
  source: "live" | "mock";
  label: string;
}

export function StatusBar({ status, state, capabilities, source, label }: StatusBarProps) {
  const link =
    status === "live" ? (
      <Badge tone="good" dot>
        Live
      </Badge>
    ) : status === "connecting" ? (
      <Badge tone="warning" dot>
        Connecting
      </Badge>
    ) : (
      <Badge tone="critical" dot>
        Offline
      </Badge>
    );

  return (
    <div className="row" style={{ gap: 10 }}>
      {link}
      {source === "mock" ? (
        <Badge tone="warning">Simulated</Badge>
      ) : (
        <Badge>{label}</Badge>
      )}
      {status === "live" && !capabilities.rigAttached && source === "live" && (
        <Badge tone="critical">No rig attached</Badge>
      )}
      {state && (
        <>
          <Badge tone={state.running ? "good" : "warning"}>
            {state.running ? "Loop running" : "Loop stopped"}
          </Badge>
          {state.recording && <Badge tone="good">Recording</Badge>}
          {state.controller?.suspended && <Badge tone="warning">Controller suspended</Badge>}
          {state.program?.running && (
            <Badge tone="good">
              {state.program.name} · step {state.program.step + 1}/{state.program.step_count}
            </Badge>
          )}
          <span className="hint mono">t + {formatDuration(state.time)}</span>
        </>
      )}
    </div>
  );
}
