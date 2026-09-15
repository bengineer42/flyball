/**
 * Wire types, one per shape the server publishes. See the book's
 * "Wire format" and "HTTP and websocket API" pages; nothing here is
 * application-specific.
 */

/** A JSON Schema document as the server emits it: draft 2020-12 plus the `unit` and `dimension` keys. */
export type JsonSchema = {
  type?: string | string[];
  title?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema | JsonSchema[];
  prefixItems?: JsonSchema[];
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  allOf?: JsonSchema[];
  enum?: unknown[];
  const?: unknown;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  multipleOf?: number;
  readOnly?: boolean;
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
  discriminator?: { propertyName: string; mapping?: Record<string, string> };
  /** Unit symbol the value is in, e.g. "°C". Put there by `Quantity(unit)`. */
  unit?: string;
  /** Dimension name, e.g. "Temperature". */
  dimension?: string;
  /** Decimal places to show. */
  precision?: number;
  [key: string]: unknown;
};

export type Nanoseconds = number;

export interface Channel {
  source: string;
  measurand: string;
}

/** A channel plus what a gauge or axis needs. */
export interface ChannelOut extends Channel {
  unit: string;
  label: string;
  range: [number, number] | null;
  precision: number | null;
  /** The band a value is normal inside; outside it, a warning. */
  warn?: [number, number] | null;
  /** The band a value is acceptable inside; outside it, an alarm. */
  alarm?: [number, number] | null;
}

export interface SampleOut {
  source: string;
  seq: number;
  time_ns: Nanoseconds;
  values: Record<string, number>;
}

export interface ReadingOut {
  time_ns: Nanoseconds;
  value: number;
}

export interface SourceOut {
  name: string;
  channels: ChannelOut[];
  latest: SampleOut | null;
}

export type Level = 10 | 20 | 30 | 40;

export interface Condition {
  kind: string;
  level: Level;
  message: string;
  since_ns: Nanoseconds;
}

/** Every device state carries `conditions`; the rest is the device's own, described by its schema. */
export interface DeviceState {
  conditions: Condition[];
  [field: string]: unknown;
}

export interface DeviceView {
  config: Record<string, unknown>;
  settings: Record<string, unknown>;
  state: DeviceState;
}

export interface CommandSchema {
  description: string | null;
  arguments: JsonSchema;
  /** Only meaningful on a simulated device (a scripted fault, a disturbance); shown on the simulation page. */
  simulation?: boolean;
}

export interface DeviceSchema {
  name: string;
  type: string;
  description: string | null;
  config: JsonSchema;
  settings: JsonSchema;
  state: JsonSchema;
  commands: Record<string, CommandSchema>;
}

export interface ActuatorSchema extends DeviceSchema {
  demand_unit: string | null;
}

/** What a reader declares about one of its sources: the measurands with their display metadata. */
export interface SourceDeclaration {
  name: string;
  measurands: Record<string, { label: string; unit: string; dimension: string; range: [number, number] | null; precision: number | null }>;
}

export interface ReaderSchema extends DeviceSchema {
  sources: SourceDeclaration[];
}

export interface ReaderRun {
  period_s: number | null;
  running: boolean;
  last_read_ns: Nanoseconds | null;
  conditions: Condition[];
}

export interface ReaderView extends DeviceView {
  run: ReaderRun;
}

/** `GET /api/schema`: everything the rig is, keyed by name. */
export interface RigSchema {
  actuators: Record<string, ActuatorSchema>;
  readers: Record<string, ReaderSchema>;
}

export interface DeviceSummary {
  name: string;
  type: string;
  description: string | null;
}

export type LoopMode = "manual" | "open" | "regulating";

export interface LawConfig {
  tag: string;
  [gain: string]: unknown;
}

export interface LoopOut {
  name: string;
  channel: ChannelOut;
  default: boolean;
  mode: LoopMode;
  law: Record<string, unknown>;
  /** A fixed setpoint, or the name of the trajectory generator being followed (a ramp). */
  reference: number | string | null;
  correction: number | null;
  demand: number | null;
  expected: number | null;
  delivered_correction: number | null;
  reading: ReadingOut | null;
}

export type SignalOutcome = "pending" | "fired" | "timeout" | "interrupted";

export interface SignalState {
  name: string;
  message: string | null;
  outcome: SignalOutcome;
  since_ns: Nanoseconds;
  timeout_s: number | null;
}

export interface SessionRow {
  id: number;
  start_ns: Nanoseconds;
  end_ns: Nanoseconds | null;
  [field: string]: unknown;
}

/** History rows carry ids and offsets from the session's start, not wall-clock. */
export interface Series {
  channel: { source: { id: number; name: string }; measurand: { id: number; name: string; unit: string; label: string } };
  points: Array<{ offset_ns: Nanoseconds; value: number }>;
  downsample: { every: number | null; bucket_ns: number | null; max_points: number | null } | null;
}

/** Messages on the websockets, by stream. */
export interface Streams {
  samples: SampleOut;
  loops: { loops: LoopOut[] };
  actuators: { actuators: Array<{ name: string; state: DeviceState }> };
  readers: { readers: Array<{ name: string } & ReaderRun> };
  signals: { signals: SignalState[] };
  events: { events: Event[] };
}

export type StreamName = keyof Streams;

/** `GET /api/clock`: the rig's timebase. On a simulated rig `now_ns` runs faster than the wall clock. */
export interface ClockOut {
  start_time_ns: Nanoseconds;
  now_ns: Nanoseconds;
  elapsed_ns: Nanoseconds;
  tags: Record<string, Nanoseconds>;
}

/** `GET /api/health`: a one-call summary of the rig's condition. */
export interface Health {
  ok: boolean;
  rig: string | null;
  uptime_s: number;
  readers: Record<string, { running: boolean; last_read_ns: Nanoseconds | null }>;
  loops: Record<string, unknown>;
  conditions: Condition[];
  signals: SignalState[];
  recording: boolean;
}

export interface ErrorDetail {
  detail: string;
}

export type EventLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR";

/** One log-like event from the rig: `GET /api/events` and `/ws/events`. `details` is whatever the emitter attached, or null. */
export interface Event {
  time_ns: Nanoseconds;
  level: EventLevel;
  scope: string;
  subject: string;
  kind: string;
  message: string;
  details: unknown;
}

/** Alias for `Event`, for files where the DOM's `Event` is also in scope. */
export type RigEvent = Event;

// History rows: what a session recorded. Offsets are nanoseconds from the session's start.

export interface SourceRow {
  id: number;
  name: string;
  kind: string | null;
}

export interface MeasurandRow {
  id: number;
  name: string;
  unit: string;
  label: string | null;
}

export interface ChannelRow {
  source: SourceRow;
  measurand: MeasurandRow;
}

export interface ActuatorRow {
  name: string;
  kind: string;
  config: unknown;
}

export interface LoopRow {
  name?: string;
  actuator: ActuatorRow;
  channel: ChannelRow;
  config: unknown;
}

export interface Tick {
  loop: string;
  offset_ns: Nanoseconds;
  mode: string;
  correction: number;
  reading: number | null;
  setpoint: number | null;
  demand: number | null;
}

export interface SessionEvent {
  offset_ns: Nanoseconds;
  kind: string;
  source: string | null;
  detail: unknown;
  id: number | null;
}

export interface Span {
  id: number;
  kind: string;
  label: string;
  start_ns: Nanoseconds;
  end_ns: Nanoseconds | null;
  parent_id: number | null;
  details: unknown;
}

/** Body for `POST /api/recording`. */
export interface StartRecording {
  details?: unknown;
  version?: string;
  config?: unknown;
  hardware?: unknown;
}

// The program library.

export type ProgramFormat = "yaml" | "toml" | "json";

export interface ProgramRow {
  id: number;
  name: string;
  format: ProgramFormat;
  /** The document, verbatim, in `format`. */
  body: string;
  created_ns: Nanoseconds;
  sha256: string;
  label: string | null;
  notes: unknown;
}

export interface ProgramCheck {
  ok: boolean;
  error: string | null;
  normalised: unknown;
}

export interface ProgrammerState {
  running: boolean;
  step: number;
  steps: number;
  command: string | null;
}

// Making loops.

export interface ActuatorChoice {
  name: string;
  type: string;
  /** null: takes any channel; else only channels in this unit. */
  demand_unit: string | null;
  /** `source.measurand` names this actuator may regulate. */
  channels: string[];
}

/** `GET /api/loops/schema`: what a form needs to make a loop on this rig right now. */
export interface LoopSchema {
  channels: ChannelOut[];
  actuators: ActuatorChoice[];
  /** JSON Schema of the law config union, discriminated on `tag`. */
  laws: JsonSchema;
  tunings: string[];
  /** channel name → the loop already regulating it. */
  regulated: Record<string, string>;
}

export interface NewLoop {
  channel: string;
  actuator: string;
  law?: LawConfig | string | null;
  default?: boolean;
  min_period_s?: number | null;
}

export type ValueSource = "process" | "setpoint" | "demand";
export type Transfer = "none" | "carry" | "track" | "reset";

export interface RegulateRequest {
  at: number | ValueSource;
  tuning?: LawConfig | string | null;
  transfer?: Transfer;
}

// The simulation: `/api/sim` -- the rig's clock and its `sim_plant` links, for a rig with nothing real on it.

export interface SimulationClock {
  /** Rig seconds per wall second, as configured. */
  speed: number;
  /**
   * Rig seconds per wall second as measured since the last poll of `/api/sim`;
   * null on the first poll or one under 0.1 s after the last. Below `speed`
   * when the rig cannot keep up (polls overlapping at 300×).
   */
  measured: number | null;
  /** Time moves only when stepped (`POST /api/sim/clock/step`). */
  stepped: boolean;
  now_ns: Nanoseconds;
}

/** What a reader last delivered from one of a plant's output ports: noise and all, not the model's state. */
export interface SimulationReading {
  value: number;
  unit: string;
  precision: number | null;
  /** The reader that read it. */
  reader: string;
  /** How old the reading is, in rig seconds. */
  age_s: number;
}

/** A simulated plant as `/api/sim` reports it; `input`/`output` for one port, `inputs`/`outputs` for many. */
export interface SimulationPlant {
  /** The `sim_plant` / `sim_furnace` config as it now stands: `kind`, `tau_s`, `gain`, `noise`, ... */
  config: Record<string, unknown>;
  /**
   * Each config field with a `live` link, to its path into this object
   * (`outputs.*`, `stats.noise`; see `resolveLive`). A field not here is a
   * parameter, not a state.
   */
  links: Record<string, string>;
  /** What each linked field's path found: a number, or one per port. */
  live: Record<string, number | Record<string, number>>;
  /** Per output port some reader reads (`output` for a single-port plant). */
  readings: Record<string, SimulationReading>;
  /** Per port, over the recent readings: σ about a moving mean, and the slope per minute. */
  stats: { noise: Record<string, number>; rate_per_min: Record<string, number> };
  input?: number;
  output?: number;
  inputs?: Record<string, number>;
  outputs?: Record<string, number>;
}

/** `GET /api/sim`: `{simulated: false}` for a rig with real hardware. */
export type Simulation =
  | { simulated: false }
  | {
      simulated: true;
      name: string | null;
      /** The rig file, if one built it; where `POST /api/sim/save` writes. */
      path: string | null;
      clock: SimulationClock;
      plants: Record<string, SimulationPlant>;
      /** What has changed since the last save: `clock`, `links.<plant>`. */
      changed: string[];
    };
