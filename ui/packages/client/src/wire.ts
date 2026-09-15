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
  reference: number | null;
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
