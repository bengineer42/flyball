/**
 * Small helpers shared between pages and the status bar. They live apart
 * from the page modules so that those export components only, which lets
 * the dev server hot-swap a page without re-running the whole app.
 */
import type { ProgrammerState, ReaderRun, SessionRow } from "@flyball/client";
import { useStream, type QueryState, type useRecording } from "@flyball/react";

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
