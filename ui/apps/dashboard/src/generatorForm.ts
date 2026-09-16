/**
 * The set-point generator union (`GET /api/controllers/schema` → `generators`)
 * as the faceplate's target control offers it: a list of kinds to choose from,
 * and each kind's config schema reshaped so `SchemaForm` renders it as a
 * usable form -- nothing here knows any generator by name; every rewrite is by
 * the shape pydantic emits for the core's `Speed` and `Duration` types.
 *
 * What is rewritten, and why:
 * - a `Speed` (a `{value, per}` object or a `{per_minute: n}` shorthand) becomes
 *   one object with a single `per_minute` number in `<unit>/min`, which is a
 *   valid wire form as it stands;
 * - a `Duration` (unit keys that add, or bare seconds) becomes one object with a
 *   single `minutes` number, also a wire form; one that may be null keeps its
 *   field and simply goes unfilled (`prune` then drops the empty object);
 * - a `Speed | Duration` union (a ramp's `pace`) keeps the two-way choice, its
 *   branches titled "at a rate" / "over a time";
 * - a number field a generator takes in the process unit gets `unit`;
 * - each config is titled by its tag, humanised, as the kind picker and a
 *   profile's segment picker show it.
 */

import { deref, humanise, type JsonSchema } from "@flyball/client";
import { impliedUiSchema, simplifyNullables, type SchemaFormProps } from "@flyball/react";

type UiSchema = NonNullable<SchemaFormProps["uiSchema"]>;

/** One generator the rig registers: its tag, a label, and its config schema (`$ref` into `$defs`). */
export interface GeneratorChoice {
  tag: string;
  label: string;
  /** "Start <label>", with a trailing "setpoint" dropped: every generator generates one, so the word says nothing. */
  verb: string;
}

const hasProperty = (s: JsonSchema | undefined, key: string) => Boolean(s?.properties && key in s.properties);

/** Every generator tag in the union, in the order the schema lists them, with the label its title gives. */
export function generatorChoices(schema: JsonSchema | undefined): GeneratorChoice[] {
  if (!schema) return [];
  return (schema.oneOf ?? schema.anyOf ?? []).flatMap((branch) => {
    const def = deref(branch, schema);
    const tag = (def.properties?.tag as JsonSchema | undefined)?.const;
    if (typeof tag !== "string") return [];
    const label = humanise(tag);
    return [{ tag, label, verb: `Start ${label.toLowerCase().replace(/\s+setpoint$/, "")}` }];
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

const speedSchema = (unit: string, title: string): JsonSchema => ({
  type: "object",
  title,
  properties: { per_minute: { type: "number", exclusiveMinimum: 0, title: "rate", unit: `${unit}/min` } },
  required: ["per_minute"],
  additionalProperties: false,
});

const durationSchema = (title: string): JsonSchema => ({
  type: "object",
  title,
  properties: { minutes: { type: "number", minimum: 0, title: "minutes", unit: "min" } },
  required: ["minutes"],
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
  const tag = (node.properties?.tag as JsonSchema | undefined)?.const;
  if (typeof tag === "string") out.title = humanise(tag);
  if (node.properties) {
    out.properties = Object.fromEntries(
      Object.entries(node.properties).map(([key, value]) => {
        const shaped = reshape(value, root, unit);
        // A generator's own number fields (a ramp's end, a hold's value) are in the process unit.
        const inUnit = typeof tag === "string" && shaped.type === "number" && !shaped.unit;
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
 * Undefined for a tag the union lacks.
 */
export function generatorFormSchema(schema: JsonSchema, tag: string, unit: string): JsonSchema | undefined {
  const branch = (schema.oneOf ?? schema.anyOf ?? []).find((b) => (deref(b, schema).properties?.tag as JsonSchema | undefined)?.const === tag);
  if (!branch) return undefined;
  const shaped = reshape({ ...deref(branch, schema), $defs: schema.$defs ?? {} }, schema, unit);
  return shaped;
}

/**
 * The form's `uiSchema`: the `tag` hidden (the kind picker chose it), and a
 * short union field (a ramp's rate-or-time `pace`) picked with segmented
 * buttons rather than a select -- the choice reads at a glance, and MUI's
 * select warns on the console when RJSF remounts the branch beneath it. Built
 * over what the schema implies, so the branches' own widgets stay.
 */
export function generatorUiSchema(formSchema: JsonSchema): UiSchema {
  const simplified = simplifyNullables(formSchema);
  const implied = impliedUiSchema(simplified, simplified);
  const ui: UiSchema = { ...implied, tag: { "ui:widget": "hidden" } };
  for (const [key, field] of Object.entries(simplified.properties ?? {})) {
    const branches = field.anyOf ?? field.oneOf;
    if (branches && branches.length >= 2 && branches.length <= 4 && key !== "tag") {
      ui[key] = { ...(implied[key] as UiSchema | undefined), "ui:widget": "segmented" };
    }
  }
  return ui;
}

/**
 * The form's data as the wire takes it: blanks (`undefined`) dropped, and an
 * object left wholly blank (an unfilled optional duration) dropped with them,
 * so a `Duration | None` field goes as absent rather than as `{}`.
 */
export function prune<T>(data: T): T {
  if (Array.isArray(data)) return data.map(prune).filter((v) => v !== undefined) as T;
  if (data && typeof data === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(data as Record<string, unknown>)) {
      const kept = prune(value);
      if (kept !== undefined) out[key] = kept;
    }
    return (Object.keys(out).length ? out : undefined) as T;
  }
  return data;
}
