/**
 * What the schema implies about how to draw it. Two rewrites: pydantic's
 * `X | null` into something RJSF renders sensibly, and a `uiSchema` that
 * points each field at the widget its bounds, type and options call for.
 */

import type { UiSchema } from "@rjsf/utils";
import { deref, type JsonSchema } from "@flyball/client";

export const UNSET = "— leave unchanged —";

/** Segmented buttons up to this many options; a select beyond. */
const SEGMENTED_MAX = 4;

/**
 * Rewrite pydantic's optional pattern `anyOf: [X, {type: null}]`.
 *
 * A nullable scalar becomes `X` left out of `required`: an empty box means
 * "omit". A nullable object or union cannot be blank, so it keeps a two-way
 * choice with the null branch first and titled, which RJSF renders as
 * "leave unchanged | <the thing>" and defaults to the former.
 *
 * Also inlines a `$ref` that carries siblings (`{$ref, default}`, pydantic's
 * enum-with-default): inside an `anyOf` branch RJSF 5 keeps re-applying that
 * default over the user's choice; and rewrites a 2020-12 tuple
 * (`prefixItems`, what pydantic emits for `tuple[float, float]`) as the
 * draft-07 `items: [...]` RJSF 5 renders.
 */
export function simplifyNullables(schema: JsonSchema): JsonSchema {
  const isCompound = (s: JsonSchema) => Boolean(s.properties || s.oneOf || s.anyOf || s.$ref);
  const walk = (node: JsonSchema): JsonSchema => {
    if (node.$ref && Object.keys(node).length > 1) {
      const { $ref: _ref, ...siblings } = node;
      return walk({ ...deref(node, schema), ...siblings });
    }
    if (node.anyOf && node.anyOf.length === 2) {
      const nonNull = node.anyOf.filter((b) => b.type !== "null");
      if (nonNull.length === 1 && nonNull[0]) {
        const { anyOf: _drop, ...rest } = node;
        const inner = walk(nonNull[0]);
        if (isCompound(inner)) {
          const set = { ...inner, title: inner.title ?? rest.title ?? "set" };
          return { ...rest, anyOf: [{ type: "null", title: UNSET }, set] };
        }
        return { ...inner, ...rest };
      }
    }
    const out: JsonSchema = { ...node };
    if (Array.isArray(node.prefixItems) && !node.items) {
      const { prefixItems, ...rest } = out;
      void prefixItems;
      return walk({ ...rest, items: node.prefixItems, ...(node.maxItems === node.prefixItems.length ? { additionalItems: false } : {}) });
    }
    if (node.properties) {
      out.properties = Object.fromEntries(Object.entries(node.properties).map(([k, v]) => [k, walk(v)]));
    }
    if (node.$defs) {
      out.$defs = Object.fromEntries(Object.entries(node.$defs).map(([k, v]) => [k, walk(v)]));
    }
    if (node.items) out.items = Array.isArray(node.items) ? node.items.map(walk) : walk(node.items);
    if (node.additionalProperties && typeof node.additionalProperties === "object") out.additionalProperties = walk(node.additionalProperties as JsonSchema);
    return out;
  };
  return walk(schema);
}

/** `enum`, or the `oneOf: [{const, title}, …]` pydantic emits for a titled enum. */
export function enumCount(schema: JsonSchema): number {
  if (schema.enum) return schema.enum.length;
  if (schema.oneOf && schema.oneOf.every((b) => "const" in b)) return schema.oneOf.length;
  return 0;
}

export function isBounded(schema: JsonSchema): boolean {
  return (schema.minimum ?? schema.exclusiveMinimum) !== undefined && (schema.maximum ?? schema.exclusiveMaximum) !== undefined;
}

/** The `[low, high]` tuple `simplifyNullables` rewrites a `Band` (`tuple[float, float]`) into. */
function isBand(schema: JsonSchema): boolean {
  return Array.isArray(schema.items) && schema.items.length === 2 && schema.items.every((i) => i.type === "number" || i.type === "integer");
}

/**
 * The widget each field's schema calls for. Every custom widget draws its own
 * label (so it looks the same under any RJSF theme's FieldTemplate), hence
 * `label: false` to stop the template drawing another.
 */
function widgetFor(schema: JsonSchema): string | undefined {
  const type = schema.type;
  if (type === "number" || type === "integer") return isBounded(schema) ? "slider" : "unitNumber";
  if (type === "boolean") return "toggle";
  const n = enumCount(schema);
  if (n > 0 && n <= SEGMENTED_MAX) return "segmented";
  if (n === 0 && type === "string") return "text";
  if (isBand(schema)) return "band";
  return undefined;
}

/**
 * The ui hints for one field's schema: a widget, one entry per branch of a
 * union (RJSF's `uiSchema.<field>.anyOf[i]`, each with the branch's own title
 * suppressed -- the select above already names it, and themes that draw
 * object titles (mui) would show it twice), nested `properties`, or -- a
 * dict's value schema, the RJSF convention `uiSchema.<field>.additionalProperties`
 * mirroring `uiSchema.items` for an array (`ports.<name>` in a `sim_drive`
 * config: without this a dict's own widgets, and any union or nested object
 * inside them, never get their hints, and RJSF falls back to its own
 * `ArrayField` for a bounded tuple like `limits`, which does not tolerate a
 * `null` value switched to from "leave unchanged").
 */
function fieldUiSchema(field: JsonSchema, root: JsonSchema): UiSchema {
  const fieldSchema = deref(field, root);
  const widget = widgetFor(fieldSchema);
  const union = fieldSchema.anyOf ? "anyOf" : fieldSchema.oneOf ? "oneOf" : undefined;
  if (widget) return { "ui:widget": widget, "ui:options": { label: false } };
  if (union) return { [union]: fieldSchema[union]!.map((b) => ({ ...fieldUiSchema(b, root), "ui:options": { label: false } })) };
  if (fieldSchema.properties) return impliedUiSchema(fieldSchema, root);
  if (fieldSchema.additionalProperties && typeof fieldSchema.additionalProperties === "object") {
    return { additionalProperties: fieldUiSchema(fieldSchema.additionalProperties as JsonSchema, root) };
  }
  return {};
}

/** Point each field at its widget. Walks `properties`, following `$ref`s. */
export function impliedUiSchema(schema: JsonSchema, root: JsonSchema): UiSchema {
  const ui: UiSchema = {};
  const resolved = deref(schema, root);
  for (const [name, field] of Object.entries(resolved.properties ?? {})) {
    const fieldUi = fieldUiSchema(field, root);
    if (Object.keys(fieldUi).length) ui[name] = fieldUi;
  }
  return ui;
}
