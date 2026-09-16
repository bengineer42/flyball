/**
 * What the widgets read, split by how often it changes, so a tile that shows
 * health does not re-render on every sample. The page fills these once from
 * the app's hooks (one websocket per stream, fanned out here); widgets never
 * fetch or subscribe on their own.
 */
import { createContext, useContext } from "react";
import type { ActuatorSchema, ChannelOut, DeviceState, LoopOut, RigEvent, RigSchema, SourceOut } from "@flyball/client";
import type { LoopTraces, Traces, useHealth, useRecording } from "@flyball/react";
import type { ChartSettings } from "../YScaleSelect.js";
import type { Programmer, useRecordingExports } from "../model.js";

/** What a widget may bind to, with display names. */
export interface Bindings {
  schema: RigSchema;
  sources: SourceOut[];
  channels: ChannelOut[];
  loops: LoopOut[];
  actuators: ActuatorSchema[];
  sourceLabel(name: string): string;
  channelLabel(key: string): string;
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
export const TracesContext = createContext<Traces>({});
export const StatesContext = createContext<Record<string, DeviceState>>({});
export const LoopsContext = createContext<{ loops: Record<string, LoopOut>; history: LoopTraces }>({ loops: {}, history: {} });
export const EventsContext = createContext<RigEvent[]>([]);

export function useRigData(): RigData {
  const data = useContext(RigDataContext);
  if (!data) throw new Error("widget rendered outside a dashboard");
  return data;
}
export const useBindings = () => useRigData().bindings;
export const useTraces = () => useContext(TracesContext);
export const useStates = () => useContext(StatesContext);
export const useLoopsData = () => useContext(LoopsContext);
export const useEventsData = () => useContext(EventsContext);

/** Bindings from the rig documents: labels fall back to names. */
export function makeBindings(schema: RigSchema, sources: SourceOut[], loops: Record<string, LoopOut>): Bindings {
  const channels = sources.flatMap((s) => s.channels);
  const sourceLabel = (name: string) => sources.find((s) => s.name === name)?.label ?? name;
  return {
    schema,
    sources,
    channels,
    loops: Object.values(loops),
    actuators: Object.values(schema.actuators),
    sourceLabel,
    channelLabel(key) {
      const c = channels.find((ch) => `${ch.source}.${ch.measurand}` === key);
      return c ? `${sourceLabel(c.source)} · ${c.label || c.measurand} (${c.unit})` : key;
    },
  };
}
