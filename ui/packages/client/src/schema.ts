/**
 * Helpers for walking the JSON Schema the server emits. Small on purpose:
 * form rendering is the form library's job; these answer the questions a
 * panel asks before it hands a schema over.
 */

import type { Access, Address, ControllerOut, Dtype, FeedforwardConfig, JsonSchema, NamespaceOut, SignalOut, TreeNode } from "./wire.js";
import { isNamespace } from "./wire.js";

/** Whether a dtype's values are numbers a gauge, chart or slider can draw: `float`/`int` only. */
export function isNumeric(dtype: Dtype): boolean {
  return dtype === "float" || dtype === "int";
}

/**
 * The address a command argument's field is linked to (`x-signal`, put there
 * by the server on an argument that is a value for a demand or setting), so
 * a form can prefill it from the store and show the live readback beside
 * it. Undefined on a plain argument.
 */
export function linkedSignal(field: JsonSchema): Address | undefined {
  const address = field["x-signal"];
  return typeof address === "string" ? address : undefined;
}

/** Follow a local `$ref` (`#/$defs/Name`) against `root`. Returns the input when it is not a ref. */
export function deref(schema: JsonSchema, root: JsonSchema): JsonSchema {
  if (!schema.$ref) return schema;
  const path = schema.$ref.replace(/^#\//, "").split("/");
  let node: unknown = root;
  for (const segment of path) {
    node = (node as Record<string, unknown> | undefined)?.[segment];
  }
  return (node as JsonSchema | undefined) ?? schema;
}

/** The `X | null` pattern pydantic emits for optional fields: `anyOf: [X, {type: "null"}]`. */
export function unwrapNullable(schema: JsonSchema): { inner: JsonSchema; nullable: boolean } {
  const branches = schema.anyOf;
  if (branches && branches.length === 2) {
    const nonNull = branches.filter((b) => b.type !== "null");
    if (nonNull.length === 1 && nonNull[0]) {
      return { inner: { ...nonNull[0], title: schema.title ?? nonNull[0].title, default: schema.default }, nullable: true };
    }
  }
  return { inner: schema, nullable: false };
}

/** Field name → its schema, `$ref`s and nullables resolved, so a renderer sees plain fields. */
export function fields(schema: JsonSchema, root: JsonSchema = schema): Array<[string, JsonSchema, boolean]> {
  const resolved = deref(schema, root);
  return Object.entries(resolved.properties ?? {}).map(([name, field]) => {
    const { inner, nullable } = unwrapNullable(deref(field, root));
    return [name, deref(inner, root), nullable];
  });
}

/** True when a command takes no arguments, so a button is the whole form. */
export function isEmpty(schema: JsonSchema): boolean {
  return Object.keys(schema.properties ?? {}).length === 0;
}

/** Decimal places for a numeric field: `precision`, else from `multipleOf`, else 2. */
export function digitsFor(schema?: ValueMeta): number {
  if (typeof schema?.precision === "number") return schema.precision;
  if (schema?.multipleOf) return Math.max(0, -Math.floor(Math.log10(schema.multipleOf)));
  return 2;
}

/**
 * A number as text with a stable width: fixed decimals, so a value under
 * noise does not change length from one reading to the next.
 */
export function formatNumber(value: number, schema?: ValueMeta): string {
  return fixed(value, digitsFor(schema));
}

/** `toFixed` that never prints `-0.0`: a value that rounds to nothing is nothing. */
export function fixed(value: number, digits: number): string {
  const text = value.toFixed(digits);
  return /^-0(\.0*)?$/.test(text) ? text.slice(1) : text;
}

/**
 * Decimals for a run of axis ticks: the signal's own, but never so few that
 * two ticks read the same (`0.3, 0.3` on a near-flat trace says nothing).
 */
export function tickDigits(ticks: readonly number[], precision: number): number {
  const finite = ticks.filter((v) => Number.isFinite(v));
  const step = finite.length > 1 ? Math.min(...finite.slice(1).map((v, i) => Math.abs(v - finite[i]!))) : 0;
  const needed = step > 0 ? Math.min(6, Math.max(0, Math.ceil(-Math.log10(step) - 1e-9))) : 0;
  return Math.max(precision, needed);
}

/** What `formatValue` needs of a schema field or a signal: enough either shape gives it. */
export type ValueMeta = { unit?: string | null; precision?: number | null; multipleOf?: number; dtype?: Dtype };

/**
 * Render a value by dtype where one is known, else by shape: `bool` (or a
 * plain boolean) as on/off, a number at fixed decimals with its unit as a
 * suffix, a `[low, high]` pair as a range, any other object or array as
 * JSON, else the value as text. Takes a command argument's `JsonSchema`
 * field (unit, precision, multipleOf) or a `SignalOut`/`SignalSchema`
 * (unit, precision, dtype) -- both shapes give it what it needs.
 */
export function formatValue(value: unknown, meta?: ValueMeta): string {
  if (value === null || value === undefined) return "—";
  if (meta?.dtype === "bool" || typeof value === "boolean") return value ? "on" : "off";
  if (typeof value === "number") {
    const text = formatNumber(value, meta);
    return meta?.unit ? `${text} ${meta.unit}` : text;
  }
  if (Array.isArray(value) && value.length === 2 && typeof value[0] === "number" && typeof value[1] === "number") {
    const text = `${formatNumber(value[0], meta)} – ${formatNumber(value[1], meta)}`;
    return meta?.unit ? `${text} ${meta.unit}` : text;
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/**
 * The `live` path a config field declares (`Field(json_schema_extra={"live": "state.duty"})`):
 * where the value the field stands for is now. Absent on a parameter.
 */
export function liveOf(field?: JsonSchema): string | undefined {
  return typeof field?.live === "string" && field.live ? field.live : undefined;
}

/**
 * What a `live` path points at in `root`: dot-separated keys walked from the
 * root (`state.duty`, `stats.noise`); a `*` segment fans out over every key
 * at that level and yields an object keyed by them (`outputs.*`,
 * `readings.*.value`). `undefined` when a key is missing; a fan-out drops
 * the keys the rest of the path misses.
 */
export function resolveLive(path: string, root: unknown): unknown {
  return walk(path ? path.split(".") : [], root);
}

function walk(segments: string[], node: unknown): unknown {
  if (segments.length === 0) return node;
  if (!node || typeof node !== "object" || Array.isArray(node)) return undefined;
  const [head, ...rest] = segments as [string, ...string[]];
  const record = node as Record<string, unknown>;
  if (head === "*") {
    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(record)) {
      const found = walk(rest, value);
      if (found !== undefined && found !== null) out[key] = found;
    }
    return out;
  }
  return walk(rest, record[head]);
}

/** `feedforward(setpoint)`: the demand, in the target's unit, the feedforward asks for at `setpoint`. Null for a tag this client does not know. */
export function feedforwardAt(feedforward: FeedforwardConfig | null | undefined, setpoint: number): number | null {
  if (!feedforward) return setpoint;
  switch (feedforward.tag) {
    case "setpoint":
      return setpoint;
    case "none":
      return 0;
    case "affine": {
      const { gain, bias } = feedforward as { gain: number; bias?: number };
      return gain * setpoint + (bias ?? 0);
    }
    case "table": {
      const points = [...((feedforward as { points: Array<[number, number]> }).points ?? [])].sort((a, b) => a[0] - b[0]);
      if (!points.length) return null;
      if (setpoint <= points[0]![0]) return points[0]![1];
      if (setpoint >= points[points.length - 1]![0]) return points[points.length - 1]![1];
      for (let i = 1; i < points.length; i++) {
        const [x0, y0] = points[i - 1]!;
        const [x1, y1] = points[i]!;
        if (setpoint <= x1) return x1 === x0 ? y0 : y0 + ((y1 - y0) * (setpoint - x0)) / (x1 - x0);
      }
      return null;
    }
    default:
      return null;
  }
}

/**
 * The setpoint a feedforward maps to `base` (`= demand − correction`), or
 * null where it cannot be told: `none` maps everything to 0, a flat table
 * segment or a value beyond its ends is ambiguous, a zero gain likewise.
 */
export function invertFeedforward(feedforward: FeedforwardConfig | null | undefined, base: number): number | null {
  if (!feedforward) return base;
  switch (feedforward.tag) {
    case "setpoint":
      return base;
    case "none":
      return null;
    case "affine": {
      const { gain, bias } = feedforward as { gain: number; bias?: number };
      return gain ? (base - (bias ?? 0)) / gain : null;
    }
    case "table": {
      const points = [...((feedforward as { points: Array<[number, number]> }).points ?? [])].sort((a, b) => a[0] - b[0]);
      for (let i = 1; i < points.length; i++) {
        const [x0, y0] = points[i - 1]!;
        const [x1, y1] = points[i]!;
        const lo = Math.min(y0, y1);
        const hi = Math.max(y0, y1);
        if (base < lo || base > hi) continue;
        if (y1 === y0) return base === y0 && x0 === x1 ? x0 : null; // a flat segment: any setpoint along it
        return x0 + ((x1 - x0) * (base - y0)) / (y1 - y0);
      }
      return null;
    }
    default:
      return null;
  }
}

/**
 * A controller's current setpoint, in the source's unit: `setpoint` as the
 * server resolved it at the last tick (a ramp's current value); else the
 * reference when it is a number; else recovered by inverting the
 * feedforward on `demand − correction`, which is exact for `setpoint` and
 * `affine` and for a monotone `table`, and null for `none`.
 */
export function setpointOf(controller: Pick<ControllerOut, "reference" | "demand" | "correction"> & { setpoint?: number | null; feedforward?: FeedforwardConfig | null }): number | null {
  if (typeof controller.setpoint === "number") return controller.setpoint;
  if (typeof controller.reference === "number") return controller.reference;
  if (controller.demand != null && controller.correction != null) return invertFeedforward(controller.feedforward, controller.demand - controller.correction);
  return null;
}

/** `set_flows` → `Set flows`, `wetFraction` → `Wet fraction`. For headings; the tag stays the identifier. */
export function humanise(tag: string): string {
  const words = tag.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[_-]+/g, " ").trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Every event `kind` the backend emits, worded for a log reader. Enumerated
 * from the `rig.event(...)` call sites (`runtime/{writer,polling,rig}.py`,
 * `programmer/programmer.py`, `server/routes/library.py`) -- not guessed.
 */
const EVENT_KINDS: Record<string, string> = {
  started: "Started",
  step: "Step",
  step_timed_out: "Step timed out",
  step_failed: "Step failed",
  failed: "Failed",
  finished: "Finished",
  interrupted: "Interrupted",
  run_from_library: "Run from library",
  restarted: "Restarted",
  offline: "Went offline",
  slow: "Running slow",
  delivery_failed: "Delivery failed",
  write_recovered: "Writes recovered",
  write_failed: "Write failed",
  recording_failed: "Recording failed",
};

/** An event's `kind` (`step_timed_out`, `run_from_library`) as a phrase for a person. Unknown kinds fall through to `humanise`. */
export function describeEventKind(kind: string): string {
  return EVENT_KINDS[kind] ?? humanise(kind);
}

/**
 * A program-step subject as the programmer names it: `name[step]`, `step`
 * 0-based (`programmer.py`'s `self._step`). `anneal[2]` → `anneal · step 3`.
 * Any other subject (a controller, a device name) is returned unchanged.
 */
export function describeSubject(subject: string): string {
  const m = /^(.+)\[(\d+)\]$/.exec(subject);
  if (!m) return subject;
  return `${m[1]} · step ${Number(m[2]) + 1}`;
}

/** A law or feedforward state/gain key, labelled with a hover hint. See `control/laws.py`. */
const STATE_KEYS: Record<string, { label: string; hint: string }> = {
  last_raw: { label: "last raw output", hint: "the law's output before clamping" },
  last_elapsed: { label: "elapsed", hint: "seconds since the law last ran, used to integrate the error" },
  integral: { label: "integral term", hint: "the accumulated error the integral gain multiplies" },
  kp: { label: "proportional gain", hint: "output per unit of error" },
  ki: { label: "integral gain", hint: "output per unit of accumulated error, over a second" },
  kd: { label: "derivative gain", hint: "output per unit of the error's rate of change" },
  tt: { label: "tracking time", hint: "how fast the integral unwinds once the actuator clamps" },
};

/** A law/feedforward state or gain key (`last_raw`, `kp`, `tt`) as a label and a hover hint. Unknown keys fall through to `humanise`, with no hint. */
export function describeStateKey(key: string): { label: string; hint?: string } {
  return STATE_KEYS[key] ?? { label: humanise(key) };
}

/**
 * A device, link or plant tag as words: the Python class name the server
 * puts in `DeviceOut.type` / `DeviceSchema.type` (`SimDaq`), or a rig
 * file's `driver:` / link `kind:` tag (`sim_daq`, `sim_furnace`). From the
 * device classes under `flyball/{sim,devices,integrations}` and the tags
 * registered there; unknown tags fall through to `humanise`.
 */
const DEVICE_TAGS: Record<string, string> = {
  SimDaq: "Simulated DAQ",
  SimDrive: "Simulated drive",
  Modbus: "Modbus device",
  Scpi: "SCPI instrument",
  QCoDeS: "QCoDeS instrument",
  PyMeasure: "PyMeasure instrument",
  sim_daq: "Simulated DAQ",
  sim_drive: "Simulated drive",
  sim_furnace: "Simulated furnace",
  sim_plant: "Simulated plant",
  modbus: "Modbus device",
  modbus_tcp: "Modbus TCP",
  modbus_rtu: "Modbus RTU",
  scpi: "SCPI instrument",
  serial: "Serial port",
  visa: "VISA resource",
  qcodes: "QCoDeS instrument",
  pymeasure: "PyMeasure instrument",
  fake_registers: "Fake registers",
  fake_text: "Fake text link",
};

export function describeDevice(tag: string): string {
  return DEVICE_TAGS[tag] ?? humanise(tag);
}

/** A signal's display name: its `label`, or its `name` humanised when the driver gave none. */
export function describeSignal(signal: Pick<SignalOut, "name" | "label">): string {
  return signal.label || humanise(signal.name);
}

/** A namespace's display name: its `label`, or its `name` humanised when the driver gave none. */
export function describeNamespace(namespace: Pick<NamespaceOut, "name" | "label">): string {
  return namespace.label || humanise(namespace.name);
}

/** A device's display name: its `label`, or its `name` humanised when the rig gave none (`DeviceOut.label` is null then). */
export function deviceTitle(device: DeviceRef): string {
  return device.label || humanise(device.name);
}

/**
 * A unit symbol as shown beside a value or on an axis. The dimensionless
 * unit `1` (`flyball.core.units.si.One`, a blender's efforts) is never
 * printed: a bare `1` after a number reads as a digit. Words the driver
 * chose for a dimensionless unit (`of full`) are kept.
 */
export function describeUnit(unit: string | null | undefined): string {
  return !unit || unit === "1" ? "" : unit;
}

/** `text` then its unit, separated by a space only when there is a unit to show. */
export function withUnit(text: string, unit: string | null | undefined): string {
  const shown = describeUnit(unit);
  return shown ? `${text} ${shown}` : text;
}

/** What names a device for a title: its name and, when the rig gave one, its label; the tree when the caller has it. */
export interface DeviceRef {
  name: string;
  label?: string | null;
  signals?: readonly TreeNode[];
}

/** Where a signal sits: its device and, when it is inside one, the innermost namespace. Either is absent when the caller does not know it. */
export interface Place {
  device?: DeviceRef;
  namespace?: Pick<NamespaceOut, "name" | "address" | "label">;
}

/**
 * A signal's place in `devices`: its device and the innermost namespace
 * holding it, found by walking the device's tree. The device alone when the
 * address is at the device's root; nothing when no device here is named by
 * the address.
 */
export function placeOf(address: Address, devices: ReadonlyArray<DeviceRef>): Place {
  const device = devices.find((d) => d.name === deviceOf(address));
  if (!device) return {};
  let namespace: NamespaceOut | undefined;
  const walk = (nodes: readonly TreeNode[]) => {
    for (const node of nodes) {
      if (!isNamespace(node)) continue;
      if (address.startsWith(`${node.address}.`)) {
        namespace = node;
        walk(node.signals);
        return;
      }
    }
  };
  walk(device.signals ?? []);
  return namespace ? { device, namespace } : { device };
}

/** `Humidity` → `humidity` for use after a qualifier; an acronym-led label (`RH`) is left alone. */
const lowerFirst = (text: string) => (text.length > 1 && text[1] === text[1]!.toUpperCase() && text[1] !== text[1]!.toLowerCase() ? text : text.charAt(0).toLowerCase() + text.slice(1));

/**
 * A signal's title where it stands beside others: inside a namespace, the
 * namespace then the signal (`Chamber humidity`); at a device's root, the
 * signal then the device (`Expected humidity · Pump blender`); alone, the
 * signal's own name. Addresses never appear -- they belong in a hover hint.
 */
export function titleFor(signal: Pick<SignalOut, "name" | "label">, place: Place = {}): string {
  const own = describeSignal(signal);
  if (place.namespace) return `${describeNamespace(place.namespace)} ${lowerFirst(own)}`;
  if (place.device) return `${own} · ${deviceTitle(place.device)}`;
  return own;
}

/**
 * A signal named where its tree is not in view -- a picker, a legend, a
 * tile. Its own label when the driver gave one that no other signal of the
 * device shares (`Dry pump flow`); otherwise its namespace then its name
 * (`Dry humidity`), so three `humidity` signals in three namespaces read
 * apart. The device is the caller's to add when several are shown.
 */
export function signalTitle(signal: Pick<SignalOut, "name" | "label" | "address">, devices: ReadonlyArray<DeviceRef>): string {
  return signalTitleAt(signal, placeOf(signal.address, devices));
}

/** `signalTitle` for a caller that already knows the signal's place. */
export function signalTitleAt(signal: Pick<SignalOut, "name" | "label" | "address">, place: Place): string {
  const own = describeSignal(signal);
  if (!place.namespace) return own;
  const shared = signalsOf(place.device?.signals ?? []).some((other) => other.address !== signal.address && describeSignal(other) === own);
  return signal.label && !shared ? own : titleFor(signal, { namespace: place.namespace });
}

/** A tile's caption under a title from `signalTitleAt`: the device alone once the title already names the namespace. */
export function captionUnder(title: string, signal: Pick<SignalOut, "name" | "label">, place: Place): string {
  return title === describeSignal(signal) ? captionFor(place) : place.device ? deviceTitle(place.device) : "";
}

/** Every tag axis across `signals` with its values in first-seen order: `line → [dry, wet]`. Empty when nothing is tagged. */
export function tagAxes(signals: ReadonlyArray<Pick<SignalOut, "tags">>): Map<string, string[]> {
  const axes = new Map<string, string[]>();
  for (const s of signals)
    for (const [axis, value] of Object.entries(s.tags ?? {})) {
      const values = axes.get(axis) ?? [];
      if (!values.includes(value)) values.push(value);
      axes.set(axis, values);
    }
  return axes;
}

/** Whether a signal carries, on every axis something is chosen for, one of the chosen values. Nothing chosen: every signal. */
export function hasTags(signal: Pick<SignalOut, "tags">, chosen: ReadonlyMap<string, ReadonlySet<string>>): boolean {
  for (const [axis, values] of chosen) {
    if (values.size === 0) continue;
    const own = signal.tags?.[axis];
    if (own === undefined || !values.has(own)) return false;
  }
  return true;
}

/** A tile's caption for a signal: its namespace then its device (`Chamber · Humidity sensors`), whichever are known. */
export function captionFor(place: Place): string {
  return [place.namespace && describeNamespace(place.namespace), place.device && deviceTitle(place.device)].filter(Boolean).join(" · ");
}

/** The hover hint behind a caption: the namespace's address, else the device's name. */
export function placeAddress(place: Place): string | undefined {
  return place.namespace?.address ?? place.device?.name;
}

/** The name of the group a signal belongs to on a chart: its namespace's label, else its device's. */
export function groupTitle(place: Place): string | undefined {
  return place.namespace ? describeNamespace(place.namespace) : place.device ? deviceTitle(place.device) : undefined;
}

/**
 * The heading of a chart that holds every signal of one unit: the unit, or
 * -- for a dimensionless unit, which shows as nothing -- the quantities
 * the signals measure (`Effort`).
 */
export function unitTitle(unit: string, signals: ReadonlyArray<Pick<SignalOut, "quantity">>): string {
  const shown = describeUnit(unit);
  if (shown) return shown;
  const quantities = [...new Set(signals.map((s) => s.quantity).filter(Boolean))].map(humanise);
  return quantities.join(", ") || "dimensionless";
}

/** A controller's display name: its target's `label`, or its `name` (the target's address). */
export function describeController(controller: Pick<ControllerOut, "name" | "label">): string {
  return controller.label || controller.name;
}

const ACCESS_WORDS: Record<string, string> = { r: "read", p: "publish", w: "write" };

/** An access set (`"rp"`, `"w"`) as words: `read, publish`. */
export function describeAccess(access: Access): string {
  return [...access.toLowerCase()].map((letter) => ACCESS_WORDS[letter] ?? letter).join(", ");
}

/** Whether a signal publishes (streams, is recorded, may be a readout or a controller's source). */
export function publishes(signal: Pick<SignalOut, "access">): boolean {
  return signal.access.toLowerCase().includes("p");
}

/** Whether a signal takes demands (may be a controller's target). */
export function writable(signal: Pick<SignalOut, "access">): boolean {
  return signal.access.toLowerCase().includes("w");
}

/** Whether a signal can be read at all (`r`); a `w`-only signal has no value to ask for. */
export function readable(signal: Pick<SignalOut, "access">): boolean {
  return signal.access.toLowerCase().includes("r");
}

/**
 * A device's own housekeeping output: the `conditions` list every device
 * declares at its root. Shown as the device's badge, never as a reading of
 * its own -- a picker, a tile grid or a "last sample" stamp skips it.
 */
export function isHousekeeping(signal: Pick<SignalOut, "name" | "address">): boolean {
  const device = deviceOf(signal.address);
  return signal.address === `${device}.conditions` || signal.address.startsWith(`${device}.last.`);
}

/** Every signal under a tree, namespaces flattened, in tree order. */
export function signalsOf(tree: readonly TreeNode[]): SignalOut[] {
  const out: SignalOut[] = [];
  for (const node of tree) {
    if (isNamespace(node)) out.push(...signalsOf(node.signals));
    else out.push(node);
  }
  return out;
}

/** The device an address is under: its first segment. */
export function deviceOf(address: Address): string {
  const dot = address.indexOf(".");
  return dot < 0 ? address : address.slice(0, dot);
}

/** A signal's address from its node's and its name relative to the node (`hum_sensors.dry` + `humidity`). */
export function addressOf(node: Address, name: string): Address {
  return `${node}.${name}`;
}

/** Unit derived from a simulated-plant config key's suffix, longest first so `_w_per_k`/`_j_per_k` win over a bare `_k`. */
const PARAM_UNIT_SUFFIXES: Array<[string, string]> = [
  ["_w_per_k", "W/K"],
  ["_j_per_k", "J/K"],
  ["_m2", "m²"],
  ["_c", "°C"],
  ["_s", "s"],
];

/**
 * A simulated plant's raw config key (`coupling_w_per_k`, `sensor_lag_s`) as
 * a label with the unit its suffix names, so the value can be shown beside
 * it instead of folded into the key. The raw key is kept as the hint. A key
 * with no recognised suffix falls through to `humanise`, with no unit.
 */
export function describeSimParam(key: string): { label: string; unit?: string; hint: string } {
  for (const [suffix, unit] of PARAM_UNIT_SUFFIXES) {
    if (key.endsWith(suffix)) return { label: humanise(key.slice(0, -suffix.length)), unit, hint: key };
  }
  return { label: humanise(key), hint: key };
}

export type AlarmLevel = "ok" | "warn" | "alarm" | "stale";

/** How long a signal may go without a sample before it reads "stale" — DESIGN-SPEC.md §2: `max(3 × period_s, 5s)`. */
export function staleAfterS(periodS: number | null | undefined): number {
  return Math.max(3 * (periodS ?? 0), 5);
}

/**
 * Freshness for one signal, in RIG time (a simulated rig's clock runs
 * faster than the wall clock, so `nowS` must come from `/api/clock` or the
 * newest sample across the rig, never `Date.now()`).
 */
export interface Freshness {
  /** The poll period of the signal's device (`DeviceOut.run.period_s`); unknown treated as 0 (only the 5s floor applies). */
  periodS?: number | null;
  /** The last sample's time, in rig seconds; null/undefined skips the stale check. */
  lastSampleS?: number | null;
  /** The rig's current time, in rig seconds; null/undefined skips the stale check. */
  nowS?: number | null;
}

/**
 * The freshest of a set of traces' last points, in seconds — a live proxy
 * for the rig's current time when nothing is polling `/api/clock`
 * continuously (DESIGN-SPEC.md §2: "the newest sample time across the
 * rig"). At least one signal elsewhere on the rig must still be sampling
 * for this to track real time; a rig gone completely silent freezes it,
 * same as every signal on it going stale together.
 */
export function latestSampleS(traces: Iterable<{ t: number[] }>): number | null {
  let latest: number | null = null;
  for (const trace of traces) {
    const last = trace.t.length ? trace.t[trace.t.length - 1] : undefined;
    if (last !== undefined && (latest === null || last > latest)) latest = last;
  }
  return latest;
}

/**
 * Where `value` sits against a signal's bands: outside `alarm` is "alarm",
 * outside `warn` is "warn", else "ok" — unless `fresh` says no sample has
 * arrived recently enough, in which case the level is "stale" regardless of
 * the last value (a stuck reading is not a healthy one). A signal with no
 * bands, or no value, is "ok" unless stale.
 */
export function alarmLevel(
  value: number | null | undefined,
  signal: { warn?: [number, number] | null; alarm?: [number, number] | null },
  fresh?: Freshness | null,
): AlarmLevel {
  if (fresh && fresh.lastSampleS != null && fresh.nowS != null && fresh.nowS - fresh.lastSampleS > staleAfterS(fresh.periodS)) return "stale";
  if (value === null || value === undefined || Number.isNaN(value)) return "ok";
  const outside = (band: [number, number] | null | undefined) =>
    !!band && (value < Math.min(band[0], band[1]) || value > Math.max(band[0], band[1]));
  if (outside(signal.alarm)) return "alarm";
  if (outside(signal.warn)) return "warn";
  return "ok";
}
