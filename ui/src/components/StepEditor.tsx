/**
 * One step, edited.
 *
 * Nothing here knows what a ramp or a hold is: the form is built from the
 * descriptor's `fields`, so a new step kind added to the registry gets an
 * editor for free. Only the three composite kinds have bespoke editors, and
 * those are shared with the manual panel.
 */

import type { FlowRequest, LawConfig, Pace, PumpsSpec, Step } from "../api/types";
import { describe, summarise, type FieldSpec } from "../programs/steps";
import type { Issue } from "../programs/validate";
import { FlowEditor, LawEditor, PaceEditor } from "./editors";
import { Badge, NumberField, Note, SelectField, TextField } from "./primitives";

export interface StepEditorProps {
  step: Step;
  onChange(step: Step): void;
  spec: PumpsSpec | null | undefined;
  issues: ReadonlyArray<Issue>;
}

/**
 * The generic writer needs index access that a discriminated union does not
 * offer. The registry guarantees each field key belongs to its step kind, so
 * the cast is sound; it is confined to these two helpers.
 */
type Mutable = Record<string, unknown>;

function fields(step: Step): Mutable {
  return step as unknown as Mutable;
}

function withField(step: Step, key: string, value: unknown): Step {
  return { ...fields(step), [key]: value } as unknown as Step;
}

export function StepEditor({ step, onChange, spec, issues }: StepEditorProps) {
  const descriptor = describe(step);
  const set = (key: string, value: unknown) => onChange(withField(step, key, value));

  const field = (spec_: FieldSpec<Step>) => {
    const key = spec_.key as string;
    const current = fields(step)[key];

    switch (spec_.kind) {
      case "number":
        return (
          <NumberField
            key={key}
            label={spec_.label}
            unit={spec_.unit === "flow" ? (spec?.units ?? undefined) : spec_.unit}
            min={spec_.min}
            max={spec_.max}
            step={spec_.step}
            optional={spec_.optional}
            help={spec_.help}
            value={typeof current === "number" ? current : null}
            onChange={(value) => set(key, value)}
          />
        );
      case "select":
        return (
          <SelectField
            key={key}
            label={spec_.label}
            help={spec_.help}
            value={String(current ?? spec_.options[0].value)}
            options={spec_.options.map((option) => ({ value: option.value, label: option.label }))}
            onChange={(value) => set(key, value)}
          />
        );
      case "text":
        return (
          <TextField
            key={key}
            label={spec_.label}
            placeholder={spec_.placeholder}
            optional={spec_.optional}
            help={spec_.help}
            value={typeof current === "string" ? current : ""}
            onChange={(value) => set(key, spec_.optional && value === "" ? null : value)}
          />
        );
      case "flow":
        return (
          <div key={key} className="full">
            <FlowEditor
              label={spec_.label}
              optional={spec_.optional}
              spec={spec}
              wetFraction={
                typeof fields(step).wet_fraction === "number"
                  ? (fields(step).wet_fraction as number)
                  : 0.5
              }
              value={(current as FlowRequest | null) ?? null}
              onChange={(value) => set(key, value)}
            />
          </div>
        );
      case "law":
        return (
          <div key={key} className="full">
            <LawEditor
              label={spec_.label}
              optional={spec_.optional}
              value={(current as LawConfig | null) ?? null}
              onChange={(value) => set(key, value)}
            />
          </div>
        );
      case "pace":
        return (
          <div key={key} className="full">
            <PaceEditor value={current as Pace} onChange={(value) => set(key, value)} />
          </div>
        );
    }
  };

  return (
    <div className="step-body">
      {descriptor.fields.map((each) => field(each as FieldSpec<Step>))}
      <div className="full">
        <TextField
          label="Note"
          optional
          value={step.note ?? ""}
          placeholder="why this step is here"
          onChange={(value) => set("note", value || undefined)}
        />
      </div>
      {issues.length > 0 && (
        <div className="full stack tight">
          {issues.map((issue, index) => (
            <Note key={index} tone={issue.level === "error" ? "critical" : "warning"}>
              {issue.message}
            </Note>
          ))}
        </div>
      )}
    </div>
  );
}

export interface StepCardProps extends StepEditorProps {
  index: number;
  open: boolean;
  current: boolean;
  flowUnits: string | null | undefined;
  onToggle(): void;
  onMove(delta: number): void;
  onRemove(): void;
  onDuplicate(): void;
}

export function StepCard({
  index,
  open,
  current,
  flowUnits,
  onToggle,
  onMove,
  onRemove,
  onDuplicate,
  ...editor
}: StepCardProps) {
  const descriptor = describe(editor.step);
  const errors = editor.issues.filter((issue) => issue.level === "error").length;
  const warnings = editor.issues.length - errors;

  return (
    <div className={`step${open ? " open" : ""}${current ? " current" : ""}`}>
      <div
        className="step-head"
        onClick={onToggle}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onToggle();
          }
        }}
        aria-expanded={open}
      >
        <span className="index">{index + 1}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="row" style={{ gap: 6 }}>
            <span className="step-title">{descriptor.label}</span>
            {descriptor.blocking && <Badge>waits</Badge>}
            {errors > 0 && <Badge tone="critical">{errors} error{errors > 1 ? "s" : ""}</Badge>}
            {errors === 0 && warnings > 0 && <Badge tone="warning">{warnings}</Badge>}
          </div>
          <div className="step-summary">
            {summarise(editor.step, { flowUnits })}
            {editor.step.note ? ` — ${editor.step.note}` : ""}
          </div>
        </div>
        <div className="row" onClick={(event) => event.stopPropagation()}>
          <button type="button" className="ghost small" onClick={() => onMove(-1)} aria-label="Move up">
            ↑
          </button>
          <button type="button" className="ghost small" onClick={() => onMove(1)} aria-label="Move down">
            ↓
          </button>
          <button type="button" className="ghost small" onClick={onDuplicate} aria-label="Duplicate">
            ⧉
          </button>
          <button type="button" className="ghost small" onClick={onRemove} aria-label="Remove">
            ✕
          </button>
        </div>
      </div>
      {open && <StepEditor {...editor} />}
    </div>
  );
}
