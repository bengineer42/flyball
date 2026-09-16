/**
 * A client-side check of an imported document against the schema the
 * server publishes (`GET /api/dashboards/schema`). Only the keywords that
 * schema uses are understood -- `type`, `properties`, `required`,
 * `additionalProperties`, `items`, `$ref`, `anyOf`, `minimum`, `maximum`,
 * `minLength` -- which is enough to name the problem before a PUT would.
 */
import { deref, type JsonSchema } from "@flyball/client";

const typeOf = (v: unknown): string => (v === null ? "null" : Array.isArray(v) ? "array" : Number.isInteger(v) ? "integer" : typeof v);

const accepts = (declared: string | string[] | undefined, v: unknown) => {
  if (!declared) return true;
  const types = Array.isArray(declared) ? declared : [declared];
  const t = typeOf(v);
  return types.some((d) => d === t || (d === "number" && t === "integer"));
};

/** The problems, as `path: message` lines; empty when the value fits. */
export function validateAgainst(schema: JsonSchema, value: unknown, root: JsonSchema = schema, path = "$"): string[] {
  const s = deref(schema, root);
  if (s.anyOf) {
    const branches = s.anyOf.map((b) => validateAgainst(b, value, root, path));
    return branches.some((errs) => errs.length === 0) ? [] : [`${path}: none of the allowed forms fit (${branches.map((e) => e[0]).join("; ")})`];
  }
  const errors: string[] = [];
  if (!accepts(s.type, value)) return [`${path}: expected ${Array.isArray(s.type) ? s.type.join(" or ") : s.type}, got ${typeOf(value)}`];
  if (typeof value === "number") {
    if (s.minimum !== undefined && value < s.minimum) errors.push(`${path}: ${value} is below the minimum ${s.minimum}`);
    if (s.maximum !== undefined && value > s.maximum) errors.push(`${path}: ${value} is above the maximum ${s.maximum}`);
  }
  if (typeof value === "string" && typeof s.minLength === "number" && value.length < s.minLength) errors.push(`${path}: must not be empty`);
  if (Array.isArray(value) && s.items && !Array.isArray(s.items)) {
    const item = s.items;
    value.forEach((v, i) => errors.push(...validateAgainst(item, v, root, `${path}[${i}]`)));
  }
  if (value && typeof value === "object" && !Array.isArray(value) && s.properties) {
    const record = value as Record<string, unknown>;
    for (const key of s.required ?? []) if (!(key in record)) errors.push(`${path}.${key}: required`);
    for (const [key, v] of Object.entries(record)) {
      const field = s.properties[key];
      if (field) errors.push(...validateAgainst(field, v, root, `${path}.${key}`));
      else if (s.additionalProperties === false) errors.push(`${path}.${key}: not allowed`);
    }
  }
  return errors;
}
