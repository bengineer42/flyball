/**
 * Wire types, one per shape the server publishes, mirroring
 * `controller/src/flyball/server/schemas.py` and the routes. See the book's
 * "HTTP and websocket API" page; nothing here is application-specific.
 *
 * Everything on the wire is named by **address**: a signal's
 * (`furnace.zone1`), a namespace's (`hum_sensors.dry`), a device's
 * (`furnace`), or a controller's, which is the address of the writable
 * signal it drives.
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

/** A closed interval `[low, high]`: a plausible range, a warn or alarm band, a clamp. */
export type Band = [number, number];

/** A signal's or node's dotted path: `device[.namespace…].signal`. */
export type Address = string;

/**
 * The access set in force, as lowercase letters in the order r, p, w:
 * `"rp"` (read, publish), `"w"` (write), `"rw"` (a setting), `"rpw"`.
 * `p` implies `r`.
 */
export type Access = string;

// region Values

/** One value on one signal at one instant. */
export interface ReadingOut {
  signal: Address;
  time_ns: Nanoseconds;
  value: number;
}

/** Signals under one node at one instant; `values` keyed by name relative to `node`. */
export interface SampleOut {
  node: Address;
  time_ns: Nanoseconds;
  values: Record<string, number>;
}

/** The last reading on a signal, without repeating its address. */
export interface LatestOut {
  time_ns: Nanoseconds;
  value: number;
}

/** What a writable signal was last set to, after limits, and by whom. */
export interface WriteOut {
  value: number | null;
  /** What was asked for, when the clamp changed it. */
  requested: number | null;
  at_limit: "low" | "high" | null;
  /** The controller driving it (it refuses manual demands), or null. */
  controller: Address | null;
}

/** One entry of a `/ws/writes` frame: a `WriteOut` with the signal it belongs to. */
export interface WriteStateOut extends WriteOut {
  signal: Address;
}

// endregion

// region Devices

export type Level = 10 | 20 | 30 | 40;

/** Something true of a device now: offline, railed, slow. Lives in state, not a log. */
export interface Condition {
  /** Stable and machine-readable: `offline`, `slow`, `railed`. */
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

/** One signal of a device's tree, with its metadata as in force and its latest values. */
export interface SignalOut {
  /** The last segment of `address`. */
  name: string;
  address: Address;
  access: Access;
  /** A display name; `""` when the driver gave none, in which case show `name`. */
  label: string;
  /** The quantity's name (`temperature`, `power`); what is measured or set, independent of any device. */
  quantity: string;
  unit: string;
  /** The unit's dimension (`Temperature`), so a client can tell what may drive or be compared with what. */
  dimension: string | null;
  dtype: string;
  shape: number[];
  /** The values a reading can plausibly take, for a gauge or an axis. */
  range: Band | null;
  precision: number | null;
  /** The band a value is normal inside; outside it, a warning. */
  warn: Band | null;
  /** The band a value is acceptable inside; outside it, an alarm. */
  alarm: Band | null;
  /** The signal's own poll period; null: the enclosing node's. */
  poll_s: number | null;
  /** What a demand is clamped to, in the signal's unit; a writable signal only. */
  limits: Band | null;
  /** Sibling signals that must be set in the same demand as this one. */
  together: string[];
  /** The last reading, once there has been one. */
  latest: LatestOut | null;
  /** The last committed state of a writable signal, once it has been set. */
  write: WriteOut | null;
}

/** A namespace of a device's tree: a sub-device or grouping, with what is under it. */
export interface NamespaceOut {
  name: string;
  address: Address;
  /** Read (and written) as one sample / one demand. */
  atomic: boolean;
  label: string;
  poll_s: number | null;
  signals: TreeNode[];
}

/** An entry of a device's `signals` tree: a signal, or a namespace nesting more. */
export type TreeNode = SignalOut | NamespaceOut;

/** True for a namespace entry of a tree; the other branch is a `SignalOut`. */
export function isNamespace(node: TreeNode): node is NamespaceOut {
  return "signals" in node;
}

export interface CommandOut {
  name: string;
  description: string | null;
  /** Only meaningful on a simulated device (a scripted fault, a disturbance); shown on the simulation page. */
  simulation: boolean;
}

/** How the runtime is polling a device; null on a `DeviceOut` when nothing on it is polled. */
export interface RunOut {
  period_s: number | null;
  running: boolean;
  last_read_ns: Nanoseconds | null;
}

/**
 * One entry of `GET /api/devices`: the tree with live values, commands,
 * state and conditions. Every device has one name rig-wide, whatever its
 * driver.
 */
export interface DeviceOut {
  name: string;
  /** A display name; null when the rig gave none, in which case show `name`. */
  label: string | null;
  /** `device`, or `simulation` for an application's own simulation device (kept on the simulation page). */
  kind: string;
  /** The rig file's tag for it (`sim_daq`, `scpi`); null for a device built in code. */
  driver: string | null;
  /** The Python class (`SimDaq`). */
  type: string;
  /** The rig file's name for the link it was built on, or null. */
  link: string | null;
  poll_s: number | null;
  signals: TreeNode[];
  commands: CommandOut[];
  state: DeviceState;
  /** What the device reports of itself, then what the runtime knows of polling it (`offline`, `slow`). */
  conditions: Condition[];
  run: RunOut | null;
}

/** One entry of a `/ws/devices` frame: a polled device's run as it reads, fails or is restarted. */
export interface DeviceRunOut extends RunOut {
  name: string;
  /** The runtime's conditions on polling it (`offline`, `slow`); the device's own are in `state`. */
  conditions: Condition[];
  state: DeviceState;
}

export interface CommandSchema {
  description: string | null;
  arguments: JsonSchema;
  simulation: boolean;
}

/** A signal as a `DeviceSchema` lists it: what a gauge, an axis or a target entry needs. */
export interface SignalSchema {
  address: Address;
  access: Access;
  label: string;
  quantity: string;
  unit: string;
  dimension: string | null;
  range: Band | null;
  precision: number | null;
  limits: Band | null;
}

/** `GET /api/devices/{name}/schema`: how a device is configured, set, what it reports, and its commands. */
export interface DeviceSchema {
  name: string;
  label: string | null;
  type: string;
  driver: string | null;
  description: string | null;
  config: JsonSchema;
  settings: JsonSchema;
  state: JsonSchema;
  /** By path relative to the device (`zone1`, `position.x`). */
  signals: Record<string, SignalSchema>;
  commands: Record<string, CommandSchema>;
}

/** `GET /api/schema`: every device's schema, by name. */
export interface RigSchema {
  devices: Record<string, DeviceSchema>;
}

/** `GET /api/sim/device`: the application's own simulation device. */
export interface DeviceView {
  config: Record<string, unknown>;
  settings: Record<string, unknown>;
  state: DeviceState;
}

// endregion

// region Reading

/**
 * `GET /api/read/{address}`: one of the three, by what the address named.
 * A signal answers a `reading`; an atomic namespace a `sample`; a device,
 * or a namespace read over several transactions, `samples`. The other two
 * keys are absent, so `"reading" in result` discriminates.
 */
export type ReadOut = { reading: ReadingOut } | { sample: SampleOut } | { samples: SampleOut[] };

// endregion

// region Controllers

export type ControllerMode = "manual" | "open" | "regulating";

export interface LawConfig {
  tag: string;
  [gain: string]: unknown;
}

/**
 * What maps the setpoint into the target's unit before the law corrects:
 * `demand = feedforward(setpoint) + correction`. `setpoint` passes it
 * through (the units agree), `none` gives 0 (the law does all the work),
 * `affine` is `gain * setpoint + bias`, `table` interpolates `(setpoint,
 * demand)` breakpoints, held flat beyond the ends. `affine`/`table` take an
 * optional `rate_gain` for a ramp's rate of change.
 */
export type FeedforwardConfig =
  | { tag: "setpoint" }
  | { tag: "none" }
  | { tag: "affine"; gain: number; bias?: number; rate_gain?: number | null }
  | { tag: "table"; points: Array<[number, number]>; rate_gain?: number | null }
  | { tag: string; [arg: string]: unknown };

/**
 * A controller as a client sees it: it binds one publishing signal
 * (`source`) to one writable signal (`target`), and is named by `target`.
 */
export interface ControllerOut {
  /** The target's address. */
  name: Address;
  /** The target signal's display name; null when it has none, in which case show `name`. */
  label: string | null;
  target: Address;
  source: Address;
  default: boolean;
  mode: ControllerMode;
  /** The law's config and state flattened, `tag` first; null when there is no law. */
  law: Record<string, unknown> | null;
  feedforward: FeedforwardConfig;
  /** The unit `demand`, `expected` and `correction` are in: the target's. */
  demand_unit: string;
  /** A fixed setpoint, or the name of the trajectory being followed (a ramp). */
  reference: number | string | null;
  /** The reference resolved at the last tick, in the source's unit: a ramp's current value. */
  setpoint: number | null;
  /** Whether the reference has landed: a number has; a trajectory once it finishes. */
  arrived?: boolean;
  correction: number;
  demand: number | null;
  expected: number | null;
  delivered_correction: number | null;
  /** On the source at the last tick. */
  reading: ReadingOut | null;
}

/** A signal a controller may bind to, with what a form shows beside it. */
export interface SignalChoice {
  address: Address;
  device: string;
  label: string;
  unit: string;
  dimension: string | null;
  /** A publishing signal's plausible values, for a setpoint entry. */
  range: Band | null;
  /** A writable signal's clamp, for a demand entry. */
  limits: Band | null;
}

/** A stored tuning as the controller form offers it: its name, the law it is for, and the gains. */
export interface TuningChoice {
  name: string;
  /** The law's tag (`PI`, `PID`, ...), so a form can offer the tunings for one law. */
  law: string;
  config: LawConfig;
}

/** `GET /api/controllers/schema`: what a form needs to make a controller on this rig right now. */
export interface ControllerSchema {
  /** Every publishing signal. */
  sources: SignalChoice[];
  /** Every writable signal. */
  targets: SignalChoice[];
  /** JSON Schema of the law config union, discriminated on `tag`. */
  laws: JsonSchema;
  /** JSON Schema of the feedforward config union, discriminated on `tag`. */
  feedforwards: JsonSchema;
  tunings: TuningChoice[];
  /** source address → the controller already regulating it. */
  regulated: Record<Address, Address>;
  /** target address → the controller already driving it (its own name). */
  driven: Record<Address, Address>;
}

export interface NewController {
  /** The writable signal to drive; the controller's name. */
  target: Address;
  /** The publishing signal to regulate. */
  source: Address;
  /** A config, or a stored tuning's name. */
  law?: LawConfig | string | null;
  /**
   * A config, or a tag alone (`"setpoint"`). Omitted: `setpoint` when the
   * units agree, else `none`. `"setpoint"` across differing units is refused (409).
   */
  feedforward?: FeedforwardConfig | string | null;
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

// endregion

// region Waits

export type WaitOutcome = "pending" | "fired" | "timeout" | "interrupted";

/** What the rig is waiting on: a program step's prompt, a settle test, a hold. */
export interface WaitState {
  name: string;
  message: string | null;
  outcome: WaitOutcome;
  since_ns: Nanoseconds;
  timeout_s: number | null;
  /** Waiting on a person (a program's `wait`): only these deserve a button. A hold or an arrival settles by itself. */
  prompt: boolean;
}

// endregion

// region Rig

/** `GET /api/clock`: the rig's timebase. On a simulated rig `now_ns` runs faster than the wall clock. */
export interface ClockOut {
  start_time_ns: Nanoseconds;
  now_ns: Nanoseconds;
  elapsed_ns: Nanoseconds;
  tags: Record<string, Nanoseconds>;
  /** How fast the rig's time runs against wall time; only a simulated rig is ever not 1. */
  speed: number;
}

/** `GET /api/health`: a one-call summary of the rig's condition. */
export interface Health {
  ok: boolean;
  rig: string | null;
  uptime_s: number;
  /** Each polled device. */
  devices: Record<string, { running: boolean; last_read_ns: Nanoseconds | null }>;
  /** Each controller's mode, by name. */
  controllers: Record<Address, ControllerMode>;
  /** Every device's own conditions, then the runtime's (`offline`, `slow`, `write_failed`). */
  conditions: Array<Condition & { device: string }>;
  /**
   * Signals outside their warn/alarm band (not double-counted), plus
   * conditions at WARNING (warn) or ERROR (alarm); `max_level` is 40/30/0.
   */
  alarms: { warn: number; alarm: number; max_level: number };
  /** The names of the registered waits. */
  waits: string[];
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

// endregion

// region Streams

/**
 * Messages on the websockets, by stream. Every socket sends what the rig
 * knows on connect, then every 50 ms one frame of whatever changed (the
 * newest value per key, so at most one sample per node per frame).
 */
export interface Streams {
  samples: { samples: SampleOut[] };
  writes: { writes: WriteStateOut[] };
  controllers: { controllers: ControllerOut[] };
  devices: { devices: DeviceRunOut[] };
  waits: { waits: WaitState[] };
  events: { events: Event[] };
}

export type StreamName = keyof Streams;

// endregion

// region History -- what a session recorded. Offsets are nanoseconds from the session's start.

export interface SessionRow {
  id: number;
  start_ns: Nanoseconds;
  end_ns: Nanoseconds | null;
  version: string | null;
  config: unknown;
  hardware: unknown;
  details: unknown;
}

/** A device as declared for the session: its name, and what built it. */
export interface DeviceRow {
  id: number;
  address: Address;
  driver: string | null;
  config: unknown;
  label: string | null;
}

/** One signal as declared for the session; `address` is the key everything else uses. */
export interface SignalRow {
  id: number;
  device_id: number;
  address: Address;
  quantity: string;
  unit: string;
  access: Access;
  dtype: string;
  shape: number[];
  label: string | null;
  range: Band | null;
  precision: number | null;
  warn: Band | null;
  alarm: Band | null;
  limits: Band | null;
}

/** A writable signal whose write states the session recorded. */
export interface WriteRow {
  signal: SignalRow;
  driver: string | null;
  limits: Band | null;
}

/** A controller is named by the signal it drives; `source` is the one it regulates. */
export interface ControllerRow {
  name: Address;
  source: Address;
  law: unknown;
  /** The feedforward's config; null in sessions recorded before there was one. */
  feedforward: unknown;
}

export interface Point {
  offset_ns: Nanoseconds;
  value: number;
}

/** How a series was thinned: exactly one of the three set. */
export interface Downsample {
  every: number | null;
  bucket_ns: number | null;
  max_points: number | null;
}

/** One signal over a window. */
export interface Series {
  signal: SignalRow;
  points: Point[];
  downsample: Downsample | null;
}

/** One controller step, for control plots. */
export interface Tick {
  /** The controller's name: the address of the signal it drives. */
  controller: Address;
  offset_ns: Nanoseconds;
  mode: string;
  /** The law's share of the demand, in the target's unit. */
  correction: number;
  reading: number | null;
  /** The setpoint resolved at this tick, in the source's unit (a ramp's value, not its name). */
  setpoint: number | null;
  demand: number | null;
  expected: number | null;
  delivered_correction: number | null;
}

/** What one writable signal was set to at one instant. */
export interface WriteStateRow extends WriteOut {
  offset_ns: Nanoseconds;
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

// endregion

// region Programs -- the library and the programmer

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

/** What `check` says: is the document accepted, and what does it name that the rig lacks. */
export interface ProgramCheck {
  ok: boolean;
  error: string | null;
  normalised: unknown;
  /** By step index (a string key on the wire): a controller, tuning or device the rig lacks right now. It may still run. */
  warnings: Record<string, string>;
}

export interface ProgrammerState {
  running: boolean;
  step: number;
  steps: number;
  command: string | null;
  failed: boolean;
  error: string | null;
}

// endregion

// region Simulation -- `/api/sim`: the rig's clock and its `sim_*` links, for a rig with nothing real on it.

export interface SimulationClock {
  /** Rig seconds per wall second, as configured. */
  speed: number;
  /**
   * Rig seconds per wall second as measured since the last poll of `/api/sim`;
   * null on the first poll or one under 0.1 s after the last. Below `speed`
   * when the rig cannot keep up.
   */
  measured: number | null;
  /** Time moves only when stepped (`POST /api/sim/clock/step`). */
  stepped: boolean;
  now_ns: Nanoseconds;
}

/** What the rig last delivered on one signal read off a plant: noise and all, not the model's state. */
export interface SimulationReading {
  value: number;
  unit: string;
  precision: number | null;
  /** The device that read it. */
  device: string;
  /** The plant port it came off. */
  port: string;
  /** How old the reading is, in rig seconds. */
  age_s: number;
}

/** A simulated plant as `/api/sim` reports it; `input`/`output` for one port, `inputs`/`outputs` for many. */
export interface SimulationPlant {
  /** The `sim_plant` / `sim_furnace` config as it now stands: `model`, `tau_s`, `gain`, `noise`, ... */
  config: Record<string, unknown>;
  /**
   * Each config field with a `live` link, to its path into this object
   * (`outputs.*`, `stats.noise`; see `resolveLive`). A field not here is a
   * parameter, not a state.
   */
  links: Record<string, string>;
  /** What each linked field's path found: a number, or one per port. */
  live: Record<string, number | Record<string, number>>;
  /** By signal address, for every signal some `sim_daq` reads off the plant. */
  readings: Record<Address, SimulationReading>;
  /** By signal address, over the recent readings: σ about a moving mean, and the slope per minute. */
  stats: { noise: Record<Address, number>; rate_per_min: Record<Address, number> };
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
      /** Whether the application attached a device of its own at `/api/sim/device`. */
      device: boolean;
    };

// endregion
