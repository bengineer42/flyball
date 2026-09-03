/**
 * The chart buffer.
 *
 * A ring of samples taken from each published state. Kept apart from the React
 * tree because it is plain data with plain rules: fixed capacity, append-only,
 * and every field nullable — a rig without a dry sensor simply has nulls in
 * that column, and the chart skips them rather than drawing a line to zero.
 */

import type { RigState } from "../api/types";
import { lineStatus } from "../domain/readings";

export interface Sample {
  /** Seconds since the rig started. */
  time: number;
  process: number | null;
  dry: number | null;
  wet: number | null;
  setPoint: number | null;
  expected: number | null;
  temperature: number | null;
  wetFlow: number | null;
  dryFlow: number | null;
}

export function sampleOf(state: RigState): Sample {
  const process = lineStatus(state.readings, "process");
  const dry = lineStatus(state.readings, "dry");
  const wet = lineStatus(state.readings, "wet");
  return {
    time: state.time,
    process: process.kind === "reading" ? process.reading.humidity : null,
    dry: dry.kind === "reading" ? dry.reading.humidity : null,
    wet: wet.kind === "reading" ? wet.reading.humidity : null,
    setPoint: state.controller ? state.controller.set_point : null,
    expected: state.expected_humidity ?? null,
    temperature: process.kind === "reading" ? process.reading.temperature : null,
    wetFlow: state.pumps ? state.pumps.flows.wet : null,
    dryFlow: state.pumps ? state.pumps.flows.dry : null,
  };
}

export function append(buffer: Sample[], sample: Sample, capacity: number): Sample[] {
  // The daemon republishes on reconnect; a repeated timestamp is a duplicate,
  // not a new sample, and would otherwise show as a vertical spike.
  const last = buffer[buffer.length - 1];
  if (last && sample.time <= last.time) return buffer;
  const next = buffer.length >= capacity ? buffer.slice(buffer.length - capacity + 1) : buffer.slice();
  next.push(sample);
  return next;
}

/** The tail of the buffer covering the last `seconds`. */
export function window(buffer: Sample[], seconds: number): Sample[] {
  if (buffer.length === 0) return buffer;
  const end = buffer[buffer.length - 1].time;
  const from = end - seconds;
  const index = buffer.findIndex((sample) => sample.time >= from);
  return index <= 0 ? buffer : buffer.slice(index);
}

/** Does any sample carry this series? Decides whether to offer it at all. */
export function hasSeries(buffer: Sample[], key: keyof Sample): boolean {
  return buffer.some((sample) => sample[key] !== null);
}
