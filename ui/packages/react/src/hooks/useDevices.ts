import { useCallback, useState } from "react";
import type { DeviceOut, DeviceSchema, Health, RigError, RigSchema } from "@flyball/client";
import { useRig } from "../provider.js";
import { useQuery, type QueryState } from "./useQuery.js";

/** `GET /api/schema` once: every device's schema, by name. */
export function useRigSchema(): QueryState<RigSchema> {
  const rig = useRig();
  return useQuery((signal) => rig.schema(signal), [rig]);
}

/** `GET /api/health`, polled: ok, recording, device liveness, conditions, alarms, activities. */
export function useHealth(refreshMs = 5000): QueryState<Health> {
  const rig = useRig();
  return useQuery(() => rig.health(), [rig], { refreshMs });
}

/**
 * `GET /api/devices`: every device with its signal tree (metadata, latest
 * values, write states), commands, state, conditions and run. Fetched once,
 * or every `refreshMs`; the tree's metadata is what a page is built from,
 * while the live values come through the store (`useSignal`, `useWriteState`).
 */
export function useDevices(refreshMs?: number): QueryState<DeviceOut[]> {
  const rig = useRig();
  return useQuery((signal) => rig.devices(signal), [rig], refreshMs ? { refreshMs } : {});
}

/** `GET /api/devices/{name}`: one device's tree, commands, state, conditions and run. */
export function useDevice(name: string, refreshMs?: number): QueryState<DeviceOut> {
  const rig = useRig();
  return useQuery((signal) => rig.device(name, signal), [rig, name], refreshMs ? { refreshMs } : {});
}

/** `GET /api/devices/{name}/schema`: config, settings, state, signals and each command's arguments as JSON Schema. */
export function useDeviceSchema(name: string): QueryState<DeviceSchema> {
  const rig = useRig();
  return useQuery((signal) => rig.deviceSchema(name, signal), [rig, name]);
}

export interface CommandRunner {
  run(command: string, args?: Record<string, unknown>): Promise<unknown>;
  /** The last result or error, per command tag, for feedback next to the form. */
  results: Record<string, { result?: unknown; error?: RigError | Error; at: number }>;
  busy: string | null;
}

/** Runs a device's commands (`POST /api/devices/{name}/commands/{tag}`) and remembers what came back. */
export function useCommands(name: string): CommandRunner {
  const rig = useRig();
  const [results, setResults] = useState<CommandRunner["results"]>({});
  const [busy, setBusy] = useState<string | null>(null);

  const run = useCallback(
    async (command: string, args: Record<string, unknown> = {}) => {
      setBusy(command);
      try {
        const result = await rig.command(name, command, args);
        setResults((r) => ({ ...r, [command]: { result, at: Date.now() } }));
        return result;
      } catch (error) {
        setResults((r) => ({ ...r, [command]: { error: error as Error, at: Date.now() } }));
        throw error;
      } finally {
        setBusy(null);
      }
    },
    [rig, name],
  );

  return { run, results, busy };
}
