/**
 * The step registry.
 *
 * One descriptor per step kind, mirroring the one-dataclass-per-command shape
 * of `humctrl.cmds`. A descriptor carries everything the rest of the UI needs
 * to handle that step — its defaults, its editable fields, how to say what it
 * does in a sentence, and what the rig must provide for it to run — so adding
 * a step kind is a single entry here and nothing else changes. The editor and
 * the summary line are generic over `fields`; neither knows any step by name.
 */

import type { Capabilities } from "../api/transport";
import type {
  FlowRequest,
  LawConfig,
  Pace,
  Step,
  StepKind,
  TimeUnit,
} from "../api/types";
import { FLOW_SCALE_LABELS } from "../domain/flows";
import { formatDurationLong, formatQuantity } from "../domain/units";

export type StepGroup = "control" | "pumps" | "recording";

export const GROUP_LABELS: Record<StepGroup, string> = {
  control: "Control",
  pumps: "Pumps (manual)",
  recording: "Recording",
};

/**
 * A declarative field. The generic editor renders these; composite kinds
 * ("flow", "law", "pace") have their own small editors because a BlendFlow or
 * a control law is more than one number.
 */
export type FieldSpec<S> =
  | {
      kind: "number";
      key: Extract<keyof S, string>;
      label: string;
      min?: number;
      max?: number;
      step?: number;
      /** "%", "s", or the rig's flow units when this is a flow. */
      unit?: string | "flow";
      optional?: boolean;
      help?: string;
    }
  | {
      kind: "select";
      key: Extract<keyof S, string>;
      label: string;
      options: ReadonlyArray<{ value: string; label: string; help?: string }>;
      help?: string;
    }
  | {
      kind: "text";
      key: Extract<keyof S, string>;
      label: string;
      placeholder?: string;
      optional?: boolean;
      help?: string;
    }
  | { kind: "flow"; key: Extract<keyof S, string>; label: string; optional?: boolean; help?: string }
  | { kind: "law"; key: Extract<keyof S, string>; label: string; optional?: boolean; help?: string }
  | { kind: "pace"; key: Extract<keyof S, string>; label: string; help?: string };

/** What a summary line is allowed to know about the rig. */
export interface SummaryContext {
  flowUnits?: string | null;
}

export interface StepDescriptor<S extends Step = Step> {
  kind: S["kind"];
  label: string;
  group: StepGroup;
  /** One line on what the step does, shown in the add-step palette. */
  blurb: string;
  /** Does this step block the program until a condition is met? */
  blocking: boolean;
  create(): S;
  fields: ReadonlyArray<FieldSpec<S>>;
  summary(step: S, context: SummaryContext): string;
  /** Rig capabilities the step needs. Checked before a run, not during. */
  requires: ReadonlyArray<keyof Capabilities>;
}

let counter = 0;
export function newId(prefix = "step"): string {
  counter += 1;
  return `${prefix}-${Date.now().toString(36)}-${counter.toString(36)}`;
}

export const DEFAULT_FLOW: FlowRequest = {
  value: 1,
  scale: "full_range_max",
  on_overdrive: null,
};

export const DEFAULT_LAW: LawConfig = { type: "PI", kp: 0.5, ki: 0.1, tt: 0 };

export const TIME_UNITS: ReadonlyArray<{ value: TimeUnit; label: string }> = [
  { value: "second", label: "per second" },
  { value: "minute", label: "per minute" },
  { value: "hour", label: "per hour" },
];

export function describeFlow(flow: FlowRequest | null | undefined, units?: string | null): string {
  if (!flow) return "rig default flow";
  if (flow.scale === "absolute") return formatQuantity(flow.value, units);
  return `${(flow.value * 100).toFixed(0)}% ${FLOW_SCALE_LABELS[flow.scale]}`;
}

export function describeLaw(law: LawConfig | string | null | undefined): string {
  if (!law) return "rig default law";
  if (typeof law === "string") return law;
  if (law.type === "open_loop") return "open loop";
  const terms: string[] = [];
  if (law.kp !== undefined) terms.push(`kp ${law.kp}`);
  if (law.ki !== undefined && law.type !== "P") terms.push(`ki ${law.ki}`);
  if (law.kd !== undefined && law.type === "PID") terms.push(`kd ${law.kd}`);
  return `${law.type} (${terms.join(", ")})`;
}

export function describePace(pace: Pace): string {
  if (pace.kind === "rate") {
    const per = pace.per === "second" ? "s" : pace.per === "minute" ? "min" : "h";
    return `${pace.value}%/${per}`;
  }
  return `over ${formatDurationLong(pace.seconds)}`;
}

// region Descriptors

const startController: StepDescriptor<Extract<Step, { kind: "start_controller" }>> = {
  kind: "start_controller",
  label: "Start controller",
  group: "control",
  blurb: "Begin regulating to a set point. Omit the law to run open loop.",
  blocking: false,
  requires: ["pumps"],
  create: () => ({
    id: newId(),
    kind: "start_controller",
    humidity: 50,
    flow: { ...DEFAULT_FLOW },
    control_law: { ...DEFAULT_LAW },
  }),
  fields: [
    { kind: "number", key: "humidity", label: "Set point", min: 0, max: 100, step: 0.1, unit: "%" },
    { kind: "flow", key: "flow", label: "Total flow", optional: true },
    {
      kind: "law",
      key: "control_law",
      label: "Control law",
      optional: true,
      help: "Cleared: the rig's configured default law.",
    },
  ],
  summary: (step, ctx) =>
    `Regulate to ${step.humidity}% at ${describeFlow(step.flow, ctx.flowUnits)}, ${describeLaw(step.control_law)}`,
};

const setPoint: StepDescriptor<Extract<Step, { kind: "set_point" }>> = {
  kind: "set_point",
  label: "Change set point",
  group: "control",
  blurb: "Step the running controller to a new set point.",
  blocking: false,
  requires: ["controller"],
  create: () => ({ id: newId(), kind: "set_point", humidity: 50 }),
  fields: [
    { kind: "number", key: "humidity", label: "Set point", min: 0, max: 100, step: 0.1, unit: "%" },
  ],
  summary: (step) => `Set point to ${step.humidity}%`,
};

const ramp: StepDescriptor<Extract<Step, { kind: "ramp" }>> = {
  kind: "ramp",
  label: "Ramp",
  group: "control",
  blurb: "Walk the set point to a target at a rate, or over a fixed time.",
  blocking: true,
  requires: ["pumps"],
  create: () => ({
    id: newId(),
    kind: "ramp",
    target: 70,
    pace: { kind: "rate", value: 5, per: "minute" },
    start_from: "reading",
    flow: null,
    control_law: null,
  }),
  fields: [
    { kind: "number", key: "target", label: "Target", min: 0, max: 100, step: 0.1, unit: "%" },
    { kind: "pace", key: "pace", label: "Pace" },
    {
      kind: "select",
      key: "start_from",
      label: "Start from",
      options: [
        { value: "reading", label: "Current reading" },
        { value: "target", label: "Current set point" },
        { value: "value", label: "A fixed humidity" },
      ],
    },
    {
      kind: "number",
      key: "start_value",
      label: "Start humidity",
      min: 0,
      max: 100,
      step: 0.1,
      unit: "%",
      optional: true,
      help: "Used only when starting from a fixed humidity.",
    },
    { kind: "flow", key: "flow", label: "Total flow", optional: true },
    { kind: "law", key: "control_law", label: "Control law", optional: true },
  ],
  summary: (step) => {
    const from =
      step.start_from === "value"
        ? `${step.start_value ?? 0}%`
        : step.start_from === "target"
          ? "the set point"
          : "the reading";
    return `Ramp from ${from} to ${step.target}% ${describePace(step.pace)}`;
  },
};

const hold: StepDescriptor<Extract<Step, { kind: "hold" }>> = {
  kind: "hold",
  label: "Hold until",
  group: "control",
  blurb: "Block until the reading satisfies a condition, or the timeout expires.",
  blocking: true,
  requires: [],
  create: () => ({
    id: newId(),
    kind: "hold",
    mode: "at",
    target: null,
    tolerance: 1,
    timeout: 600,
    min_duration: 30,
    min_readings: 5,
  }),
  fields: [
    {
      kind: "select",
      key: "mode",
      label: "Condition",
      options: [
        { value: "at", label: "Within tolerance of the target" },
        { value: "above", label: "Above the target" },
        { value: "below", label: "Below the target" },
        { value: "cross", label: "Crosses the target (direction from the reading)" },
      ],
    },
    {
      kind: "number",
      key: "target",
      label: "Target",
      min: 0,
      max: 100,
      step: 0.1,
      unit: "%",
      optional: true,
      help: "Cleared: hold against whatever set point the controller is holding.",
    },
    { kind: "number", key: "tolerance", label: "Tolerance", min: 0, max: 50, step: 0.1, unit: "%" },
    {
      kind: "number",
      key: "min_duration",
      label: "Must hold for",
      min: 0,
      step: 1,
      unit: "s",
      help: "The condition must be true across this whole window.",
    },
    { kind: "number", key: "min_readings", label: "Minimum readings", min: 1, step: 1 },
    {
      kind: "number",
      key: "timeout",
      label: "Timeout",
      min: 0,
      step: 1,
      unit: "s",
      optional: true,
      help: "Cleared: wait indefinitely. A step with no timeout can stall a run.",
    },
  ],
  summary: (step) => {
    const target = step.target === null || step.target === undefined ? "the set point" : `${step.target}%`;
    const condition =
      step.mode === "at"
        ? `within ±${step.tolerance}% of ${target}`
        : step.mode === "cross"
          ? `crosses ${target}`
          : `${step.mode} ${target}`;
    const window = step.min_duration > 0 ? ` for ${formatDurationLong(step.min_duration)}` : "";
    const timeout = step.timeout ? `, timeout ${formatDurationLong(step.timeout)}` : ", no timeout";
    return `Hold until ${condition}${window}${timeout}`;
  },
};

const setBlend: StepDescriptor<Extract<Step, { kind: "set_blend" }>> = {
  kind: "set_blend",
  label: "Set blend",
  group: "pumps",
  blurb: "Drive the pumps directly by ratio and total flow. Suspends the controller.",
  blocking: false,
  requires: ["pumps"],
  create: () => ({
    id: newId(),
    kind: "set_blend",
    wet_fraction: 0.5,
    flow: { ...DEFAULT_FLOW },
  }),
  fields: [
    {
      kind: "number",
      key: "wet_fraction",
      label: "Wet fraction",
      min: 0,
      max: 1,
      step: 0.01,
      help: "0 is all dry, 1 is all wet.",
    },
    { kind: "flow", key: "flow", label: "Total flow" },
  ],
  summary: (step, ctx) =>
    `Blend ${(step.wet_fraction * 100).toFixed(0)}% wet at ${describeFlow(step.flow, ctx.flowUnits)}`,
};

const setFlows: StepDescriptor<Extract<Step, { kind: "set_flows" }>> = {
  kind: "set_flows",
  label: "Set flows",
  group: "pumps",
  blurb: "Each line in absolute flow units. Suspends the controller.",
  blocking: false,
  requires: ["pumps"],
  create: () => ({ id: newId(), kind: "set_flows", wet: 0, dry: 0 }),
  fields: [
    { kind: "number", key: "wet", label: "Wet flow", min: 0, step: 0.01, unit: "flow" },
    { kind: "number", key: "dry", label: "Dry flow", min: 0, step: 0.01, unit: "flow" },
  ],
  summary: (step, ctx) =>
    `Flows wet ${formatQuantity(step.wet, ctx.flowUnits)}, dry ${formatQuantity(step.dry, ctx.flowUnits)}`,
};

const setEfforts: StepDescriptor<Extract<Step, { kind: "set_efforts" }>> = {
  kind: "set_efforts",
  label: "Set efforts",
  group: "pumps",
  blurb: "Each line as normalised drive, 0 to 1. Suspends the controller.",
  blocking: false,
  requires: ["pumps"],
  create: () => ({ id: newId(), kind: "set_efforts", wet: 0, dry: 0 }),
  fields: [
    { kind: "number", key: "wet", label: "Wet effort", min: 0, max: 1, step: 0.01 },
    { kind: "number", key: "dry", label: "Dry effort", min: 0, max: 1, step: 0.01 },
  ],
  summary: (step) => `Efforts wet ${step.wet.toFixed(2)}, dry ${step.dry.toFixed(2)}`,
};

const stopPumps: StepDescriptor<Extract<Step, { kind: "stop_pumps" }>> = {
  kind: "stop_pumps",
  label: "Stop pumps",
  group: "pumps",
  blurb: "Both lines to zero, leaving the loop running.",
  blocking: false,
  requires: ["pumps"],
  create: () => ({ id: newId(), kind: "stop_pumps" }),
  fields: [],
  summary: () => "Stop the pumps",
};

const startRecording: StepDescriptor<Extract<Step, { kind: "start_recording" }>> = {
  kind: "start_recording",
  label: "Start recording",
  group: "recording",
  blurb: "Begin writing samples, optionally flagging the point.",
  blocking: false,
  requires: ["recording"],
  create: () => ({ id: newId(), kind: "start_recording", name: null }),
  fields: [
    { kind: "text", key: "name", label: "Flag", placeholder: "e.g. step-response", optional: true },
  ],
  summary: (step) => (step.name ? `Start recording "${step.name}"` : "Start recording"),
};

const stopRecording: StepDescriptor<Extract<Step, { kind: "stop_recording" }>> = {
  kind: "stop_recording",
  label: "Stop recording",
  group: "recording",
  blurb: "Stop writing samples.",
  blocking: false,
  requires: ["recording"],
  create: () => ({ id: newId(), kind: "stop_recording", name: null }),
  fields: [{ kind: "text", key: "name", label: "Flag", optional: true }],
  summary: (step) => (step.name ? `Stop recording "${step.name}"` : "Stop recording"),
};

const flag: StepDescriptor<Extract<Step, { kind: "flag" }>> = {
  kind: "flag",
  label: "Add flag",
  group: "recording",
  blurb: "Mark this instant in the record.",
  blocking: false,
  requires: ["recording"],
  create: () => ({ id: newId(), kind: "flag", flag: "" }),
  fields: [{ kind: "text", key: "flag", label: "Flag", placeholder: "e.g. door-opened" }],
  summary: (step) => `Flag "${step.flag || "unnamed"}"`,
};

// endregion

type Descriptors = { [K in StepKind]: StepDescriptor<Extract<Step, { kind: K }>> };

export const STEP_DESCRIPTORS: Descriptors = {
  start_controller: startController,
  set_point: setPoint,
  ramp,
  hold,
  set_blend: setBlend,
  set_flows: setFlows,
  set_efforts: setEfforts,
  stop_pumps: stopPumps,
  start_recording: startRecording,
  stop_recording: stopRecording,
  flag,
};

export const STEP_ORDER: ReadonlyArray<StepKind> = [
  "start_controller",
  "set_point",
  "ramp",
  "hold",
  "set_blend",
  "set_flows",
  "set_efforts",
  "stop_pumps",
  "start_recording",
  "stop_recording",
  "flag",
];

/** The descriptor for a step, typed against that step. */
export function describe(step: Step): StepDescriptor<Step> {
  return STEP_DESCRIPTORS[step.kind] as StepDescriptor<Step>;
}

export function summarise(step: Step, context: SummaryContext = {}): string {
  return describe(step).summary(step, context);
}
