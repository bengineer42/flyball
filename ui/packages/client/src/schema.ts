/**
 * Helpers for walking the JSON Schema the server emits. Small on purpose:
 * form rendering is the form library's job; these answer the questions a
 * panel asks before it hands a schema over.
 */

import type { JsonSchema } from "./wire.js";

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
export function digitsFor(schema?: JsonSchema): number {
  if (typeof schema?.precision === "number") return schema.precision;
  if (schema?.multipleOf) return Math.max(0, -Math.floor(Math.log10(schema.multipleOf)));
  return 2;
}

/**
 * A number as text with a stable width: fixed decimals, so a value under
 * noise does not change length from one reading to the next.
 */
export function formatNumber(value: number, schema?: JsonSchema): string {
  return value.toFixed(digitsFor(schema));
}

/** Render a value the way its schema says: fixed decimals, unit as suffix. */
export function formatValue(value: unknown, schema?: JsonSchema): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    const text = formatNumber(value, schema);
    return schema?.unit ? `${text} ${schema.unit}` : text;
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

/** A loop's current setpoint: the reference when it is a number, else recovered from demand − correction. */
export function setpointOf(loop: { reference: number | string | null; demand: number | null; correction: number | null }): number | null {
  if (typeof loop.reference === "number") return loop.reference;
  if (loop.demand != null && loop.correction != null) return loop.demand - loop.correction;
  return null;
}

/** `set_flows` → `Set flows`, `wetFraction` → `Wet fraction`. For headings; the tag stays the identifier. */
export function humanise(tag: string): string {
  const words = tag.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[_-]+/g, " ").trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export type AlarmLevel = "ok" | "warn" | "alarm";

/**
 * Where `value` sits against a channel's bands: outside `alarm` is "alarm",
 * outside `warn` is "warn", else "ok". A channel with no bands, or no value,
 * is always "ok".
 */
export function alarmLevel(
  value: number | null | undefined,
  channel: { warn?: [number, number] | null; alarm?: [number, number] | null },
): AlarmLevel {
  if (value === null || value === undefined || Number.isNaN(value)) return "ok";
  const outside = (band: [number, number] | null | undefined) =>
    !!band && (value < Math.min(band[0], band[1]) || value > Math.max(band[0], band[1]));
  if (outside(channel.alarm)) return "alarm";
  if (outside(channel.warn)) return "warn";
  return "ok";
}
