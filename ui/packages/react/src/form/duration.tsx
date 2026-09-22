/**
 * `Duration` (`engine/src/flyball/foundation/time.py`): a span of time as unit keys that add
 * (`{minutes: 5}`) or a bare number of seconds. Its schema is an `anyOf` of those two shapes,
 * which RJSF's default rendering turns into a branch-picker dropdown plus, below it, that
 * branch's own nested object form (every unit key as its own blank field) -- for what is, to
 * an operator, one number and a unit. `ui:field` (not `ui:widget`: the schema itself carries
 * `anyOf`, which only a field can fully replace) points it at this instead: one number input,
 * one unit dropdown.
 */

import { useState } from "react";
import type { FieldProps } from "@rjsf/utils";
import { ariaDescribedByIds, descriptionId } from "@rjsf/utils";
import { humanise } from "@flyball/client";
import { Hint } from "./widgets.js";

const DURATION_UNIT_SECONDS: Record<string, number> = {
  seconds: 1,
  minutes: 60,
  hours: 3600,
  days: 86400,
  milliseconds: 1e-3,
  microseconds: 1e-6,
  nanoseconds: 1e-9,
};
const DURATION_UNITS = Object.keys(DURATION_UNIT_SECONDS);

/** `Duration`'s wire value (a unit-keyed object, or a bare number of seconds) as seconds. */
function durationSeconds(value: unknown): number | undefined {
  if (typeof value === "number") return value;
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, number>);
    if (!entries.length) return undefined;
    return entries.reduce((sum, [unit, n]) => sum + (DURATION_UNIT_SECONDS[unit] ?? 0) * n, 0);
  }
  return undefined;
}

/** The unit to display a `Duration` value in: the object's one non-zero key, else seconds. */
function durationUnit(value: unknown): string {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const keys = Object.keys(value as Record<string, number>).filter((k) => (value as Record<string, number>)[k] !== 0);
    if (keys.length === 1 && DURATION_UNIT_SECONDS[keys[0]!] !== undefined) return keys[0]!;
  }
  return "seconds";
}

/** `ui:field` for a `Duration`-shaped `anyOf`: see the module doc. */
export function DurationField(props: FieldProps) {
  const { schema, formData, onChange, disabled, readonly, required, idSchema, name, label: labelProp, rawErrors } = props;
  const label = labelProp || schema.title || humanise(name);
  const description = schema.description as string | undefined;
  const id = idSchema.$id;
  const off = disabled || readonly;
  const [unit, setUnit] = useState(() => durationUnit(formData));
  const seconds = durationSeconds(formData);
  const displayed = seconds === undefined ? undefined : seconds / DURATION_UNIT_SECONDS[unit]!;
  const emit = (n: number | undefined, u: string) => onChange(n === undefined ? undefined : u === "seconds" ? n : { [u]: n });
  return (
    <div className="fb-field">
      <span className="fb-field-head">
        <label className="fb-field-label" id={`${id}-label`} htmlFor={id}>
          {label}
          {required && <span className="fb-required">*</span>}
        </label>
        {description && <Hint id={descriptionId(id)} description={description} />}
      </span>
      <span className="fb-unit-input fb-duration">
        <input
          id={id}
          type="number"
          value={displayed ?? ""}
          min={0}
          required={required}
          disabled={off}
          aria-invalid={(rawErrors?.length ?? 0) > 0}
          aria-describedby={ariaDescribedByIds(id)}
          onChange={(e) => emit(e.target.value === "" ? undefined : Number(e.target.value), unit)}
        />
        <select
          className="fb-duration-unit"
          value={unit}
          disabled={off}
          onChange={(e) => {
            const next = e.target.value;
            setUnit(next);
            if (seconds !== undefined) emit(seconds / DURATION_UNIT_SECONDS[next]!, next);
          }}
        >
          {DURATION_UNITS.map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>
      </span>
    </div>
  );
}
