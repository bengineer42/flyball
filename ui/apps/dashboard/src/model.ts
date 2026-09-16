/**
 * Small helpers shared between pages and the status bar. They live apart
 * from the page modules so that those export components only, which lets
 * the dev server hot-swap a page without re-running the whole app.
 */
import { useMemo } from "react";
import type { Address, ProgrammerState, SessionRow } from "@flyball/client";
import { useQuery, useRig, type QueryState, type useRecording } from "@flyball/react";

export type Programmer = QueryState<ProgrammerState>;
/** `ProgrammerState.step` is the running step's index; people count from one, as the `step` events do. */
export const stepOf = (p: ProgrammerState) => `${Math.min(p.step + 1, p.steps)}/${p.steps}`;
/** The reserved program name that opens the detail page in create mode. */
export const NEW = "new";

export type Recording = ReturnType<typeof useRecording>;

/** A session's display name from its free-form details, else its id. */
export const sessionName = (s: SessionRow) => {
  const d = s.details as Record<string, unknown> | null | undefined;
  return d && typeof d.name === "string" && d.name ? d.name : `session #${s.id}`;
};

/**
 * Where the store holds what the live charts are drawing: the open session's
 * export URLs, or nothing while the rig is not recording. A chart offers
 * these beside the points the browser is holding.
 */
export function useRecordingExports() {
  const rig = useRig();
  const recording = useQuery(() => rig.recording(), [rig], { refreshMs: 15000 });
  const id = recording.data?.id ?? null;
  return useMemo(
    () => ({
      id,
      /** One signal's series, as the store has it. */
      series: (address: Address) => (id === null ? undefined : rig.seriesExportUrl(id, address, "csv")),
      /** A whole chart's counterpart: only a single-signal chart has one file behind it. */
      signals: (signals: ReadonlyArray<{ address: Address }>) => (id === null || signals.length !== 1 ? undefined : rig.seriesExportUrl(id, signals[0]!.address, "csv")),
      /** One controller's ticks; `controller` is the address of the signal it drives. */
      ticks: (controller: Address) => (id === null ? undefined : rig.ticksExportUrl(id, controller, "csv")),
      /** One writable signal's write states. */
      writes: (address: Address) => (id === null ? undefined : rig.writesExportUrl(id, address, "csv")),
    }),
    [rig, id],
  );
}
