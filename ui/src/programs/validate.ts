/**
 * Static checks on a program, run as it is edited.
 *
 * These catch the mistakes the rig would otherwise catch at step 7 of 9, with
 * the pumps already running: starting a controller twice
 * (`ControllerAlreadyRunningError`), changing a set point before there is a
 * controller to change, a hold with no timeout that will never return. Errors
 * block the run button; warnings do not, because plenty of legitimate runs
 * look odd (a program that only drives the pumps needs no controller at all).
 */

import type { Capabilities } from "../api/transport";
import type { Program, PumpsSpec, Step } from "../api/types";
import { flowsToEfforts, overdriven } from "../domain/flows";
import { describe } from "./steps";

export type IssueLevel = "error" | "warning";

export interface Issue {
  level: IssueLevel;
  message: string;
  /** The step it belongs to, or undefined for whole-program issues. */
  stepId?: string;
}

export interface ValidationContext {
  pumpsSpec?: PumpsSpec | null;
  capabilities?: Capabilities | null;
}

export function validateProgram(program: Program, context: ValidationContext = {}): Issue[] {
  const issues: Issue[] = [];
  const { pumpsSpec, capabilities } = context;

  if (program.steps.length === 0) {
    issues.push({ level: "warning", message: "This program has no steps." });
  }
  if (!program.name.trim()) {
    issues.push({ level: "error", message: "Give the program a name." });
  }

  let controllerStarted = false;
  let recording = false;

  program.steps.forEach((step: Step) => {
    const at = { stepId: step.id };

    switch (step.kind) {
      case "start_controller":
        if (controllerStarted) {
          issues.push({
            ...at,
            level: "error",
            message:
              "A controller is already running at this point. The rig refuses a second one — stop the rig or drop this step.",
          });
        }
        controllerStarted = true;
        break;

      case "set_point":
        if (!controllerStarted) {
          issues.push({
            ...at,
            level: "error",
            message: "No controller is running yet, so there is no set point to change.",
          });
        }
        break;

      case "ramp":
        if (step.start_from === "value" && step.start_value === undefined) {
          issues.push({ ...at, level: "error", message: "Set the humidity to start the ramp from." });
        }
        if (step.start_from === "target" && !controllerStarted) {
          issues.push({
            ...at,
            level: "error",
            message: "Starting from the set point needs a controller already running.",
          });
        }
        if (step.pace.kind === "duration" && step.pace.seconds <= 0) {
          issues.push({ ...at, level: "error", message: "A ramp duration must be greater than zero." });
        }
        if (step.pace.kind === "rate" && step.pace.value <= 0) {
          issues.push({ ...at, level: "error", message: "A ramp rate must be greater than zero." });
        }
        controllerStarted = true;
        break;

      case "hold":
        if (!step.timeout) {
          issues.push({
            ...at,
            level: "warning",
            message: "No timeout: if the condition is never met this step holds the run indefinitely.",
          });
        }
        if ((step.target === null || step.target === undefined) && !controllerStarted) {
          issues.push({
            ...at,
            level: "error",
            message: "Holding against the set point needs a controller, or give this step its own target.",
          });
        }
        break;

      case "set_flows":
        if (pumpsSpec && overdriven(flowsToEfforts({ wet: step.wet, dry: step.dry }, pumpsSpec))) {
          issues.push({
            ...at,
            level: "error",
            message: `Beyond the pumps: max wet ${pumpsSpec.max_flows.wet}, max dry ${pumpsSpec.max_flows.dry}.`,
          });
        }
        if (controllerStarted) {
          issues.push({
            ...at,
            level: "warning",
            message: "Driving the pumps directly suspends the running controller.",
          });
        }
        break;

      case "set_blend":
      case "set_efforts":
        if (controllerStarted) {
          issues.push({
            ...at,
            level: "warning",
            message: "Driving the pumps directly suspends the running controller.",
          });
        }
        break;

      case "start_recording":
        if (recording) {
          issues.push({ ...at, level: "warning", message: "Already recording." });
        }
        recording = true;
        break;

      case "stop_recording":
        if (!recording) {
          issues.push({ ...at, level: "warning", message: "Not recording at this point." });
        }
        recording = false;
        break;

      case "flag":
        if (!step.flag.trim()) {
          issues.push({ ...at, level: "error", message: "A flag needs a name." });
        }
        if (!recording) {
          issues.push({
            ...at,
            level: "warning",
            message: "Not recording, so this flag lands in whatever record is open later.",
          });
        }
        break;

      case "stop_pumps":
        break;
    }

    if (capabilities) {
      for (const need of describe(step).requires) {
        if (!capabilities[need]) {
          issues.push({
            ...at,
            level: "warning",
            message: `This rig does not report ${need} support; the step may fail.`,
          });
        }
      }
    }
  });

  if (recording) {
    issues.push({
      level: "warning",
      message: "The program ends while still recording. Add a stop-recording step to close the record.",
    });
  }

  return issues;
}

export function hasErrors(issues: Issue[]): boolean {
  return issues.some((issue) => issue.level === "error");
}

export function issuesFor(issues: Issue[], stepId: string): Issue[] {
  return issues.filter((issue) => issue.stepId === stepId);
}
