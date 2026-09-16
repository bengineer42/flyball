import { useCallback, useState } from "react";
import type { DeviceSchema, DeviceView, Simulation } from "@flyball/client";
import { RigError } from "@flyball/client";
import { useRig } from "../provider.js";
import { useQuery } from "./useQuery.js";
import type { CommandRunner } from "./useDevices.js";

export interface SimulationHook extends CommandRunner {
  /** `/api/sim` says the rig is a simulation. False while loading, on `{simulated: false}`, and on 404. */
  attached: boolean;
  /** The `/api/sim` document, when the rig is a simulation. */
  simulation: Extract<Simulation, { simulated: true }> | undefined;
  /** Rig seconds per wall second, from the clock. */
  speed: number | undefined;
  /** `PUT /api/sim/clock`; resolves to the speed the rig confirmed and refreshes. */
  setSpeed(speed: number): Promise<number>;
  /** The application's own device at `/api/sim/device`; both undefined when it has none (404). */
  schema: DeviceSchema | undefined;
  view: DeviceView | undefined;
  hasDevice: boolean;
  loading: boolean;
  error: Error | undefined;
  /** Re-fetch `/api/sim` and the device's view. */
  refresh(): void;
}

/** Resolve to `null` on the statuses that mean "not here" rather than "broken". */
const absent =
  <T>(...statuses: number[]) =>
  (error: unknown): T | null => {
    if (error instanceof RigError && statuses.includes(error.status)) return null;
    throw error;
  };

/**
 * A simulated rig's controls: the clock's speed from `/api/sim`, and the
 * application's own device (chamber physics, sensor noise, ...) from
 * `/api/sim/device`. Polls both, so the state and the clock stay current.
 */
export function useSimulation(refreshMs = 2000): SimulationHook {
  const rig = useRig();
  const doc = useQuery<Simulation | null>(() => rig.simulation().catch(absent(404)), [rig], { refreshMs });
  // Whether there is a device to ask for: `/api/sim` says (`device`); a real rig or one still
  // loading has none. Rigs without one (most) make no request at all.
  const sim = doc.data && doc.data.simulated ? doc.data : undefined;
  const probe = doc.data === undefined ? null : sim === undefined ? false : sim.device;
  const schema = useQuery<DeviceSchema | null>(
    () => (probe ? rig.simulationDeviceSchema().catch(absent(404, 409)) : Promise.resolve(null)),
    [rig, probe],
  );
  const hasDevice = !!schema.data;
  const view = useQuery<DeviceView | null>(() => (hasDevice ? rig.simulationDevice().catch(absent(404, 409)) : Promise.resolve(null)), [rig, hasDevice], { refreshMs: hasDevice ? refreshMs : undefined });
  const [results, setResults] = useState<CommandRunner["results"]>({});
  const [busy, setBusy] = useState<string | null>(null);

  const run = useCallback(
    async (command: string, args: Record<string, unknown> = {}) => {
      setBusy(command);
      try {
        const result = await rig.runSimulationCommand(command, args);
        setResults((r) => ({ ...r, [command]: { result, at: Date.now() } }));
        view.refresh();
        return result;
      } catch (error) {
        setResults((r) => ({ ...r, [command]: { error: error as Error, at: Date.now() } }));
        throw error;
      } finally {
        setBusy(null);
      }
    },
    [rig, view.refresh],
  );

  const setSpeed = useCallback(
    async (speed: number) => {
      const { speed: confirmed } = await rig.setSimulationSpeed(speed);
      doc.refresh();
      view.refresh();
      return confirmed;
    },
    [rig, doc.refresh, view.refresh],
  );

  const refresh = useCallback(() => {
    doc.refresh();
    view.refresh();
  }, [doc.refresh, view.refresh]);

  const simulation = doc.data && doc.data.simulated ? doc.data : undefined;
  return {
    attached: simulation !== undefined,
    simulation,
    speed: simulation?.clock.speed,
    setSpeed,
    schema: schema.data ?? undefined,
    view: view.data ?? undefined,
    hasDevice,
    loading: doc.loading || (probe !== false && schema.loading),
    error: doc.error ?? schema.error ?? view.error,
    run,
    busy,
    results,
    refresh,
  };
}
