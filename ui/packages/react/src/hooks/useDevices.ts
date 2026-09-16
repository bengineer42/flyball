import { useCallback, useState, useSyncExternalStore } from "react";
import type { DeviceKind, DeviceState, DeviceView, Health, RigSchema, RigError } from "@flyball/client";
import { useRig, useTelemetry } from "../provider.js";
import { useQuery, type QueryState } from "./useQuery.js";
import type { StreamStatus } from "./useStream.js";
import { READOUT_MS, useStoreStatus } from "../store/hooks.js";

/** `GET /api/schema` once; the document every panel is a function of. */
export function useRigSchema(): QueryState<RigSchema> {
  const rig = useRig();
  return useQuery((signal) => rig.schema(signal), [rig]);
}

/** `GET /api/health`, polled: ok, recording, reader liveness, conditions, signals. */
export function useHealth(refreshMs = 5000): QueryState<Health> {
  const rig = useRig();
  return useQuery(() => rig.health(), [rig], { refreshMs });
}

/** One device's view (config, settings, state) by HTTP, refreshed on demand. */
export function useDeviceView(kind: DeviceKind, name: string): QueryState<DeviceView> {
  const rig = useRig();
  return useQuery(() => rig.device(kind as "actuators", name), [rig, kind, name]);
}

/**
 * Latest state of every actuator, from `/ws/actuators` through the telemetry
 * store; the initial message carries all of them. The object keeps its
 * identity until a state changes, and changes reach the caller at most four
 * times a second. One actuator: `useActuatorState(name)`.
 */
export function useActuatorStates(): { states: Record<string, DeviceState>; status: StreamStatus } {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => store.subscribeActuators(null, cb, READOUT_MS), [store]);
  useSyncExternalStore(subscribe, () => store.actuatorVersion());
  const status = useStoreStatus("actuators");
  return { states: store.actuators(), status };
}

export interface CommandRunner {
  run(command: string, args?: Record<string, unknown>): Promise<unknown>;
  /** The last result or error, per command tag, for feedback next to the form. */
  results: Record<string, { result?: unknown; error?: RigError | Error; at: number }>;
  busy: string | null;
}

/** Runs a device's commands and remembers what came back. */
export function useCommands(kind: DeviceKind, name: string): CommandRunner {
  const rig = useRig();
  const [results, setResults] = useState<CommandRunner["results"]>({});
  const [busy, setBusy] = useState<string | null>(null);

  const run = useCallback(
    async (command: string, args: Record<string, unknown> = {}) => {
      setBusy(command);
      try {
        const result = await rig.runCommand(kind, name, command, args);
        setResults((r) => ({ ...r, [command]: { result, at: Date.now() } }));
        return result;
      } catch (error) {
        setResults((r) => ({ ...r, [command]: { error: error as Error, at: Date.now() } }));
        throw error;
      } finally {
        setBusy(null);
      }
    },
    [rig, kind, name],
  );

  return { run, results, busy };
}
