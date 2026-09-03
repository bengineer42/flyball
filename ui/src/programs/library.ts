/**
 * Program storage.
 *
 * Programs live in the browser until the daemon can hold them (R1). That is a
 * deliberate stopgap, not a design: the export format below is the same JSON
 * the planned `POST /api/programs` takes, so saved work survives the move.
 */

import type { Program, Step } from "../api/types";
import { newId, STEP_DESCRIPTORS } from "./steps";

const KEY = "humctrl.programs.v1";
export const EXPORT_VERSION = 1;

export interface ProgramFile {
  format: "humctrl.program";
  version: number;
  program: Program;
}

export function emptyProgram(name = "New program"): Program {
  const now = Date.now() / 1000;
  return {
    id: newId("prog"),
    name,
    description: "",
    record_as: null,
    steps: [],
    created: now,
    modified: now,
  };
}

/** A worked example, so the builder is not a blank page on first run. */
export function exampleProgram(): Program {
  const program = emptyProgram("Step response 40 → 60");
  program.description =
    "Settle at 40%, record, step to 60%, hold until it settles, then stop.";
  program.record_as = "step-response";
  program.steps = [
    { ...STEP_DESCRIPTORS.start_controller.create(), humidity: 40 },
    { ...STEP_DESCRIPTORS.hold.create(), mode: "at", target: 40, tolerance: 1, min_duration: 60 },
    STEP_DESCRIPTORS.start_recording.create(),
    { ...STEP_DESCRIPTORS.set_point.create(), humidity: 60 },
    { ...STEP_DESCRIPTORS.hold.create(), mode: "at", target: 60, tolerance: 1, min_duration: 120 },
    STEP_DESCRIPTORS.stop_recording.create(),
    STEP_DESCRIPTORS.stop_pumps.create(),
  ];
  return program;
}

export function loadPrograms(): Program[] {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as Program[]) : [];
  } catch {
    // A corrupt store should not take the whole app down with it.
    return [];
  }
}

export function savePrograms(programs: Program[]): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(programs));
  } catch {
    /* private mode, quota — the in-memory copy still works for this session */
  }
}

export function toFile(program: Program): string {
  const file: ProgramFile = { format: "humctrl.program", version: EXPORT_VERSION, program };
  return JSON.stringify(file, null, 2);
}

export class ProgramParseError extends Error {}

/** Parse an exported file, re-issuing ids so an import never collides. */
export function fromFile(text: string): Program {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new ProgramParseError("That file is not JSON.");
  }
  const file = parsed as Partial<ProgramFile>;
  const program = (file.format === "humctrl.program" ? file.program : parsed) as Program | undefined;
  if (!program || !Array.isArray(program.steps)) {
    throw new ProgramParseError("No program in that file.");
  }
  const steps: Step[] = program.steps.filter((step) => step && step.kind in STEP_DESCRIPTORS);
  if (steps.length !== program.steps.length) {
    throw new ProgramParseError("That file contains step kinds this build does not know.");
  }
  const now = Date.now() / 1000;
  return {
    ...program,
    id: newId("prog"),
    name: program.name || "Imported program",
    steps: steps.map((step) => ({ ...step, id: newId() })),
    created: program.created ?? now,
    modified: now,
  };
}

export function download(filename: string, text: string): void {
  const blob = new Blob([text], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
