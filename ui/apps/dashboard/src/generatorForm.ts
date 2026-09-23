/**
 * The set-point generator union (`GET /api/controllers/schema` → `generators`)
 * as the faceplate's target control offers it: a list of kinds to choose from,
 * and each kind's config schema reshaped so `SchemaForm` renders it as a
 * usable form -- nothing here knows any generator by name; every rewrite is by
 * the shape pydantic emits for the core's `Speed` and `Duration` types.
 *
 * What is rewritten, and why:
 * - a `Speed` (a `{value, per}` object or a `{per_minute: n}` shorthand) becomes
 *   one object with a `value` number in the process unit and a `per` unit
 *   picker (per second/minute/hour); a `Duration` (unit keys that add, or bare
 *   seconds) becomes one object with a `value` number and a `unit` picker
 *   (seconds/minutes/hours/days) -- the engine accepts any one of those unit
 *   keys (`RATE_KEYS`/`DURATION_KEYS` in the server's `dialect.py`), the old
 *   hardcoded `per_minute`/`minutes` was only ever this form's own limit, and
 *   `prune` packs the picked unit and value into that single wire key
 *   (`{hours: 1.5}`, say) once the form submits; one that may be null keeps
 *   its field and simply goes unfilled (a blank `value` prunes the object
 *   away entirely);
 * - a `Speed | Duration` union (a ramp's `pace`) keeps the two-way choice, its
 *   branches titled "at a rate" / "over a time";
 * - a number field a generator takes in the process unit gets `unit`;
 * - each config is titled by its type, humanised, as the kind picker and a
 *   profile's segment picker show it.
 */

import { deref, humanise, type JsonSchema } from "@flyball/client";
import { impliedUiSchema, simplifyNullables, type SchemaFormProps } from "@flyball/react";

type UiSchema = NonNullable<SchemaFormProps["uiSchema"]>;

/** One generator the rig registers: its type, a label, and its config schema (`$ref` into `$defs`). */
export interface GeneratorChoice {
  type: string;
  label: string;
  /** "Start <label>", with a trailing "setpoint" dropped: every generator generates one, so the word says nothing. */
  verb: string;
}

const hasProperty = (s: JsonSchema | undefined, key: string) => Boolean(s?.properties && key in s.properties);

/** Every generator type in the union, in the order the schema lists them, with the label its title gives. */
export function generatorChoices(schema: JsonSchema | undefined): GeneratorChoice[] {
  if (!schema) return [];
  return (schema.oneOf ?? schema.anyOf ?? []).flatMap((branch) => {
    const def = deref(branch, schema);
    const type = (def.properties?.type as JsonSchema | undefined)?.const;
    if (typeof type !== "string") return [];
    const label = humanise(type);
    return [{ type, label, verb: `Start ${label.toLowerCase().replace(/\s+setpoint$/, "")}` }];
  });
}

/** A `Speed`: the shorthand branch with `per_minute` among its keys, anywhere in an `anyOf`. */
function isSpeed(node: JsonSchema, root: JsonSchema): boolean {
  return (node.anyOf ?? []).some((b) => hasProperty(deref(b, root), "per_minute"));
}

/** A `Duration`: a branch with `minutes` among its keys, anywhere in an `anyOf`. */
function isDuration(node: JsonSchema, root: JsonSchema): boolean {
  return (node.anyOf ?? []).some((b) => hasProperty(deref(b, root), "minutes"));
}

/** The `per` a `Speed` may wire as (`RATE_KEYS` in the server's `dialect.py`, the practical subset). */
const RATE_UNITS: Array<{ const: string; title: string }> = [
  { const: "per_second", title: "/s" },
  { const: "per_minute", title: "/min" },
  { const: "per_hour", title: "/h" },
];

/** The `unit` a `Duration` may wire as (`DURATION_KEYS` in the server's `dialect.py`, the practical subset). */
const DURATION_UNITS: Array<{ const: string; title: string }> = [
  { const: "seconds", title: "s" },
  { const: "minutes", title: "min" },
  { const: "hours", title: "h" },
  { const: "days", title: "d" },
];

const RATE_UNIT_KEYS = new Set(RATE_UNITS.map((u) => u.const));
const DURATION_UNIT_KEYS = new Set(DURATION_UNITS.map((u) => u.const));

const speedSchema = (unit: string, title: string): JsonSchema => ({
  type: "object",
  title,
  properties: {
    value: { type: "number", exclusiveMinimum: 0, title: "rate", unit },
    per: { type: "string", title: "per", default: "per_minute", oneOf: RATE_UNITS },
  },
  required: ["value", "per"],
  additionalProperties: false,
});

const durationSchema = (title: string): JsonSchema => ({
  type: "object",
  title,
  properties: {
    value: { type: "number", minimum: 0, title: "duration" },
    unit: { type: "string", title: "unit", default: "minutes", oneOf: DURATION_UNITS },
  },
  required: ["value", "unit"],
  additionalProperties: false,
});

/** The rewrite of one node (see the module doc); recurses into what it does not rewrite. */
function reshape(node: JsonSchema, root: JsonSchema, unit: string): JsonSchema {
  const branches = node.anyOf;
  if (branches) {
    const nonNull = branches.filter((b) => b.type !== "null");
    // `Duration | None`: the field stays, unfilled means none.
    if (nonNull.length === 1 && nonNull.length < branches.length && isDuration(nonNull[0]!, root)) {
      const { anyOf: _drop, default: _default, ...rest } = node;
      return { ...durationSchema(typeof rest.title === "string" ? rest.title : "duration"), description: rest.description };
    }
    // `Speed | Duration`: a rate or a time, the operator's choice.
    if (branches.length === 2 && isSpeed(branches[0]!, root) && isDuration(branches[1]!, root)) {
      return { ...node, anyOf: [speedSchema(unit, "at a rate"), durationSchema("over a time")] };
    }
    if (isSpeed(node, root)) return speedSchema(unit, typeof node.title === "string" ? node.title : "rate");
    if (isDuration(node, root)) return durationSchema(typeof node.title === "string" ? node.title : "duration");
  }
  const out: JsonSchema = { ...node };
  const type = (node.properties?.type as JsonSchema | undefined)?.const;
  if (typeof type === "string") out.title = humanise(type);
  if (node.properties) {
    out.properties = Object.fromEntries(
      Object.entries(node.properties).map(([key, value]) => {
        const shaped = reshape(value, root, unit);
        // A generator's own number fields (a ramp's end, a hold's value) are in the process unit.
        const inUnit = typeof type === "string" && shaped.type === "number" && !shaped.unit;
        return [key, inUnit ? { ...shaped, unit } : shaped];
      }),
    );
  }
  if (node.$defs) out.$defs = Object.fromEntries(Object.entries(node.$defs).map(([k, v]) => [k, reshape(v, root, unit)]));
  if (node.items) out.items = Array.isArray(node.items) ? node.items.map((i) => reshape(i, root, unit)) : reshape(node.items, root, unit);
  if (branches) out.anyOf = branches.map((b) => reshape(b, root, unit));
  if (node.oneOf) out.oneOf = node.oneOf.map((b) => reshape(b, root, unit));
  return out;
}

/**
 * The form schema for one generator: its config (deref'd out of the union)
 * with the whole union's `$defs` beside it, so a profile's segments still
 * resolve, every `Speed`/`Duration` reshaped, and `unit` on its numbers.
 * Undefined for a type the union lacks.
 */
export function generatorFormSchema(schema: JsonSchema, type: string, unit: string): JsonSchema | undefined {
  const branch = (schema.oneOf ?? schema.anyOf ?? []).find((b) => (deref(b, schema).properties?.type as JsonSchema | undefined)?.const === type);
  if (!branch) return undefined;
  const shaped = reshape({ ...deref(branch, schema), $defs: schema.$defs ?? {} }, schema, unit);
  return shaped;
}

/**
 * The form's `uiSchema`: the `type` hidden (the kind picker chose it), and a
 * short union field (a ramp's rate-or-time `pace`) picked with segmented
 * buttons rather than a select -- the choice reads at a glance, and MUI's
 * select warns on the console when RJSF remounts the branch beneath it. Built
 * over what the schema implies, so the branches' own widgets stay.
 */
export function generatorUiSchema(formSchema: JsonSchema): UiSchema {
  const simplified = simplifyNullables(formSchema);
  const implied = impliedUiSchema(simplified, simplified);
  const ui: UiSchema = { ...implied, type: { "ui:widget": "hidden" } };
  for (const [key, field] of Object.entries(simplified.properties ?? {})) {
    const branches = field.anyOf ?? field.oneOf;
    if (branches && branches.length >= 2 && branches.length <= 4 && key !== "type") {
      ui[key] = { ...(implied[key] as UiSchema | undefined), "ui:widget": "segmented" };
    }
  }
  return ui;
}

/**
 * A `speedSchema`/`durationSchema` object -- exactly `{value, per}` or
 * `{value, unit}`, its picker among `RATE_UNITS`/`DURATION_UNITS` -- as the
 * wire single-key form that unit names (`{hours: 1.5}`, say), or `null` with
 * no value typed, matching a blank field going unfilled rather than as
 * `{unit: n}`. `undefined` when `data` is not one of these objects at all,
 * so the caller falls back to pruning it generically.
 */
function packQuantity(data: Record<string, unknown>): Record<string, unknown> | null | undefined {
  const keys = new Set(Object.keys(data));
  const wireKey = typeof data.unit === "string" && DURATION_UNIT_KEYS.has(data.unit) && keys.size <= 2 && keys.has("unit") ? data.unit : typeof data.per === "string" && RATE_UNIT_KEYS.has(data.per) && keys.size <= 2 && keys.has("per") ? data.per : undefined;
  if (!wireKey) return undefined;
  return typeof data.value === "number" && Number.isFinite(data.value) ? { [wireKey]: data.value } : null;
}

/**
 * The form's data as the wire takes it: a `speedSchema`/`durationSchema`
 * object packed down to its single unit key (`packQuantity`), blanks
 * (`undefined`) dropped, and an object left wholly blank (an unfilled
 * optional duration) dropped with them, so a `Duration | None` field goes as
 * absent rather than as `{}`.
 */
export function prune<T>(data: T): T {
  if (Array.isArray(data)) return data.map(prune).filter((v) => v !== undefined) as T;
  if (data && typeof data === "object") {
    const record = data as Record<string, unknown>;
    const packed = packQuantity(record);
    if (packed !== undefined) return (packed ?? undefined) as T;
    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(record)) {
      const kept = prune(value);
      if (kept !== undefined) out[key] = kept;
    }
    return (Object.keys(out).length ? out : undefined) as T;
  }
  return data;
}
