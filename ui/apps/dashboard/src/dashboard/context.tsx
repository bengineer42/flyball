/**
 * What the widgets read, split by how often it changes, so a tile that shows
 * health does not re-render on every sample. The page fills these once from
 * the app's hooks (one websocket per stream, fanned out here); widgets never
 * fetch or subscribe on their own, except through the telemetry store's
 * per-signal hooks (`useSignal`, `useWriteState`, `useController`).
 */
import { createContext, useContext } from "react";
import { describeUnit, deviceOf, publishes, signalTitle, signalsOf, type Address, type ControllerOut, type DeviceOut, type RigEvent, type SignalOut } from "@flyball/client";
import type { ControllerTraces, useHealth, useRecording } from "@flyball/react";
import type { ChartSettings } from "../YScaleSelect.js";
import type { Programmer, useRecordingExports } from "../model.js";

/** What a widget may bind to, with display names: a signal by address, a controller by name, a device by name. */
export interface Bindings {
  devices: DeviceOut[];
  /** Every publishing signal, in tree order: what a readout, gauge or chart may show. */
  signals: SignalOut[];
  controllers: ControllerOut[];
  deviceLabel(name: string): string;
  /** `Tube furnace · Zone 1 (entry) (°C)`, or the address itself when the rig lacks it. */
  signalLabel(address: Address): string;
  signalAt(address: Address): SignalOut | undefined;
}

export interface RigData {
  bindings: Bindings;
  /** The rig's name, for the document's `rig`. */
  rigName: string;
  health: ReturnType<typeof useHealth>;
  recording: ReturnType<typeof useRecording>;
  programmer: Programmer;
  exports: ReturnType<typeof useRecordingExports>;
  charts: ChartSettings;
  /** Pixels per grid row, from the document; with `GRID_MARGIN` it gives a tile's height from its `h`. */
  rowHeight: number;
}

export const RigDataContext = createContext<RigData | null>(null);
export const ControllersContext = createContext<{ controllers: Record<Address, ControllerOut>; history: ControllerTraces }>({ controllers: {}, history: {} });
export const EventsContext = createContext<RigEvent[]>([]);

export function useRigData(): RigData {
  const data = useContext(RigDataContext);
  if (!data) throw new Error("widget rendered outside a dashboard");
  return data;
}
export const useBindings = () => useRigData().bindings;
export const useControllersData = () => useContext(ControllersContext);
export const useEventsData = () => useContext(EventsContext);

/** Bindings from the rig documents: labels fall back to names. */
export function makeBindings(devices: DeviceOut[], controllers: Record<Address, ControllerOut>): Bindings {
  const signals = devices.flatMap((d) => signalsOf(d.signals)).filter(publishes);
  const byAddress = new Map(signals.map((s) => [s.address, s]));
  const deviceLabel = (name: string) => devices.find((d) => d.name === name)?.label ?? name;
  return {
    devices,
    signals,
    controllers: Object.values(controllers),
    deviceLabel,
    signalLabel(address) {
      const s = byAddress.get(address);
      return s ? `${deviceLabel(deviceOf(address))} · ${signalTitle(s, devices)}${describeUnit(s.unit) ? ` (${describeUnit(s.unit)})` : ""}` : address;
    },
    signalAt: (address) => byAddress.get(address),
  };
}
