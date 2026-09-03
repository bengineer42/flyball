/**
 * The small pieces every panel is built from.
 *
 * Each takes an explicit props interface and renders; none of them fetch, none
 * hold rig state. `Tile` in particular is the answer to "what if this value
 * does not exist" — it takes `number | null` and a reason, so a missing dry
 * line renders as an explained dash everywhere it appears, identically.
 */

import type { ReactNode } from "react";

export type Tone = "neutral" | "good" | "warning" | "critical";

export interface CardProps {
  title?: ReactNode;
  hint?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Card({ title, hint, actions, children, className }: CardProps) {
  return (
    <section className={className ? `card ${className}` : "card"}>
      {(title || actions || hint) && (
        <header>
          <div className="row" style={{ gap: 8 }}>
            {title && <h2>{title}</h2>}
            {hint && <span className="hint">{hint}</span>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export interface TileProps {
  label: ReactNode;
  /** null renders the absent state — never a zero standing in for "unknown". */
  value: string | null;
  sub?: ReactNode;
  /** Why the value is missing. Shown in place of the value. */
  absentNote?: string;
  colour?: string;
  small?: boolean;
}

export function Tile({ label, value, sub, absentNote, colour, small }: TileProps) {
  return (
    <div className="tile">
      <span className="label">
        {colour && <span className="swatch" style={{ background: colour }} aria-hidden />}
        {label}
      </span>
      <span className={`value${small ? " small" : ""}${value === null ? " absent" : ""}`}>
        {value ?? "—"}
      </span>
      {value === null && absentNote ? (
        <span className="sub">{absentNote}</span>
      ) : (
        sub && <span className="sub">{sub}</span>
      )}
    </div>
  );
}

export interface BadgeProps {
  tone?: Tone;
  children: ReactNode;
  dot?: boolean;
}

export function Badge({ tone = "neutral", children, dot }: BadgeProps) {
  return (
    <span className={tone === "neutral" ? "badge" : `badge ${tone}`}>
      {dot && <span className="dot" aria-hidden />}
      {children}
    </span>
  );
}

export interface NoteProps {
  tone?: Tone;
  children: ReactNode;
}

export function Note({ tone = "neutral", children }: NoteProps) {
  return <div className={tone === "neutral" ? "note" : `note ${tone}`}>{children}</div>;
}

export interface NumberFieldProps {
  label: ReactNode;
  value: number | null;
  onChange(value: number | null): void;
  min?: number;
  max?: number;
  step?: number;
  unit?: string | null;
  /** Allow clearing to null, for genuinely optional values. */
  optional?: boolean;
  help?: ReactNode;
  disabled?: boolean;
}

export function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step,
  unit,
  optional,
  help,
  disabled,
}: NumberFieldProps) {
  return (
    <label className="field">
      <span>
        {label}
        {unit ? ` (${unit})` : ""}
        {optional && <span className="help"> · optional</span>}
      </span>
      <input
        type="number"
        value={value === null || value === undefined ? "" : value}
        min={min}
        max={max}
        step={step ?? "any"}
        disabled={disabled}
        onChange={(event) => {
          const raw = event.target.value;
          if (raw === "") {
            onChange(optional ? null : 0);
            return;
          }
          const parsed = Number(raw);
          onChange(Number.isFinite(parsed) ? parsed : null);
        }}
      />
      {help && <span className="help">{help}</span>}
    </label>
  );
}

export interface TextFieldProps {
  label: ReactNode;
  value: string;
  onChange(value: string): void;
  placeholder?: string;
  help?: ReactNode;
  optional?: boolean;
}

export function TextField({ label, value, onChange, placeholder, help, optional }: TextFieldProps) {
  return (
    <label className="field">
      <span>
        {label}
        {optional && <span className="help"> · optional</span>}
      </span>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
      {help && <span className="help">{help}</span>}
    </label>
  );
}

export interface SelectFieldProps<T extends string> {
  label: ReactNode;
  value: T;
  options: ReadonlyArray<{ value: T; label: string }>;
  onChange(value: T): void;
  help?: ReactNode;
  disabled?: boolean;
}

export function SelectField<T extends string>({
  label,
  value,
  options,
  onChange,
  help,
  disabled,
}: SelectFieldProps<T>) {
  return (
    <label className="field">
      <span>{label}</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as T)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {help && <span className="help">{help}</span>}
    </label>
  );
}

export interface ToggleProps {
  label: ReactNode;
  checked: boolean;
  onChange(checked: boolean): void;
  help?: ReactNode;
  disabled?: boolean;
}

export function Toggle({ label, checked, onChange, help, disabled }: ToggleProps) {
  return (
    <label className="field inline">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>
        {label}
        {help && <span className="help"> — {help}</span>}
      </span>
    </label>
  );
}

export interface SliderProps {
  label: ReactNode;
  value: number;
  onChange(value: number): void;
  min: number;
  max: number;
  step: number;
  format?(value: number): string;
  disabled?: boolean;
}

export function Slider({ label, value, onChange, min, max, step, format, disabled }: SliderProps) {
  return (
    <label className="field">
      <span className="row" style={{ justifyContent: "space-between" }}>
        <span>{label}</span>
        <span className="mono">{format ? format(value) : value.toFixed(2)}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

/** A feature the daemon does not serve yet, named with its requirement. */
export function Unavailable({
  what,
  requirement,
  children,
}: {
  what: string;
  requirement: string;
  children?: ReactNode;
}) {
  return (
    <Note>
      <strong>{what}</strong> is not available from this rig yet ({requirement} in{" "}
      <span className="mono">ui/API-REQUIREMENTS.md</span>).
      {children ? <div style={{ marginTop: 4 }}>{children}</div> : null}
    </Note>
  );
}
