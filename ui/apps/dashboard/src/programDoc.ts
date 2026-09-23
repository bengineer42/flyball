/**
 * The program document tree as the builder sees it: which commands the file
 * dialect (`GET /api/programs/schema`) offers, each command's argument
 * schema shaped for a form, a step's arguments in and out of that form, and
 * the step an error message points at. No rendering, no fetching.
 */
import { deref, describeUnit, humanise, isEmpty, RigError, unwrapNullable, type CommandSchema, type JsonSchema, type SignalSchema } from "@flyball/client";
import type { Tree } from "./programText.js";

/** One step of a program file: `{ramp: {...}}`, plus any modifier keys the dialect allows beside it. */
export type Step = Record<string, unknown>;

export interface ProgramTree extends Tree {
  name?: unknown;
  description?: unknown;
  steps: Step[];
}

export interface CommandInfo {
  tag: string;
  title: string;
  /** The command's docstring; `short` is its first paragraph on one line. */
  description?: string;
  short?: string;
  /** The argument object: the dialect's request schema without `command`, flat time keys included. */
  args: JsonSchema;
  /** The field a bare scalar / list under the command key stands for (`prompt: "msg"` → `message`). */
  primary?: string;
  /** The composite time field (`pace`, `timeout`, `duration`) the flat time keys fold into, when the command has one. */
  time?: TimeField;
}

/** One way of saying the composite: a rate (`per_minute`) or a span (`minutes`); the keys are the alternative's, in schema order. */
export interface TimeGroup {
  keys: string[];
}

/**
 * A composite time field as one control: `pace` is `Speed | Duration`, so a
 * program gives exactly one of its keys; `timeout` and `duration` one span key.
 */
export interface TimeField {
  name: string;
  title: string;
  description?: string;
  groups: TimeGroup[];
  /** Every key the field owns: the composite itself and its flat spellings. */
  keys: string[];
}

const TIME_KEYS = ["nanoseconds", "microseconds", "milliseconds", "seconds", "minutes", "hours", "days"];
const RATE_KEYS = TIME_KEYS.map((k) => `per_${k.replace(/s$/, "")}`);
const FLAT_KEYS = new Set([...TIME_KEYS, ...RATE_KEYS]);
/** The flat keys worth a field of their own before anything is typed; the rest appear when the value has them. */
const COMMON_FLAT = new Set(["seconds", "minutes", "hours", "per_second", "per_minute", "per_hour"]);
const UNIT_OF_PER: Record<string, string> = { nanosecond: "per_nanosecond", microsecond: "per_microsecond", millisecond: "per_millisecond", second: "per_second", minute: "per_minute", hour: "per_hour", day: "per_day" };

const isObject = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object" && !Array.isArray(v);

/** Is `key` one of the rarely used units, kept out of the way in a pick? */
export const isRareUnit = (key: string) => !COMMON_FLAT.has(key);

/** Every property name reachable through the alternatives of `schema`. */
function leafKeys(schema: JsonSchema, root: JsonSchema, depth = 0): string[] {
  if (depth > 6) return [];
  const s = deref(schema, root);
  return [...Object.keys(s.properties ?? {}), ...[...(s.anyOf ?? []), ...(s.oneOf ?? [])].flatMap((b) => leafKeys(b, root, depth + 1))];
}

/**
 * The composite time field of an argument object, from its schema: the one
 * non-flat property whose alternatives are spelled with the object's flat
 * time keys; each top-level alternative is a group the file gives one key of.
 */
function timeFieldOf(args: JsonSchema, root: JsonSchema): TimeField | undefined {
  const properties = args.properties ?? {};
  const flat = Object.keys(properties).filter((k) => FLAT_KEYS.has(k));
  if (flat.length === 0) return undefined;
  for (const [name, raw] of Object.entries(properties)) {
    if (FLAT_KEYS.has(name)) continue;
    if (name === "timeout") continue; // never folded flat: a step's timeout always nests as its own Duration
    const { inner } = unwrapNullable(deref(raw, root));
    const alternatives = inner.anyOf ?? inner.oneOf ?? [inner];
    const groups = alternatives
      .map((alt) => ({ keys: flat.filter((k) => leafKeys(alt, root).includes(k)) }))
      .filter((g) => g.keys.length > 0);
    if (groups.length === 0) continue;
    return { name, title: inner.title ?? raw.title ?? humanise(name), description: raw.description ?? inner.description, groups, keys: [name, ...flat] };
  }
  return undefined;
}

/** The step branches of the file schema, each resolved. */
function stepBranches(programSchema: JsonSchema): JsonSchema[] {
  const items = programSchema.properties?.steps?.items;
  const step = Array.isArray(items) ? items[0] : items;
  return [...(step?.oneOf ?? []), ...(step?.anyOf ?? [])].map((b) => deref(b, programSchema));
}

/** Does the schema, anywhere in its alternatives, describe an object with `key`? */
function mentions(schema: JsonSchema, key: string, root: JsonSchema, depth = 0): boolean {
  if (depth > 6) return false;
  const s = deref(schema, root);
  if (s.properties && key in s.properties) return true;
  return [...(s.anyOf ?? []), ...(s.oneOf ?? [])].some((b) => mentions(b, key, root, depth + 1));
}

/** The commands the dialect offers, in the schema's order. */
export function commandsOf(programSchema: JsonSchema | undefined): CommandInfo[] {
  if (!programSchema) return [];
  const out: CommandInfo[] = [];
  for (const branch of stepBranches(programSchema)) {
    const tag = branch.required?.[0] ?? Object.keys(branch.properties ?? {})[0];
    const value = tag ? branch.properties?.[tag] : undefined;
    if (!tag || !value) continue;
    const alternatives = (value.anyOf ?? value.oneOf ?? [value]).map((a) => deref(a, programSchema));
    const args = alternatives.find((a) => a.properties) ?? { type: "object", properties: {} };
    const shorthand = alternatives.find((a) => a !== args);
    const byTitle = shorthand?.title?.toLowerCase();
    const primary = byTitle && args.properties && byTitle in args.properties ? byTitle : args.required?.[0];
    const time = timeFieldOf(args, programSchema);
    const description = branch.description ?? value.description;
    const short = description?.split(/\n\s*\n/)[0]?.replace(/\s+/g, " ");
    const title = args.title && !/Request$|Config$/.test(args.title) ? args.title : humanise(tag);
    out.push({ tag, title, description, short, args, primary, time });
  }
  return out;
}

/** The modifier keys a step may carry beside its command (none in a dialect without modifiers). */
export function modifiersOf(programSchema: JsonSchema | undefined): Record<string, JsonSchema> {
  const branch = programSchema ? stepBranches(programSchema)[0] : undefined;
  const tag = branch?.required?.[0];
  return Object.fromEntries(Object.entries(branch?.properties ?? {}).filter(([k]) => k !== tag));
}

/** Which command a step names, and what else it carries. */
export function splitStep(step: Step, commands: CommandInfo[]): { tag: string | undefined; value: unknown; modifiers: Record<string, unknown> } {
  const tags = new Set(commands.map((c) => c.tag));
  const tag = Object.keys(step).find((k) => tags.has(k)) ?? (commands.length === 0 ? Object.keys(step)[0] : undefined);
  const modifiers = Object.fromEntries(Object.entries(step).filter(([k]) => k !== tag));
  return { tag, value: tag === undefined ? undefined : step[tag], modifiers };
}

/** A step's value as a plain argument object: the shorthand expanded to its field. */
export function argsOf(value: unknown, command: CommandInfo | undefined): Record<string, unknown> {
  if (isObject(value)) return { ...value };
  if (value === null || value === undefined) return {};
  return command?.primary ? { [command.primary]: value } : {};
}

/**
 * A step's arguments as the form holds them: the shorthand expanded, the
 * composite time field and its flat keys left out (they are one control of
 * their own, see `timeEntries`), and `loop` (the controllers named) always a list.
 */
export function toForm(value: unknown, command: CommandInfo | undefined): Record<string, unknown> {
  const args = argsOf(value, command);
  for (const k of command?.time?.keys ?? []) delete args[k];
  if ("loop" in args) {
    const loop = args.loop;
    if (typeof loop === "string") args.loop = [loop];
    else if (loop === null || loop === undefined) delete args.loop;
  }
  return args;
}

/**
 * What the arguments say for the composite time field, as `[key, number]`
 * pairs in the flat spelling: `{minutes: 30}` → `[["minutes", 30]]`; the
 * composite `timeout: {minutes: 30}`, `timeout: 90` (seconds) and
 * `pace: {value: 5, per: "minute"}` are read the same way. More than one
 * pair means the file gave several, which the runner refuses.
 */
export function timeEntries(args: Record<string, unknown>, field: TimeField): Array<[string, unknown]> {
  const out: Array<[string, unknown]> = [];
  for (const k of field.keys) {
    if (!(k in args)) continue;
    const v = args[k];
    if (k !== field.name) out.push([k, v]);
    else if (typeof v === "number") out.push(["seconds", v]);
    else if (isObject(v) && typeof v.value === "number" && typeof v.per === "string" && UNIT_OF_PER[v.per]) out.push([UNIT_OF_PER[v.per]!, v.value]);
    else if (isObject(v)) for (const [ik, iv] of Object.entries(v)) out.push([ik, iv]);
    else out.push([k, v]);
  }
  return out;
}

/** `args` with the composite time field said one way only: `key: value`, every other spelling cleared; no key at all when `value` is undefined. */
export function withTime(args: Record<string, unknown>, field: TimeField, key: string | null, value: unknown): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  let placed = false;
  for (const [k, v] of Object.entries(args)) {
    if (!field.keys.includes(k)) {
      out[k] = v;
      continue;
    }
    if (!placed && key !== null && value !== undefined) {
      out[key] = value; // where the first time key sat, so the file's order holds
      placed = true;
    }
  }
  if (!placed && key !== null && value !== undefined) out[key] = value;
  return out;
}

/**
 * C9: a timed `wait` with a `message` must write its duration nested
 * (`duration: {minutes: 20}`), never folded flat beside `message` -- the
 * loader refuses `wait: {minutes: 20, message: "soak"}`. A `wait` with no
 * message still folds flat as usual; every other command is untouched.
 * Applied to the full step arguments after any edit (`args` already has the
 * composite field's keys, flat or nested, alongside `message`); several flat
 * keys at once (a file's conflict, shown by the time control) are left for
 * that to resolve first.
 */
export function enforceWaitTimeSpelling(tag: string | undefined, args: Record<string, unknown>, field: TimeField | undefined): Record<string, unknown> {
  if (tag !== "wait" || !field) return args;
  if (typeof args.message !== "string" || args.message === "") return args;
  if (field.name in args) return args; // already nested
  const flatKeys = field.keys.filter((k) => k !== field.name && k in args);
  if (flatKeys.length !== 1) return args;
  const key = flatKeys[0]!;
  const { [key]: value, ...rest } = args;
  return { ...rest, [field.name]: { [key]: value } };
}

/**
 * The form's data back to the tree's spelling: empty fields left out, an empty
 * loop list meaning the default loop, and a switch at its default left out too
 * (the switch shows the default, so the form fills it in; the file need not say it).
 */
export function fromForm(data: Record<string, unknown>, command?: CommandInfo): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(data)) {
    if (v === undefined) continue;
    if (k === "loop" && Array.isArray(v) && v.length === 0) continue;
    if (typeof v === "string" && v === "") continue;
    if (typeof v === "boolean" && command?.args.properties?.[k]?.default === v) continue;
    out[k] = v;
  }
  return out;
}

/** Deep equality that ignores key order (the form may hand keys back in schema order). */
export function sameValue(a: unknown, b: unknown): boolean {
  const canon = (v: unknown): unknown => (Array.isArray(v) ? v.map(canon) : isObject(v) ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, canon(v[k])])) : v);
  return JSON.stringify(canon(a)) === JSON.stringify(canon(b));
}

/** `args` with its keys in the order `original` had them, new keys after: an edit keeps the file's spelling. */
export function inOrder(args: Record<string, unknown>, original: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(original)) if (k in args) out[k] = args[k];
  for (const [k, v] of Object.entries(args)) if (!(k in out)) out[k] = v;
  return out;
}

/** A fresh step for `command`: the tree spelling with nothing filled in. */
export function newStep(command: CommandInfo): Step {
  return { [command.tag]: {} };
}

/** The JSON types `schema` admits, through `$ref`s and alternatives. */
function typesOf(schema: JsonSchema, root: JsonSchema, depth = 0): Set<string> {
  const out = new Set<string>();
  if (depth > 6) return out;
  const s = deref(schema, root);
  for (const t of Array.isArray(s.type) ? s.type : s.type ? [s.type] : []) out.add(t);
  if (s.properties && !s.type) out.add("object");
  if (s.items && !s.type) out.add("array");
  if (s.enum) for (const v of s.enum) out.add(jsonType(v));
  if ("const" in s) out.add(jsonType(s.const));
  for (const b of [...(s.anyOf ?? []), ...(s.oneOf ?? [])]) for (const t of typesOf(b, root, depth + 1)) out.add(t);
  return out;
}

const jsonType = (v: unknown): string => (v === null ? "null" : Array.isArray(v) ? "array" : typeof v === "number" ? (Number.isInteger(v) ? "integer" : "number") : typeof v);

/** Does `value` have a type the field's schema admits? An integer fits a number field; nothing else crosses. */
export function valueFits(value: unknown, schema: JsonSchema, root: JsonSchema): boolean {
  const types = typesOf(schema, root);
  const t = jsonType(value);
  return types.has(t) || (t === "integer" && types.has("number"));
}

/** A step re-targeted at another command: only arguments the new command has under the same name, with a type it accepts, are kept. */
export function retarget(step: Step, from: CommandInfo | undefined, to: CommandInfo, commands: CommandInfo[], root: JsonSchema): Step {
  const { value, modifiers } = splitStep(step, commands);
  const args = argsOf(value, from);
  const keep = Object.fromEntries(Object.entries(args).filter(([k, v]) => to.args.properties?.[k] && valueFits(v, to.args.properties[k]!, root)));
  return { [to.tag]: keep, ...modifiers };
}

/**
 * Strip `default` everywhere, so the form only holds what the user typed; the
 * default goes into the placeholder instead. A boolean keeps its default: a
 * switch has no blank state, so it shows the effective value (`fromForm` drops it again).
 */
function withoutDefaults(schema: JsonSchema): JsonSchema {
  const { default: d, ...rest } = schema;
  const out: JsonSchema = typeof d === "boolean" && schema.type === "boolean" ? { ...rest, default: d } : rest;
  if (schema.properties) out.properties = Object.fromEntries(Object.entries(schema.properties).map(([k, v]) => [k, withoutDefaults(v)]));
  if (schema.anyOf) out.anyOf = schema.anyOf.map(withoutDefaults);
  if (schema.oneOf) out.oneOf = schema.oneOf.map(withoutDefaults);
  if (schema.items && !Array.isArray(schema.items)) out.items = withoutDefaults(schema.items);
  if (schema.$defs) out.$defs = Object.fromEntries(Object.entries(schema.$defs).map(([k, v]) => [k, withoutDefaults(v)]));
  return out;
}

export interface FormShape {
  schema: JsonSchema;
  uiSchema: Record<string, unknown>;
}

/**
 * What the rig has for a `command` or `set` step's picks (`GET /api/schema`):
 * each device's commands and its writable signals by path relative to the
 * device, for the `device` / `device_command` / `args` / `values` fields.
 */
export interface DevicePicks {
  /** Every device on the rig, so a saved name can be told apart from one the rig lacks. */
  names: string[];
  /** Every command a device has, simulation ones included: those are not offered, but a saved one is still known. */
  commands: Record<string, Record<string, CommandSchema>>;
  /** Only demand signals (role `"demand"`), in tree order; a device with none takes no `set`. */
  demands: Record<string, Record<string, SignalSchema>>;
}

const signalTitle = (path: string, signal: SignalSchema) => signal.label || humanise(path.split(".").pop() ?? path);

/** The device command a `command` step names, when `devices` knows it. */
function deviceCommandOf(args: Record<string, unknown>, devices: DevicePicks | undefined): CommandSchema | undefined {
  const device = typeof args.device === "string" ? devices?.commands[args.device] : undefined;
  return typeof args.device_command === "string" ? device?.[args.device_command] : undefined;
}

/** The commands of `device` the builder offers: its own, not the simulation-only ones (those live on the Simulation page). */
function offeredCommands(devices: DevicePicks, device: unknown): string[] {
  const all = typeof device === "string" ? devices.commands[device] : undefined;
  return Object.entries(all ?? {})
    .filter(([, c]) => !c.simulation)
    .map(([name]) => name);
}

const NOT_ON_RIG = "not on this rig";

/**
 * What the step's check warning says about `name`: the fragment of the
 * runner's `; `-joined message that quotes it, else the bare mark. The
 * runner reports exactly what the rig lacks, so its words go on the field.
 */
export function warningFor(warning: string | undefined, name: string): string {
  const hit = warning?.split("; ").find((part) => part.includes(`'${name}'`));
  return hit ? `${NOT_ON_RIG}: ${hit}` : NOT_ON_RIG;
}

/**
 * A string field as a pick: the rig's `offered` names, and the saved value
 * whether or not the rig has it, so the form never shows a different value
 * from the file. A saved value the rig lacks is titled with the mark; one
 * that is there but not offered (a simulation command) with `aside`. No
 * options at all leaves the field free text.
 */
function pickField(field: JsonSchema, offered: string[], saved: unknown, has: (name: string) => boolean, aside: string): JsonSchema {
  const options = offered.map((name) => ({ const: name, title: name }));
  if (typeof saved === "string" && saved !== "" && !offered.includes(saved)) options.unshift({ const: saved, title: `${saved} (${has(saved) ? aside : NOT_ON_RIG})` });
  if (options.length === 0) return field;
  return options.every((o) => o.const === o.title) ? { ...field, enum: options.map((o) => o.const) } : { ...field, oneOf: options };
}

/** A saved value the rig has no field for: the same type it has, with `description` as its mark when given. */
function savedField(name: string, value: unknown, description?: string): JsonSchema {
  const type = typeof value === "number" ? "number" : typeof value === "boolean" ? "boolean" : typeof value === "string" ? "string" : Array.isArray(value) ? "array" : value === null ? "null" : "object";
  return { type, title: humanise(name), ...(description ? { description } : {}) };
}

/** A writable signal as a number field: its label, unit (none for a dimensionless `1`) and limits, the address and access as the hint. */
/** A demand as a field for its own value: its own value schema (dtype-correct), titled, with its unit and limits. */
function valueField(path: string, signal: SignalSchema): JsonSchema {
  const [low, high] = signal.limits ?? [undefined, undefined];
  const unit = describeUnit(signal.unit);
  return {
    ...signal.value,
    title: signalTitle(path, signal),
    ...(unit ? { unit } : {}),
    description: `${signal.address} [${signal.access.toUpperCase()}]${signal.limits ? ` · limits ${signal.limits[0]} – ${signal.limits[1]}${unit ? ` ${unit}` : ""}` : ""}`,
    ...(low !== undefined ? { minimum: low } : {}),
    ...(high !== undefined ? { maximum: high } : {}),
  };
}

/**
 * The argument schema shaped for the form: `loop` as a pick from the rig's
 * controllers, a `command` step's `device` and `device_command` as picks
 * from the rig's devices and its `args` as that command's own arguments
 * (from `current`, what the step says now, with a linked (`x-signal`)
 * argument optional), a `set` step's `device` as a pick from the devices
 * with a demand and its `values` as one field per demand of that device --
 * the composite time field and its flat keys left to their own control,
 * defaults as placeholders.
 *
 * What `current` says is always on the form, whether or not the rig has it:
 * a device, command or signal the rig lacks is a pick option or a field of
 * its own, marked with the step's check `warning`, so the form and the text
 * never disagree and a save never loses what the file had. Simulation-only
 * commands are not offered (they belong to the Simulation page) but a saved
 * one is shown.
 * Self-contained: `$defs` are copied in so `$ref`s still resolve.
 */
export function formShape(command: CommandInfo, root: JsonSchema, controllers: string[] | undefined, devices?: DevicePicks, current: Record<string, unknown> = {}, warning?: string): FormShape {
  const properties: Record<string, JsonSchema> = {};
  const ui: Record<string, unknown> = {};
  const deviceCommand = command.tag === "command" ? deviceCommandOf(current, devices) : undefined;
  const has = (name: string) => Boolean(devices?.names.includes(name));
  let defs = root.$defs;
  for (const [name, raw] of Object.entries(command.args.properties ?? {})) {
    if (command.time?.keys.includes(name)) continue; // one control of its own
    let field = withoutDefaults(deref(raw, root));
    if (name === "wait" && field.type === "boolean" && !field.description) field = { ...field, description: `wait for the ${command.title.toLowerCase()} to finish before the next step` };
    if (command.tag === "command") {
      if (name === "device" && devices) {
        const names = Object.keys(devices.commands).filter((d) => offeredCommands(devices, d).length > 0);
        field = pickField(field, names, current.device, has, "only simulation commands");
        ui[name] = { "ui:widget": "select" }; // a dropdown, not a segmented button, even with few options -- it belongs at the top with `device_command`, not buried mid-form
      } else if (name === "device_command" && devices) {
        const known = (name: string) => typeof current.device === "string" && name in (devices.commands[current.device] ?? {});
        field = pickField(field, offeredCommands(devices, current.device), current.device_command, known, "simulation");
      } else if (name === "args") {
        if (deviceCommand && !isEmpty(deviceCommand.arguments)) {
          const { $defs, ...args } = withoutDefaults(deviceCommand.arguments);
          field = { ...args, title: field.title ?? "Arguments", ...(deviceCommand.description ? { description: deviceCommand.description.split(/\n\s*\n/)[0]?.replace(/\s+/g, " ") } : {}) };
          if ($defs) defs = { ...defs, ...$defs };
        } else if (isObject(current.args) && Object.keys(current.args).length > 0) {
          // the rig cannot say what the arguments are (not known yet, or no such device or command): the saved ones, as they are
          const saved = current.args;
          field = { type: "object", title: field.title ?? "Arguments", description: devices && typeof current.device_command === "string" ? warningFor(warning, current.device_command) : undefined, properties: Object.fromEntries(Object.keys(saved).map((k) => [k, savedField(k, saved[k])])) };
        } else continue; // nothing to fill until the command is known, and nothing when it takes no arguments
      }
    }
    if (command.tag === "set" && devices) {
      if (name === "device") {
        field = pickField(field, Object.keys(devices.demands), current.device, has, "no demands");
        ui[name] = { "ui:widget": "select" };
      } else if (name === "values") {
        const signals = (typeof current.device === "string" && devices.demands[current.device]) || {};
        const shown = Object.keys(signals);
        // one field per demand shown, none required: the step sets the ones given; a saved signal the device lacks keeps its row
        const saved = isObject(current.values) ? current.values : {};
        const fields = Object.fromEntries([...shown.map((path) => [path, valueField(path, signals[path]!)]), ...Object.entries(saved).filter(([path]) => !(path in signals)).map(([path, v]) => [path, savedField(path, v, warningFor(warning, path))])]);
        if (Object.keys(fields).length > 0) field = { type: "object", title: field.title ?? "Values", properties: fields };
      }
    }
    if (name === "loop") {
      field = {
        type: "array",
        title: "Controllers",
        description: field.description ?? (controllers && controllers.length > 0 ? undefined : "Controller names (the address each drives); none means the rig's default controller."),
        items: controllers && controllers.length > 0 ? { type: "string", enum: controllers } : { type: "string" },
        uniqueItems: true,
      };
      if (controllers && controllers.length > 0) ui[name] = { "ui:widget": "checkboxes", "ui:options": { inline: true } };
    } else {
      const d = raw.default;
      if (d !== undefined && d !== null && typeof d !== "object") ui[name] = { "ui:placeholder": String(d) };
    }
    properties[name] = field;
  }
  const schema: JsonSchema = { type: "object", title: command.title, properties, ...(command.args.required ? { required: command.args.required.filter((r) => r in properties) } : {}), ...(defs ? { $defs: withoutDefaults({ $defs: defs }).$defs } : {}) };
  // `device`/`device_command` first -- everything else (`args`, `values`, ...) picks up
  // after them, in whatever order they were declared.
  const front = ["device", "device_command"].filter((k) => k in properties);
  if (front.length > 0) ui["ui:order"] = [...front, "*"];
  return { schema, uiSchema: ui };
}

/**
 * A `command` or `set` step's arguments with what no longer applies dropped:
 * a new device clears the command, its args and the values; a new command
 * its args.
 */
export function onDeviceChange(args: Record<string, unknown>, before: Record<string, unknown>): { args: Record<string, unknown>; changed: boolean } {
  if (args.device !== before.device) {
    const { device_command: _c, args: _a, values: _v, ...rest } = args;
    void _c;
    void _a;
    void _v;
    return { args: rest, changed: true };
  }
  if (args.device_command !== before.device_command) {
    const { args: _a, ...rest } = args;
    void _a;
    return { args: rest, changed: true };
  }
  return { args, changed: false };
}

/** The tree with `steps` guaranteed a list; null when `value` is not a program-shaped mapping at all. */
export function asProgram(value: unknown): ProgramTree | null {
  if (!isObject(value)) return null;
  const steps = value.steps;
  if (steps !== undefined && steps !== null && !Array.isArray(steps)) return null;
  return { ...value, steps: (steps ?? []).map((s: unknown) => (isObject(s) ? s : { "": s })) } as ProgramTree;
}

/** "step 3: ..." from the check's 422 detail → the step's index (the runner counts from zero) and the rest. */
export function stepOfError(e: unknown): { index: number | null; message: string } {
  const detail = e instanceof RigError ? e.detail : e instanceof Error ? e.message : String(e);
  const m = /^step (\d+): ?(.*)$/s.exec(detail);
  return m ? { index: Number(m[1]), message: m[2] ?? detail } : { index: null, message: detail };
}

/** The first line of a pydantic validation error, for a chip: "ramp.to: Input should be a valid number". */
export function briefError(message: string): string {
  const lines = message.split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.length <= 1) return message;
  // "N validation errors for ...", then pairs of "field" / "  explanation [type=...]"
  const pairs: string[] = [];
  for (let i = 1; i + 1 < lines.length; i += 1) {
    const field = lines[i]!;
    const why = lines[i + 1]!;
    if (/^\S+$/.test(field) && !/^\[/.test(why) && !/^For further/.test(why)) {
      pairs.push(`${field}: ${why.replace(/\s*\[type=.*$/, "")}`);
      i += 1;
      if (lines[i + 1]?.startsWith("For further")) i += 1;
    }
  }
  return pairs.length ? pairs.join("; ") : lines[0]!;
}
