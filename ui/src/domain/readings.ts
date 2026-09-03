/**
 * Reading availability.
 *
 * Three states, not two. A rig with no dry sensor and a rig whose dry sensor
 * just threw are different situations and get different treatment on screen:
 * the first is "not fitted" and is unremarkable, the second is a fault.
 */

import type { ReadingLine, ReadingsState, ReadingState } from "../api/types";

export type LineStatus =
  /** A sensor is fitted and reading. */
  | { kind: "reading"; reading: ReadingState }
  /** Fitted, but the last read raised. */
  | { kind: "failed"; error: string }
  /** Nothing on this line, or nothing yet — normal on a rig without it. */
  | { kind: "absent" };

export function lineStatus(readings: ReadingsState | undefined, line: ReadingLine): LineStatus {
  const error = readings?.errors?.[line];
  if (error) return { kind: "failed", error };
  const reading = readings?.[line];
  if (reading) return { kind: "reading", reading };
  return { kind: "absent" };
}

export function humidityOf(status: LineStatus): number | null {
  return status.kind === "reading" ? status.reading.humidity : null;
}

export function temperatureOf(status: LineStatus): number | null {
  return status.kind === "reading" ? status.reading.temperature : null;
}

export const LINE_LABELS: Record<ReadingLine, string> = {
  process: "Process",
  dry: "Dry line",
  wet: "Wet line",
};

/** Why a line might be missing, in words the operator can act on. */
export const LINE_ABSENT_NOTES: Record<ReadingLine, string> = {
  process: "No process reading yet — the loop has not completed a pass.",
  dry: "No dry-line sensor on this rig. The controller uses the configured dry humidity.",
  wet: "No wet-line sensor on this rig. The controller uses the configured wet humidity.",
};
