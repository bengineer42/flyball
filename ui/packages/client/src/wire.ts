/**
 * Wire types, one per shape the server publishes, mirroring
 * `engine/src/flyball/server/schemas.py` and the routes. See the book's
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

/** A closed interval `[low, high]`: a plausible range, a warning or alarm band, a clamp. */
export type Bounds = [number, number];

/** A signal's or node's dotted path: `device[.namespace…].signal`. */
export type Address = string;

/**
 * The access set in force, as lowercase letters in the order r, p, w:
 * `"rp"` (read, publish), `"w"` (write), `"rw"` (a setting), `"rpw"`.
 * `p` implies `r`.
 */
export type Access = string;

// region Values

/**
 * What a reading carries: a number for a float/int signal, a boolean, a
 * string (an enum's value), or a JSON structure (a mode's config, a list of
 * conditions). The signal's `dtype` says which.
 */
export type Value = number | boolean | string | null | Value[] | { [key: string]: Value };

/** The wire's name for a signal's value type. */
export type Dtype = "float" | "int" | "bool" | "str" | "enum" | "json";

/**
 * What a signal is to its device: `demand` (settable, with a readback; the
 * only thing a controller drives), `readout` (produced by the device, never
 * written from outside), `setting` (re-set by a command), `config`
 * (effective at build). Inputs are bindings, not signals.
 */
export type Role = "demand" | "readout" | "setting" | "config";

/** One value on one signal at one instant. */
export interface ReadingOut {
  signal: Address;
  time_ns: Nanoseconds;
  value: Value;
}

/** A demand's write record, riding along with its reading in a `SampleOut`: no `value` -- that
 * is already in `values`, keyed the same way. What `/ws/writes` used to carry, per signal. */
export interface WriteMetaOut {
  /** What was asked for, when the clamp changed it. */
  requested: number | null;
  at_limit: "low" | "high" | null;
  /** The controller driving it (it refuses manual demands), or null. */
  controller: Address | null;
}

/** Signals under one node at one instant; `values` keyed by name relative to `node`.
 *
 * `writes` carries the write record for each demand the sample includes (present only for
 * those, keyed the same way as `values`): a demand's reading and its write record arrive
 * together now, so `/ws/writes` no longer exists.
 */
export interface SampleOut {
  node: Address;
  time_ns: Nanoseconds;
  values: Record<string, Value>;
  writes?: Record<string, WriteMetaOut>;
}

/** The last reading on a signal, without repeating its address. */
export interface LatestOut {
  time_ns: Nanoseconds;
  value: Value;
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
  dtype: Dtype;
  shape: number[];
  role: Role;
  /** The section, as `{axis: name}`: `{"line": "dry"}`; a second grouping across the tree. Empty without one. */
  tags: Record<string, string>;
  /** The value the signal has before anything reads or sets it (a mode's starting state), or null. */
  initial: Value;
  /** What a gauge or axis spans: the signal's own range, else its limits, else the unit's scale. */
  range: Bounds | null;
  precision: number | null;
  /** The band a value is normal inside; outside it, a warning. */
  warning: Bounds | null;
  /** The band a value is acceptable inside; outside it, an alarm. */
  alarm: Bounds | null;
  /** The signal's own poll period; null: the enclosing node's. */
  poll_s: number | null;
  /** What a demand is clamped to, in the signal's unit, as effective now; a demand only. */
  limits: Bounds | null;
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
  /** The method only records; the rig commits the device after it. */
  commit: boolean;
  /** What the device's `mode` output becomes when this runs, if it has one. */
  mode: Value;
  /** Puts a controller driving the device into manual and runs; without it the command is refused while one is active. */
  interrupts: boolean;
  /** A synthesised `set_<name>`: the path of the demand it sets. */
  demand_of: string | null;
  /** Argument name -> the path (relative to the device) of the demand or setting it is a value for. */
  links: Record<string, string>;
}

/** An input a device declares: what it follows, and what the rig bound to that role. */
export interface InputOut {
  name: string;
  label: string;
  quantity: string;
  unit: string;
  /** The address bound to this role, or null. */
  bound: Address | null;
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
  /** The rig file's `driver:` for it (`sim_daq`, `scpi`); null for a device built in code. */
  driver: string | null;
  /** The Python class (`SimDaq`). */
  class_name: string;
  /** The rig file's name for the link it was built on, or null. */
  link: string | null;
  poll_s: number | null;
  signals: TreeNode[];
  commands: CommandOut[];
  /** What the device follows, by role. */
  inputs: Record<string, InputOut>;
  /** Implements `read`: polled on a period. */
  readable: boolean;
  /** Implements `commit`: has demands. */
  writable: boolean;
  /** What the device says of itself (its `conditions` signal), then what the runtime knows of polling it (`offline`, `slow`). */
  conditions: Condition[];
  run: RunOut | null;
}

/** One entry of `/ws/samples`'s `runs`: a polled device's run as it reads, fails or is restarted. */
export interface DeviceRunOut extends RunOut {
  name: string;
  /** The runtime's conditions on polling it (`offline`, `slow`); the device's own are on its `conditions` signal. */
  conditions: Condition[];
}

/**
 * A command's request schema. An argument that is a value for a demand or
 * setting carries `x-signal` (its address), `unit`, and the effective
 * `minimum`/`maximum`; it is not required, since the rig fills it from the
 * signal's current value.
 */
export interface CommandSchema {
  description: string | null;
  arguments: JsonSchema;
  simulation: boolean;
  commit: boolean;
  mode: Value;
  interrupts: boolean;
  demand_of: string | null;
}

/** A signal as a `DeviceSchema` lists it: what a gauge, an axis, a form or a target entry needs. */
export interface SignalSchema {
  address: Address;
  access: Access;
  role: Role;
  tags: Record<string, string>;
  label: string;
  quantity: string;
  unit: string;
  dimension: string | null;
  dtype: Dtype;
  /** The JSON schema of one value: a number, an enum's members, a structure. */
  value: JsonSchema;
  range: Bounds | null;
  precision: number | null;
  limits: Bounds | null;
}

/** An input as a `DeviceSchema` lists it. */
export interface InputSchema {
  label: string;
  quantity: string;
  unit: string;
  bound: Address | null;
}

/** `GET /api/devices/{name}/schema`: how a device is configured, its signals, inputs and commands. */
export interface DeviceSchema {
  name: string;
  label: string | null;
  class_name: string;
  driver: string | null;
  description: string | null;
  readable: boolean;
  writable: boolean;
  config: JsonSchema;
  /** By path relative to the device (`zone1`, `position.x`). */
  signals: Record<string, SignalSchema>;
  /** By role. */
  inputs: Record<string, InputSchema>;
  commands: Record<string, CommandSchema>;
}

/** `GET /api/schema`: every device's schema, by name. */
export interface RigSchema {
  devices: Record<string, DeviceSchema>;
}

/** `GET /api/sim/device`: the application's own simulation device: its config and its signals' current values by path. */
export interface DeviceView {
  config: Record<string, unknown>;
  values: Record<string, Value>;
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

/** Who drives the output: a person, or the law. Open loop is the `open_loop` law under `regulating`, not a mode. */
export type ControllerMode = "manual" | "regulating";

export interface LawConfig {
  type: string;
  [gain: string]: unknown;
}

/**
 * What maps the setpoint into the output's unit before the law corrects:
 * `output = feedforward(setpoint) + correction`. `setpoint` passes it
 * through (the units agree), `none` gives 0 (the law does all the work),
 * `affine` is `gain * setpoint + bias`, `table` interpolates `(setpoint,
 * output)` breakpoints, held flat beyond the ends. `affine`/`table` take an
 * optional `rate_gain` for a ramp's rate of change.
 */
export type FeedforwardConfig =
  | { type: "setpoint" }
  | { type: "none" }
  | { type: "affine"; gain: number; bias?: number; rate_gain?: number | null }
  | { type: "table"; points: Array<[number, number]>; rate_gain?: number | null }
  | { type: string; [arg: string]: unknown };

/**
 * A running trajectory as `ControllerOut.reference` shows it: `{type, ...its
 * config}` plus, once started, where it lands. Loosely typed on purpose (the
 * server allows extra keys), so the shape stays put as generators are added.
 * A `linear_ramp_setpoint` carries `end` and `pace` (a speed as `{value,
 * per}`, or a duration as `{seconds, nanoseconds}`); a `dwell` carries
 * `value` and `duration`; a `profile` its `segments` as given.
 */
export interface GeneratorOut {
  type: string;
  /** Seconds from the rig's start (`ClockOut.start_time_ns`) at which it lands; absent while endless or not yet started. */
  end_time?: number;
  /** A profile's segment in force, as an index into `segments`. */
  active?: number;
  [k: string]: unknown;
}

/** A span of time on the way in: unit keys that add (`{minutes: 1, seconds: 30}`). */
export type DurationSpec = Partial<Record<"nanoseconds" | "microseconds" | "milliseconds" | "seconds" | "minutes" | "hours" | "days", number>>;

/** A speed on the way in: one key naming the unit (`{per_minute: 10}`). */
export type SpeedSpec = { per_second: number } | { per_minute: number } | { per_hour: number } | { per_day: number };

/** Walk the setpoint from where it is to `end`, at a speed or over a duration. */
export interface LinearRampSpec {
  type: "linear_ramp_setpoint";
  pace: SpeedSpec | DurationSpec;
  end: number;
}

/** Sit at `value`; with no `duration` it never finishes of its own accord. */
export interface DwellSpec {
  type: "dwell";
  value: number;
  duration?: DurationSpec | null;
}

/** Segments in order; only the last may be endless. */
export interface ProfileSpec {
  type: "profile";
  segments: Array<LinearRampSpec | DwellSpec>;
}

/** A set-point generator as `PUT .../setpoint` and `POST .../regulate` take one in `at`; `GET /api/controllers/schema` lists them. */
export type GeneratorSpec = LinearRampSpec | DwellSpec | ProfileSpec | { type: string; [k: string]: unknown };

/**
 * A controller as a client sees it: it regulates one published signal
 * (`measured_signal`) through one demand (`output_signal`), and is named by
 * its output. The faceplate reads `measured`, `setpoint` and `output`.
 */
export interface ControllerOut {
  /** The output's address. */
  name: Address;
  /** The output signal's display name; null when it has none, in which case show `name`. */
  label: string | null;
  output_signal: Address;
  measured_signal: Address;
  default: boolean;
  mode: ControllerMode;
  /** The law's config and state flattened, `type` first; null when there is no law. */
  law: Record<string, unknown> | null;
  feedforward: FeedforwardConfig;
  /** The unit `output`, `expected` and `correction` are in: the output signal's. */
  output_unit: string;
  /** A fixed setpoint, or the trajectory being followed (a ramp, a dwell, a profile). */
  reference: number | GeneratorOut | null;
  /** The reference resolved at the last tick, in the measured unit: a ramp's current value. */
  setpoint: number | null;
  /** Whether the reference has landed: a number has; a trajectory once it finishes. */
  arrived?: boolean;
  correction: number;
  /** The last value asked of the output signal. */
  output: number | null;
  expected: number | null;
  delivered_correction: number | null;
  /** The measured signal's reading at the last tick. */
  measured: ReadingOut | null;
}

/** A signal a controller may bind to, with what a form shows beside it. */
export interface SignalChoice {
  address: Address;
  device: string;
  label: string;
  unit: string;
  dimension: string | null;
  /** A published signal's plausible values, for a setpoint entry. */
  range: Bounds | null;
  /** A demand's clamp, for an output entry. */
  limits: Bounds | null;
}

/** A stored tuning as the controller form offers it: its name, the law it is for, and the gains. */
export interface TuningChoice {
  name: string;
  /** The law's type (`PI`, `PID`, ...), so a form can offer the tunings for one law. */
  law: string;
  config: LawConfig;
}

/** `GET /api/controllers/schema`: what a form needs to make a controller on this rig right now. */
export interface ControllerSchema {
  /** Every published signal: what a controller may regulate. */
  measured: SignalChoice[];
  /** Every signal a controller may drive. */
  outputs: SignalChoice[];
  /** JSON Schema of the law config union, discriminated on `type`. */
  laws: JsonSchema;
  /** JSON Schema of the feedforward config union, discriminated on `type`. */
  feedforwards: JsonSchema;
  /** JSON Schema of the set-point generator config union (`GeneratorSpec`), discriminated on `type`. */
  generators: JsonSchema;
  tunings: TuningChoice[];
  /** measured signal address → the controller already regulating it. */
  regulated: Record<Address, Address>;
  /** output address → the controller already driving it (its own name). */
  driven: Record<Address, Address>;
}

export interface NewController {
  /** The demand to drive, its output; the controller's name. */
  output: Address;
  /** The published signal to regulate, its measured signal. */
  measured: Address;
  /** A config, or a stored tuning's name. */
  law?: LawConfig | string | null;
  /**
   * A config, or a type alone (`"setpoint"`). Omitted: `setpoint` when the
   * units agree, else `none`. `"setpoint"` across differing units is refused (409).
   */
  feedforward?: FeedforwardConfig | string | null;
  default?: boolean;
  min_period_s?: number | null;
}

export type ValueSource = "measured" | "setpoint" | "output";
export type Transfer = "none" | "carry" | "track" | "cold";

/** Where a controller is sent: a value, where it already is (`measured`/`setpoint`/`output`), or a trajectory to follow. */
export type SetpointSpec = number | ValueSource | GeneratorSpec;

/** Where a generator starts from: a value, or the controller's `setpoint`, `measured` (the reading) or `output`. */
export type StartSpec = number | ValueSource;

export interface RegulateRequest {
  at: SetpointSpec;
  /** For a generator in `at`: where it starts. Omitted, the current setpoint while regulating, else the last reading. */
  start?: StartSpec | null;
  tuning?: LawConfig | string | null;
  transfer?: Transfer;
}

// endregion

// region Activities

export type ActivityOutcome = "pending" | "fired" | "timeout" | "interrupted";

/** What the rig is waiting on: a program step's prompt, a settle test, a timed wait. */
export interface ActivityOut {
  name: string;
  message: string | null;
  outcome: ActivityOutcome;
  since_ns: Nanoseconds;
  timeout_s: number | null;
  /** Waiting on a person (a program's `prompt`): only these deserve a button. A timed wait or a settle finishes by itself. */
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
  /** The names of the registered activities. */
  activities: string[];
  recording: boolean;
  exposure?: Exposure | null;
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

// region Rig composition -- building the rig up from links, devices and a whole document; its versions; saving it

/**
 * The rig file's own shape: what `GET /api/rig/document`, `/api/rig/config`,
 * a version's `document`, and `POST /api/rig` take and return. `name` is
 * absent on a rig built with none.
 */
export interface RigDocument {
  name?: string;
  links: Record<string, Record<string, unknown>>;
  devices: Record<string, Record<string, unknown>>;
  controllers: Record<string, Record<string, unknown>>;
}

/** A link as the file writes one: `{name, type, ...its config}`. Body for `POST /api/links`, and what it returns. */
export interface LinkEntry {
  name: string;
  type: string;
  [key: string]: unknown;
}

/**
 * A device entry with its name, as `POST /api/devices` takes it: the file's
 * envelope (`driver`, `label`, `poll_s`, `inputs`) plus the driver's own
 * fields, flat beside the envelope.
 */
export interface NewDevice {
  name: string;
  driver: string;
  label?: string;
  poll_s?: number;
  inputs?: Record<string, string>;
  [key: string]: unknown;
}

/** `GET /api/rig/versions`: one version of the rig's history, newest first. */
export interface RigVersion {
  id: number;
  time_ns: Nanoseconds;
  reason: string;
  files: string[];
  /** The version this one was made from; the head moves on a restore rather than a new row being written. */
  parent?: number | null;
  /** The version the running rig is at. */
  head?: boolean;
}

/**
 * `GET /api/runner`: how this runner is serving -- its `runner:` config as
 * resolved (flags over environment over file). The token is never returned.
 */
export interface RunnerInfo {
  /** Where the runner listens: `unix:/abs/path` behind a front, `tcp:<host>:<port>` bare; null when unknown. */
  endpoint: string | null;
  root_path: string | null;
  mcp: boolean;
  compose: boolean;
  allow_save: boolean;
  allow_shutdown: boolean;
  store: string | null;
  programs: string | null;
  tunings: string | null;
  drivers: string | null;
  files: string[];
  /** As configured: how long the rolling scratch record is kept while nothing is recorded (`"1h"`); the size cap; retention; rotation; the store's cap. */
  keep?: string | null;
  keep_size?: string | null;
  retain?: string | null;
  rotate?: string | null;
  max_store?: string | null;
  /** The same, resolved: ns in the rig's clock and bytes; null or 0 for none. */
  keep_ns?: Nanoseconds | null;
  keep_bytes?: number | null;
  retain_ns?: Nanoseconds | null;
  rotate_ns?: Nanoseconds | null;
  max_bytes?: number | null;
}

/**
 * The operator verb (pending D-034): the stop button, and everything else that drives the rig,
 * needs it. The only verb string this client hard-codes; D-034 may rename it, nothing else here
 * changes shape.
 */
export const OPERATE = "operate";

/**
 * `GET <root>/api/auth`: who this caller is here, and what this rig's door is like. Answered by
 * the front for fronted rigs, and by the runner when bare -- the same shape either way, so the UI
 * never has to know which one it is talking to.
 *
 * The UI's rules, read off this and nothing else:
 * - `canOperate = verbs.includes(OPERATE)`;
 * - `signedIn = user !== null && scheme !== "local"`;
 * - `open = shape === "local"`;
 * - show the stop button iff `verbs.includes(OPERATE)`;
 * - `v !== 2`: show "front and UI versions differ" rather than guessing at an unknown shape.
 */
export interface AuthInfo {
  v: 2;
  /** How this rig's door is set up. */
  shape: "local" | "password" | "proxy" | "bare";
  /** How this caller got in. */
  scheme: "local" | "anonymous" | "session" | "token" | "proxy";
  user: { id: string; name: string; kind: "human" | "service" | "agent" } | null;
  /** This caller's verbs on this rig, sorted; the UI decides from these, never from `scheme` or `shape` alone. */
  verbs: string[];
  /** What a caller with no credential gets. */
  anonymous: "none" | "read";
  login: {
    /** The front offers the admin password (shape `password`). */
    password: boolean;
    /** A token may be pasted (bare runner). */
    token: boolean;
    /** Phase 3; false in Phase 1. */
    passkey: boolean;
    /** Phase 3: the IdP's display name. */
    sso: string | null;
  };
  /** Where the runner (or `flyball run`'s front) serves against where it was asked to; null or absent when not known. */
  exposure?: Exposure | null;
  /** The rig this request's path routes to (B1's Rig.Name); absent at a flyballd root, where no single rig applies. */
  rig?: string;
}

/** Who asked for the stop (`StopReport.actor`), and how they reached it. */
export interface StopActor {
  sub: string;
  sid: string;
  kind: string;
  via: "http" | "mcp" | "signal";
  detail: string;
}

/** One device's outcome of a stop. */
export interface DeviceStopOut {
  state: "stopped" | "unchanged" | "failed";
  detail: string;
}

/**
 * `POST <root>/api/rig/stop`: what stopping did. Needs `OPERATE`; never rate-limited. `interim`
 * is true until package A8's real `Stopper` replaces the placeholder that only interrupts the
 * program and puts controllers in manual (the signals work lands the rest). The route answers
 * 501 `{"detail": ...}` until A8 lands -- callers must not treat that as success.
 */
export interface StopReport {
  at_ns: Nanoseconds;
  actor: StopActor;
  reason: string;
  devices: Record<Address, DeviceStopOut>;
  program_interrupted: boolean;
  controllers_manual: Address[];
  interim: boolean;
}

// Passkey wire types: for Phase 3 (passkeys in the Go front). Nothing serves /api/auth/passkey/* in
// Phase 1; the shape is kept for the Go front to answer.

/** A registered passkey, as `GET /api/auth/passkey` and registration report it. */
export interface PasskeyOut {
  id: number;
  label: string;
  created_ns: number;
  transports: string[];
}

/** `GET /api/auth/passkey`: this runner's credentials, plus whether they outlive a restart. */
export interface PasskeyListOut {
  store_backed: boolean;
  passkeys: PasskeyOut[];
}

/**
 * Where a runner serves against where it was asked to (`GET /api/auth`, `GET /api/health`). An open runner
 * (no password, no token) asked for an address beyond loopback is served on 127.0.0.1 instead (`restricted`);
 * with `--insecure-open` it is served where asked, and `open_network` says anyone who reaches it may operate.
 */
export interface Exposure {
  requested: string;
  host: string;
  port: number;
  open: boolean;
  restricted: boolean;
  open_network: boolean;
  /** What the runner said about it on stderr, if anything. */
  warning: string | null;
}

/** A duration the runner reports, as ns or as configured (`1h`, `30d`, `15m`), in seconds; null for none/0. */
export function durationS(value: number | string | null | undefined): number | null {
  if (value == null || value === 0 || value === "0" || value === "") return null;
  if (typeof value === "number") return value / 1e9;
  const m = /^\s*([\d.]+)\s*(ns|us|ms|s|m|h|d)?\s*$/i.exec(value);
  if (!m) return null;
  const n = Number(m[1]);
  const unit = (m[2] ?? "s").toLowerCase();
  return n * ({ ns: 1e-9, us: 1e-6, ms: 1e-3, s: 1, m: 60, h: 3600, d: 86400 }[unit] ?? 1);
}

/** A size the runner reports, as bytes or as configured (`256MB`, `20GB`), in bytes; null for none/0. */
export function sizeBytes(value: number | string | null | undefined): number | null {
  if (value == null || value === 0 || value === "0" || value === "") return null;
  if (typeof value === "number") return value;
  const m = /^\s*([\d.]+)\s*([kmgt]?i?b?)\s*$/i.exec(value);
  if (!m) return null;
  const n = Number(m[1]);
  const unit = (m[2] ?? "").toLowerCase().replace("i", "").replace("b", "");
  return n * ({ "": 1, k: 1e3, m: 1e6, g: 1e9, t: 1e12 }[unit] ?? 1);
}

/** `GET /api/rig/versions/{id}`: a version with the document it held. */
export interface RigVersionDetail extends RigVersion {
  document: RigDocument;
}

/** `POST /api/rig/save`: where the rig was written, and what. */
export interface SaveResult {
  path: string;
  document: RigDocument;
}

// endregion

// region Streams

/**
 * Messages on the websockets, by stream. Every socket sends what the rig
 * knows on connect, then every 50 ms one frame of whatever changed (the
 * newest value per key, so at most one sample per node per frame). `/ws/writes`
 * and `/ws/devices` are gone: a demand's write record rides with its reading
 * in `SampleOut.writes`, and a device's run rides beside the samples, under
 * `runs`, on `/ws/samples` -- either key present only when it changed.
 */
export interface Streams {
  samples: { samples?: SampleOut[]; runs?: DeviceRunOut[] };
  controllers: { controllers: ControllerOut[] };
  activities: { activities: ActivityOut[] };
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
  /**
   * `"scratch"`: the rolling record the runner keeps while nothing is being
   * recorded (the last `keep` of the rig's clock, trimmed continuously),
   * from which a range can be kept as a session of its own. Absent or
   * `"session"` for a recording proper.
   */
  kind?: "session" | "scratch";
  /** Never aged out by the runner's retention. */
  pinned?: boolean;
  /** The session this one continued when the runner rotated at a boundary. */
  continues?: number | null;
  /** What the scratch record holds on disk, when the runner says. */
  bytes?: number | null;
}

/** Whether a session row is the runner's rolling scratch record rather than a recording. */
export const isScratch = (s: Pick<SessionRow, "kind">): boolean => s.kind === "scratch";

/** A range of a scratch record to keep as a session of its own. */
export interface KeepRange {
  start_ns: Nanoseconds;
  end_ns: Nanoseconds;
  details?: unknown;
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
  range: Bounds | null;
  precision: number | null;
  warning: Bounds | null;
  alarm: Bounds | null;
  limits: Bounds | null;
}

/** A writable signal whose write states the session recorded. */
export interface WriteRow {
  signal: SignalRow;
  driver: string | null;
  limits: Bounds | null;
}

/** A controller is named by the demand it drives; `measured` is the signal it regulates. */
export interface ControllerRow {
  name: Address;
  measured: Address;
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
  /** The controller's name: the address of the demand it drives. */
  controller: Address;
  offset_ns: Nanoseconds;
  mode: string;
  /** The law's share of the output, in the output's unit; null when it was not a number (a NaN integral). */
  correction: number | null;
  measured: number | null;
  /** The setpoint resolved at this tick, in the measured unit (a ramp's value, not its name). */
  setpoint: number | null;
  output: number | null;
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
  /** Backfill the new session with this much of the scratch record, in the rig's clock, so what was just watched is kept. */
  include_ns?: Nanoseconds;
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
  /** Time moves only when advanced (`POST /api/sim/clock/advance`). */
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
