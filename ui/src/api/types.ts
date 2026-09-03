/**
 * Wire types.
 *
 * These mirror `humctrl.server.schemas` where it exists, and the shape the
 * routes are being written towards where it does not yet. Anything the daemon
 * cannot serve today is marked `PLANNED` and is listed in API-REQUIREMENTS.md;
 * the UI degrades rather than assuming those endpoints answer.
 */

export type Percent = number;
export type Normalised = number;

// region Readings

/** A sensor reading. `time` is seconds, not the domain's nanoseconds. */
export interface ReadingState {
  time: number;
  humidity: Percent;
  temperature: number;
  source?: string | null;
}

export type ReadingLine = "process" | "dry" | "wet";

/**
 * The three lines. Any line may be absent (no such sensor on this rig) or
 * present in `errors` (the sensor is fitted and failed to read). The two cases
 * are shown differently, so a missing dry line is never mistaken for a fault.
 */
export interface ReadingsState {
  process?: ReadingState | null;
  dry?: ReadingState | null;
  wet?: ReadingState | null;
  errors: Partial<Record<ReadingLine, string>>;
}

// endregion

// region Pumps

export type FlowScale = "absolute" | "blend_max" | "full_range_max";
export type OnOverdrive = "raise" | "clamp";

/** A total flow. `scale` disambiguates the three BlendFlow variants. */
export interface FlowRequest {
  value: number;
  scale: FlowScale;
  on_overdrive?: OnOverdrive | null;
}

export interface FlowState {
  value: number;
  scale: FlowScale;
}

export interface Lines<T> {
  wet: T;
  dry: T;
}

export type Flows = Lines<number>;
export type Efforts = Lines<Normalised>;

export interface PumpsState {
  flows: Flows;
  efforts: Efforts;
}

/** Pump limits. Absent when no pumps are attached to the rig. */
export interface PumpsSpec {
  max_flows: Lines<number>;
  full_range_max_flow: number;
  units?: string | null;
}

export interface Blend {
  wet_fraction: Normalised;
  flow: number;
}

// endregion

// region Controller

export type LawType = "open_loop" | "P" | "PI" | "PID";

export interface LawConfig {
  type: LawType;
  kp?: number;
  ki?: number;
  kd?: number;
  tt?: number;
}

export interface ControllerState {
  type: string;
  set_point: Percent;
  /** May leave 0-100 when railed. */
  demand: number;
  flow: FlowState;
  suspended: boolean;
  law?: LawConfig | string | null;
}

// endregion

/** The whole rig, as pushed over /ws/telemetry. */
export interface RigState {
  time: number;
  running: boolean;
  recording: boolean;
  pumps?: PumpsState | null;
  pumps_spec?: PumpsSpec | null;
  controller?: ControllerState | null;
  readings: ReadingsState;
  /** Humidity the current blend is expected to hold, given the source lines. */
  expected_humidity?: Percent | null;
  /**
   * PLANNED (R5): flow as measured by in-line sensors. `pumps.flows` is
   * commanded flow — effort times max flow — not measurement. Absent on every
   * rig today; the UI labels the two differently so they are never confused.
   */
  measured_flows?: Lines<number> | null;
  /** PLANNED: the program the loop is stepping through, if any. */
  program?: ProgramStatus | null;
}

export interface Warning {
  time: number;
  error: string;
}

// region Programs (PLANNED server-side; the builder is fully local until then)

export type StepKind =
  | "start_controller"
  | "set_point"
  | "ramp"
  | "hold"
  | "set_blend"
  | "set_flows"
  | "set_efforts"
  | "stop_pumps"
  | "start_recording"
  | "stop_recording"
  | "flag";

export type TimeUnit = "second" | "minute" | "hour";

/** How fast a ramp runs: a rate, or a fixed duration for the whole ramp. */
export type Pace =
  | { kind: "rate"; value: number; per: TimeUnit }
  | { kind: "duration"; seconds: number };

export type StartFrom = "reading" | "target" | "value";
export type TargetMode = "above" | "below" | "cross" | "at";

export interface StepBase {
  id: string;
  kind: StepKind;
  /** Free-text note, recorded as a span label when the run is recorded. */
  note?: string;
}

export interface StartControllerStep extends StepBase {
  kind: "start_controller";
  humidity: Percent;
  flow?: FlowRequest | null;
  control_law?: LawConfig | null;
}

export interface SetPointStep extends StepBase {
  kind: "set_point";
  humidity: Percent;
}

export interface RampStep extends StepBase {
  kind: "ramp";
  target: Percent;
  pace: Pace;
  start_from: StartFrom;
  start_value?: Percent;
  flow?: FlowRequest | null;
  control_law?: LawConfig | null;
}

export interface HoldStep extends StepBase {
  kind: "hold";
  mode: TargetMode;
  /** Omitted: hold against whatever set point the controller is holding. */
  target?: Percent | null;
  tolerance: Percent;
  timeout?: number | null;
  min_duration: number;
  min_readings: number;
}

export interface SetBlendStep extends StepBase {
  kind: "set_blend";
  wet_fraction: Normalised;
  flow: FlowRequest;
}

export interface SetFlowsStep extends StepBase {
  kind: "set_flows";
  wet: number;
  dry: number;
}

export interface SetEffortsStep extends StepBase {
  kind: "set_efforts";
  wet: Normalised;
  dry: Normalised;
}

export interface StopPumpsStep extends StepBase {
  kind: "stop_pumps";
}

export interface StartRecordingStep extends StepBase {
  kind: "start_recording";
  name?: string | null;
}

export interface StopRecordingStep extends StepBase {
  kind: "stop_recording";
  name?: string | null;
}

export interface FlagStep extends StepBase {
  kind: "flag";
  flag: string;
}

export type Step =
  | StartControllerStep
  | SetPointStep
  | RampStep
  | HoldStep
  | SetBlendStep
  | SetFlowsStep
  | SetEffortsStep
  | StopPumpsStep
  | StartRecordingStep
  | StopRecordingStep
  | FlagStep;

export interface Program {
  id: string;
  name: string;
  description?: string;
  /** Recorded under this flag when the program starts, if set. */
  record_as?: string | null;
  steps: Step[];
  created: number;
  modified: number;
}

export interface ProgramStatus {
  program_id?: string | null;
  name: string;
  running: boolean;
  step: number;
  step_count: number;
  started?: number | null;
}

// endregion
