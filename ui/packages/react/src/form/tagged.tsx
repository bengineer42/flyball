/**
 * A small discriminated union of plain objects (`BlendFlow`: `Absolute |
 * OfBlendMax | OfGuaranteedMax`), rendered compactly: a segmented control
 * for the variant, then that variant's own fields inline -- instead of
 * RJSF's default nested select-then-object-form (a raw class-name title, an
 * asterisk regardless of whether the whole thing is optional, ...).
 *
 * There is no discriminator key on the wire: a plain `X | Y | Z` union
 * serialises as each dataclass's own fields, nothing more, so the variant a
 * value belongs to is read back by matching its keys against each branch's
 * properties -- the same way RJSF's own `isSelect` machinery would, done
 * here so the readback formatter (`formatTagged`) can use it outside a form
 * too.
 */

import { useState } from "react";
import type { FieldProps } from "@rjsf/utils";
import { deref, formatValue, humanise, unwrapNullable, type JsonSchema } from "@flyball/client";
import { enumCount, impliedUiSchema, isTaggedUnion } from "./uiSchema.js";

/**
 * A field's own label: its title, except for an enum -- `SchemaForm`'s
 * `simplifyNullables` inlines a bare `$ref` (with its sibling `default`)
 * before this ever runs, so by here `on_overdrive`'s schema already carries
 * `OnOverdrive`'s own title (the enum class's name) with nothing left to
 * tell it apart from a field genuinely titled that. The property name is
 * the one thing that is always the field's own.
 */
function fieldLabel(key: string, field: JsonSchema): string {
  return enumCount(field) > 0 ? humanise(key) : (field.title ?? humanise(key));
}

/** The branches of a tagged union, `$ref`s resolved -- `schema` itself may be a bare `$ref` too. */
export function taggedBranches(schema: JsonSchema, root: JsonSchema): JsonSchema[] {
  const resolved = deref(schema, root);
  return (resolved.oneOf ?? resolved.anyOf ?? []).map((b) => deref(b, root));
}

/** A branch's display name: its schema `title` (the dataclass's name), humanised. */
export function branchTitle(branch: JsonSchema): string {
  return branch.title ? humanise(branch.title) : "";
}

/** The index of the branch a value belongs to: the one whose properties overlap its keys the most. */
export function matchBranch(value: unknown, branches: JsonSchema[]): number {
  const keys = value && typeof value === "object" && !Array.isArray(value) ? new Set(Object.keys(value)) : new Set<string>();
  let best = 0;
  let bestScore = Number.NEGATIVE_INFINITY;
  branches.forEach((branch, i) => {
    const props = Object.keys(branch.properties ?? {});
    const score = props.filter((k) => keys.has(k)).length - props.filter((k) => !keys.has(k)).length;
    if (score > bestScore) {
      bestScore = score;
      best = i;
    }
  });
  return best;
}

/** A property's default: on the field itself (a `$ref` with a sibling `default`), else on what it resolves to. */
function fieldDefault(field: JsonSchema, root: JsonSchema): unknown {
  return field.default !== undefined ? field.default : deref(field, root).default;
}

/** A branch's fields at their defaults, for the value a variant switch starts from. */
function branchDefaults(branch: JsonSchema, root: JsonSchema): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(branch.properties ?? {})
      .map(([k, f]) => [k, fieldDefault(f as JsonSchema, root)])
      .filter(([, v]) => v !== undefined),
  );
}

/**
 * A tagged union's value as one line: `Absolute · 1 L/min · clamp` -- the
 * matched branch's title, then each of its fields' values (unit-suffixed
 * where the field schema has one), in schema order. `null` when `schema`
 * is not a tagged union or `value` is not a plain object, so the caller
 * falls back to raw JSON.
 */
export function formatTagged(value: unknown, schema: JsonSchema | undefined, root: JsonSchema): string | null {
  if (!schema || value === null || typeof value !== "object" || Array.isArray(value)) return null;
  // See through an `X | None` wrapper (a linked, optional argument often is one) to the union inside.
  const { inner } = unwrapNullable(deref(schema, root));
  if (!isTaggedUnion(inner, root)) return null;
  const branches = taggedBranches(inner, root);
  const branch = branches[matchBranch(value, branches)];
  if (!branch) return null;
  const row = value as Record<string, unknown>;
  const parts = Object.entries(branch.properties ?? {})
    .filter(([k]) => k in row)
    .map(([k, field]) => formatValue(row[k], deref(field as JsonSchema, root)));
  const title = branchTitle(branch);
  return [title, ...parts].filter((s) => s !== "").join(" · ") || null;
}

/**
 * `ui:field` for a tagged union: a segmented control for the variant (its
 * branches' schema titles, humanised -- a `Labelled` enum nested inside a
 * branch gets its own titles the ordinary way, through `impliedUiSchema`),
 * then the chosen branch's own fields, each through the registry's
 * `SchemaField` so it gets its usual widget. A field is required only when
 * the union itself is (the caller may leave the whole thing out) and the
 * branch itself requires it -- so switching a variant never conjures an
 * asterisk that would not have been there without this field.
 */
export function TaggedUnionField(props: FieldProps) {
  const { schema, formData, onChange, registry, idSchema, name, disabled, readonly, required, label: labelProp } = props;
  const root = (registry.rootSchema ?? {}) as JsonSchema;
  const s = schema as JsonSchema;
  const branches = taggedBranches(s, root);
  const [index, setIndex] = useState(() => matchBranch(formData, branches));
  const branch = branches[index] ?? branches[0];
  const label = labelProp || s.title || humanise(name);
  if (!branch) return null;
  // Always present: RJSF's own default registry always has it, and nothing here replaces it.
  const SchemaField = registry.fields.SchemaField!;
  const branchUi = impliedUiSchema(branch, root);
  const row = (formData && typeof formData === "object" && !Array.isArray(formData) ? formData : {}) as Record<string, unknown>;
  const requiredHere = new Set(required ? (branch.required ?? []) : []);
  return (
    <div className="fb-field fb-tagged">
      <label className="fb-field-label" id={`${idSchema.$id}-label`}>
        {label}
        {required && <span className="fb-required">*</span>}
      </label>
      <div className="fb-segmented" role="radiogroup" aria-labelledby={`${idSchema.$id}-label`}>
        {branches.map((b, i) => {
          const checked = i === index;
          return (
            <label key={i} className={checked ? "fb-segment fb-segment-on" : "fb-segment"}>
              <input
                type="radio"
                name={`${idSchema.$id}-variant`}
                checked={checked}
                disabled={disabled || readonly}
                onChange={() => {
                  setIndex(i);
                  onChange(branchDefaults(b, root));
                }}
              />
              {branchTitle(b) || `option ${i + 1}`}
            </label>
          );
        })}
      </div>
      <div className="fb-tagged-fields">
        {Object.entries(branch.properties ?? {}).map(([key, fieldSchema]) => (
          <SchemaField
            key={key}
            name={key}
            schema={fieldSchema as JsonSchema}
            uiSchema={{ ...(branchUi[key] ?? {}), "ui:title": fieldLabel(key, fieldSchema as JsonSchema) }}
            idSchema={{ $id: `${idSchema.$id}_${key}` }}
            formData={row[key]}
            required={requiredHere.has(key)}
            disabled={disabled}
            readonly={readonly}
            registry={registry}
            onChange={(value: unknown) => onChange({ ...row, [key]: value })}
            onBlur={() => undefined}
            onFocus={() => undefined}
          />
        ))}
      </div>
    </div>
  );
}
