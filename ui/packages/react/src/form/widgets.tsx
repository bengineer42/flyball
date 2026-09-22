/**
 * Front-panel controls for RJSF: a slider for a bounded number, a stepper for
 * an unbounded one, a switch for a boolean, segmented buttons for a short
 * enum, a text box for a plain string. Plain HTML under `.fb-*` classes, so
 * they render the same whichever RJSF theme's Form hosts them.
 *
 * Each widget draws its own label: core's FieldTemplate labels a field and
 * MUI's leaves it to the widget, and the implied uiSchema turns the template's
 * label off so there is exactly one either way. The description comes from
 * the registry's own template, so it matches the theme.
 */

import type { WidgetProps } from "@rjsf/utils";
import {
  ariaDescribedByIds,
  descriptionId,
  enumOptionsIndexForValue,
  enumOptionsValueForIndex,
  optionId,
} from "@rjsf/utils";
import type { JsonSchema } from "@flyball/client";
import type { ReactNode } from "react";

const labelId = (id: string) => `${id}-label`;

function decimals(step: number): number {
  return Math.max(0, -Math.floor(Math.log10(step)));
}

/** Snap floating-point drift after stepping: 0.1 + 0.2 → 0.3. */
function snap(value: number, step: number): number {
  return Number(value.toFixed(decimals(step)));
}

/** 1, 2 or 5 × 10^k, the largest not above `raw`. */
function niceStep(raw: number): number {
  if (!(raw > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(raw));
  const m = raw / p;
  return snap((m >= 5 ? 5 : m >= 2 ? 2 : 1) * p, p);
}

function bounds(s: JsonSchema) {
  return { min: s.minimum ?? s.exclusiveMinimum, max: s.maximum ?? s.exclusiveMaximum };
}

function clamp(value: number, min?: number, max?: number): number {
  if (min !== undefined && value < min) return min;
  if (max !== undefined && value > max) return max;
  return value;
}

/**
 * A field's description as a hover hint beside its label -- the ⓘ is the
 * cue that there is one -- with the text itself kept for assistive
 * technology under the id the control's `aria-describedby` names. Body
 * text under every label made a form of five fields read as a page of
 * prose in five sizes.
 */
export function Hint({ id, description }: { id: string; description: string }) {
  return (
    <>
      <span className="fb-hint" title={description} tabIndex={0} role="img" aria-label={description}>
        ⓘ
      </span>
      <span id={id} className="fb-visually-hidden">
        {description}
      </span>
    </>
  );
}

/** Label above (its description a hover hint beside it), the control, all under one id. */
function Field({ id, label, required, schema, children }: WidgetProps & { children: ReactNode }) {
  const description = (schema as JsonSchema).description;
  return (
    <div className="fb-field">
      {(label || description) && (
        <span className="fb-field-head">
          {label && (
            <label className="fb-field-label" id={labelId(id)} htmlFor={id}>
              {label}
              {required && <span className="fb-required">*</span>}
            </label>
          )}
          {description && <Hint id={descriptionId(id)} description={description} />}
        </span>
      )}
      {children}
    </div>
  );
}

function invalid(props: WidgetProps): boolean {
  return (props.rawErrors?.length ?? 0) > 0;
}

/** A number input with the schema's `unit` as a suffix and ± buttons stepping by `multipleOf`, else 1. */
export function UnitNumberWidget(props: WidgetProps) {
  const { id, value, onChange, onBlur, onFocus, disabled, readonly, schema, required, autofocus, placeholder } = props;
  const s = schema as JsonSchema;
  const { min, max } = bounds(s);
  const step = s.multipleOf ?? 1;
  const off = disabled || readonly;
  const nudge = (dir: 1 | -1) => onChange(snap(clamp((typeof value === "number" ? value : min ?? 0) + dir * step, min, max), step));
  return (
    <Field {...props}>
      <span className="fb-unit-input fb-stepper">
        <button type="button" className="fb-step" aria-label="decrease" disabled={off} onClick={() => nudge(-1)}>
          −
        </button>
        <input
          id={id}
          type="number"
          value={value ?? ""}
          placeholder={placeholder}
          step={s.multipleOf ?? "any"}
          min={min}
          max={max}
          required={required}
          disabled={off}
          autoFocus={autofocus}
          aria-invalid={invalid(props)}
          aria-describedby={ariaDescribedByIds(id)}
          onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
          onBlur={(e) => onBlur(id, e.target.value)}
          onFocus={(e) => onFocus(id, e.target.value)}
        />
        <button type="button" className="fb-step" aria-label="increase" disabled={off} onClick={() => nudge(1)}>
          +
        </button>
        {s.unit && <span className="fb-unit">{s.unit}</span>}
      </span>
    </Field>
  );
}

/** A text input for a plain string (a signal's name, a label): the same label and `fb-*` frame as the number widgets. */
export function TextWidget(props: WidgetProps) {
  const { id, value, onChange, onBlur, onFocus, disabled, readonly, schema, required, autofocus, placeholder } = props;
  const s = schema as JsonSchema;
  return (
    <Field {...props}>
      <span className="fb-unit-input fb-text">
        <input
          id={id}
          type="text"
          value={typeof value === "string" ? value : ""}
          placeholder={placeholder}
          required={required}
          disabled={disabled || readonly}
          autoFocus={autofocus}
          aria-invalid={invalid(props)}
          aria-describedby={ariaDescribedByIds(id)}
          onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
          onBlur={(e) => onBlur(id, e.target.value)}
          onFocus={(e) => onFocus(id, e.target.value)}
        />
        {s.unit && <span className="fb-unit">{s.unit}</span>}
      </span>
    </Field>
  );
}

/** A slider and a number input, in step, for a number with both bounds. */
export function SliderNumberWidget(props: WidgetProps) {
  const { id, value, onChange, onBlur, onFocus, disabled, readonly, schema, required, autofocus, label } = props;
  const s = schema as JsonSchema;
  const { min, max } = bounds(s) as { min: number; max: number };
  const raw = niceStep((max - min) / 200);
  const step = s.multipleOf ?? (s.type === "integer" ? Math.max(1, Math.round(raw)) : raw);
  const off = disabled || readonly;
  const num = typeof value === "number" ? value : undefined;
  return (
    <Field {...props}>
      <span className="fb-unit-input fb-slider">
        <input
          id={`${id}-slider`}
          type="range"
          className="fb-slider-track"
          value={num ?? min}
          min={min}
          max={max}
          step={step}
          disabled={off}
          aria-labelledby={label ? labelId(id) : undefined}
          aria-valuetext={num === undefined ? "" : s.unit ? `${num} ${s.unit}` : String(num)}
          onChange={(e) => onChange(Number(e.target.value))}
        />
        <input
          id={id}
          type="number"
          value={num ?? ""}
          step={step}
          min={min}
          max={max}
          required={required}
          disabled={off}
          autoFocus={autofocus}
          aria-invalid={invalid(props)}
          aria-describedby={ariaDescribedByIds(id)}
          onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
          onBlur={(e) => onBlur(id, e.target.value)}
          onFocus={(e) => onFocus(id, e.target.value)}
        />
        {s.unit && <span className="fb-unit">{s.unit}</span>}
      </span>
    </Field>
  );
}

/** Two number inputs for a `[low, high]` pair (a `Band`): the RJSF `ArrayField` this replaces does not
 * tolerate a `null` value (switched to from a nullable field's "leave unchanged"), so this widget must,
 * rendering both boxes empty rather than throwing. */
export function BandWidget(props: WidgetProps) {
  const { id, value, onChange, onBlur, onFocus, disabled, readonly, schema, required, autofocus } = props;
  const s = schema as JsonSchema;
  const itemSchema = (Array.isArray(s.items) ? s.items[0] : undefined) as JsonSchema | undefined;
  const step = itemSchema?.multipleOf ?? "any";
  const off = disabled || readonly;
  const [lo, hi]: Array<number | undefined> = Array.isArray(value) ? value : [undefined, undefined];
  const set = (index: 0 | 1, n: number | undefined) => onChange(index === 0 ? [n, hi] : [lo, n]);
  return (
    <Field {...props}>
      <span className="fb-unit-input fb-band">
        <input
          id={id}
          type="number"
          value={lo ?? ""}
          step={step}
          required={required}
          disabled={off}
          autoFocus={autofocus}
          aria-invalid={invalid(props)}
          aria-describedby={ariaDescribedByIds(id)}
          onChange={(e) => set(0, e.target.value === "" ? undefined : Number(e.target.value))}
          onBlur={(e) => onBlur(id, e.target.value)}
          onFocus={(e) => onFocus(id, e.target.value)}
        />
        <span className="fb-band-sep" aria-hidden="true">
          –
        </span>
        <input
          id={`${id}-1`}
          type="number"
          value={hi ?? ""}
          step={step}
          required={required}
          disabled={off}
          aria-invalid={invalid(props)}
          aria-describedby={ariaDescribedByIds(id)}
          onChange={(e) => set(1, e.target.value === "" ? undefined : Number(e.target.value))}
          onBlur={(e) => onBlur(id, e.target.value)}
          onFocus={(e) => onFocus(id, e.target.value)}
        />
        {s.unit && <span className="fb-unit">{s.unit}</span>}
      </span>
    </Field>
  );
}

/** A switch: a checkbox styled as a toggle, its label beside it. */
export function ToggleWidget(props: WidgetProps) {
  const { id, value, onChange, onBlur, onFocus, disabled, readonly, schema, label, required, autofocus } = props;
  const description = (schema as JsonSchema).description;
  return (
    <div className="fb-field">
      <label className="fb-toggle" htmlFor={id}>
        <input
          id={id}
          type="checkbox"
          role="switch"
          checked={value === true}
          disabled={disabled || readonly}
          autoFocus={autofocus}
          aria-invalid={invalid(props)}
          aria-describedby={ariaDescribedByIds(id)}
          onChange={(e) => onChange(e.target.checked)}
          onBlur={(e) => onBlur(id, e.target.checked)}
          onFocus={(e) => onFocus(id, e.target.checked)}
        />
        <span className="fb-toggle-track" aria-hidden="true" />
        {label && (
          <span className="fb-toggle-label">
            {label}
            {required && <span className="fb-required">*</span>}
          </span>
        )}
        {description && <Hint id={descriptionId(id)} description={description} />}
      </label>
    </div>
  );
}

/** Radio buttons drawn as a segmented button group, for an enum of a few titled options. */
export function SegmentedWidget(props: WidgetProps) {
  const { id, value, onChange, onBlur, onFocus, disabled, readonly, required, options, label } = props;
  const enumOptions = options.enumOptions ?? [];
  const enumDisabled = (options.enumDisabled ?? []) as unknown[];
  const selected = enumOptionsIndexForValue(value, enumOptions);
  return (
    <Field {...props}>
      <div
        className="fb-segmented"
        role="radiogroup"
        aria-labelledby={label ? labelId(id) : undefined}
        aria-required={required}
        aria-invalid={invalid(props)}
        aria-describedby={ariaDescribedByIds(id)}
      >
        {enumOptions.map((option, i) => {
          const off = disabled || readonly || enumDisabled.includes(option.value);
          const checked = selected === String(i);
          return (
            <label key={i} className={checked ? "fb-segment fb-segment-on" : "fb-segment"}>
              <input
                type="radio"
                id={optionId(id, i)}
                name={id}
                value={i}
                checked={checked}
                disabled={off}
                onChange={() => onChange(enumOptionsValueForIndex(i, enumOptions))}
                onBlur={() => onBlur(id, enumOptionsValueForIndex(i, enumOptions))}
                onFocus={() => onFocus(id, enumOptionsValueForIndex(i, enumOptions))}
              />
              {option.label}
            </label>
          );
        })}
      </div>
    </Field>
  );
}

export const widgets = {
  text: TextWidget,
  unitNumber: UnitNumberWidget,
  slider: SliderNumberWidget,
  band: BandWidget,
  toggle: ToggleWidget,
  segmented: SegmentedWidget,
};
