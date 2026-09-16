/**
 * Small helpers shared between pages and the status bar. They live apart
 * from the page modules so that those export components only, which lets
 * the dev server hot-swap a page without re-running the whole app.
 */
import { useMemo } from "react";
import type { ChannelOut, ProgrammerState, ReaderRun, SessionRow } from "@flyball/client";
import { useQuery, useRig, useStream, type QueryState, type useRecording } from "@flyball/react";

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

/** Live reader runs from `/ws/readers`, keyed by name. */
export function useReaderRuns() {
  return useStream("readers", {} as Record<string, ReaderRun>, (held, message) => {
    const next = { ...held };
    for (const { name, ...run } of message.readers) next[name] = run;
    return next;
  }).state;
}

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
      /** One channel's series, as the store has it. */
      series: (channel: Pick<ChannelOut, "source" | "measurand">) =>
        id === null ? undefined : rig.seriesExportUrl(id, channel.source, channel.measurand, "csv"),
      /** A whole chart's counterpart: only a single-channel chart has one file behind it. */
      channels: (channels: Array<Pick<ChannelOut, "source" | "measurand">>) =>
        id === null || channels.length !== 1 ? undefined : rig.seriesExportUrl(id, channels[0]!.source, channels[0]!.measurand, "csv"),
      /** One loop's ticks. */
      ticks: (loop: string) => (id === null ? undefined : rig.ticksExportUrl(id, loop, "csv")),
    }),
    [rig, id],
  );
}
