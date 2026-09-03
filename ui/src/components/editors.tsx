/**
 * Editors for the composite domain values.
 *
 * A `BlendFlow` and a control law are each more than one input, and both are
 * edited in two places — a program step and the manual panel. They live here so
 * those two places cannot drift apart in what they accept.
 */

import type { FlowRequest, LawConfig, LawType, Pace, PumpsSpec } from "../api/types";
import { FLOW_SCALE_LABELS, maxFlowAt, resolveFlow } from "../domain/flows";
import { formatQuantity } from "../domain/units";
import { DEFAULT_FLOW, DEFAULT_LAW, TIME_UNITS } from "../programs/steps";
import { NumberField, SelectField, Toggle } from "./primitives";

export interface FlowEditorProps {
  label?: string;
  value: FlowRequest | null;
  onChange(value: FlowRequest | null): void;
  /** Allows "use the rig default" as a value. */
  optional?: boolean;
  /** Enables the live preview of what the request resolves to. */
  spec?: PumpsSpec | null;
  /** The blend the preview should assume. */
  wetFraction?: number;
}

export function FlowEditor({
  label = "Total flow",
  value,
  onChange,
  optional,
  spec,
  wetFraction = 0.5,
}: FlowEditorProps) {
  if (optional && value === null) {
    return (
      <fieldset>
        <legend>{label}</legend>
        <Toggle
          label="Use the rig's default flow"
          checked
          onChange={() => onChange({ ...DEFAULT_FLOW })}
          help="the maximum every blend can reach"
        />
      </fieldset>
    );
  }

  const flow = value ?? { ...DEFAULT_FLOW };
  const absolute = flow.scale === "absolute";
  const resolved = spec ? resolveFlow(flow, wetFraction, spec) : null;
  const ceiling = spec ? maxFlowAt(spec, wetFraction) : null;

  return (
    <fieldset>
      <legend>{label}</legend>
      {optional && (
        <Toggle
          label="Use the rig's default flow"
          checked={false}
          onChange={() => onChange(null)}
        />
      )}
      <div className="grid cols-2" style={{ gap: 8 }}>
        <SelectField<FlowRequest["scale"]>
          label="Scale"
          value={flow.scale}
          options={[
            { value: "full_range_max", label: "Fraction of full-range max" },
            { value: "blend_max", label: "Fraction of this blend's max" },
            { value: "absolute", label: `Absolute${spec?.units ? ` (${spec.units})` : ""}` },
          ]}
          onChange={(scale) =>
            onChange({
              ...flow,
              scale,
              // A fraction and an absolute flow are not the same number.
              value: scale === "absolute" ? (resolved ?? flow.value) : Math.min(flow.value, 1),
              on_overdrive: scale === "absolute" ? (flow.on_overdrive ?? "raise") : null,
            })
          }
        />
        <NumberField
          label={absolute ? "Flow" : "Fraction"}
          value={flow.value}
          min={0}
          max={absolute ? undefined : 1}
          step={absolute ? 0.01 : 0.05}
          unit={absolute ? (spec?.units ?? undefined) : undefined}
          onChange={(next) => onChange({ ...flow, value: next ?? 0 })}
        />
        {absolute && (
          <SelectField<"raise" | "clamp">
            label="If it exceeds the pumps"
            value={flow.on_overdrive ?? "raise"}
            options={[
              { value: "raise", label: "Refuse the request" },
              { value: "clamp", label: "Clamp to the maximum" },
            ]}
            onChange={(on_overdrive) => onChange({ ...flow, on_overdrive })}
          />
        )}
      </div>
      {resolved !== null && (
        <p className="hint" style={{ margin: "6px 0 0" }}>
          At {(wetFraction * 100).toFixed(0)}% wet this is{" "}
          <span className="mono">{formatQuantity(resolved, spec?.units)}</span>
          {ceiling !== null && (
            <>
              {" "}
              of a possible <span className="mono">{formatQuantity(ceiling, spec?.units)}</span>
              {resolved > ceiling + 1e-9 && (
                <strong> — beyond what the pumps can deliver at this blend.</strong>
              )}
            </>
          )}
        </p>
      )}
    </fieldset>
  );
}

export interface LawEditorProps {
  label?: string;
  value: LawConfig | null;
  onChange(value: LawConfig | null): void;
  optional?: boolean;
}

const LAW_OPTIONS: ReadonlyArray<{ value: LawType; label: string }> = [
  { value: "open_loop", label: "Open loop (no feedback)" },
  { value: "P", label: "P — proportional" },
  { value: "PI", label: "PI — proportional + integral" },
  { value: "PID", label: "PID" },
];

export function LawEditor({ label = "Control law", value, onChange, optional }: LawEditorProps) {
  if (optional && value === null) {
    return (
      <fieldset>
        <legend>{label}</legend>
        <Toggle
          label="Use the rig's configured law"
          checked
          onChange={() => onChange({ ...DEFAULT_LAW })}
        />
      </fieldset>
    );
  }

  const law = value ?? { ...DEFAULT_LAW };
  const closed = law.type !== "open_loop";

  return (
    <fieldset>
      <legend>{label}</legend>
      {optional && (
        <Toggle label="Use the rig's configured law" checked={false} onChange={() => onChange(null)} />
      )}
      <div className="grid cols-2" style={{ gap: 8 }}>
        <SelectField<LawType>
          label="Law"
          value={law.type}
          options={LAW_OPTIONS}
          onChange={(type) => onChange({ ...law, type })}
        />
        {closed && (
          <NumberField
            label="kp"
            value={law.kp ?? 0}
            step={0.01}
            onChange={(kp) => onChange({ ...law, kp: kp ?? 0 })}
          />
        )}
        {closed && law.type !== "P" && (
          <NumberField
            label="ki"
            value={law.ki ?? 0}
            step={0.001}
            onChange={(ki) => onChange({ ...law, ki: ki ?? 0 })}
          />
        )}
        {law.type === "PID" && (
          <NumberField
            label="kd"
            value={law.kd ?? 0}
            step={0.001}
            onChange={(kd) => onChange({ ...law, kd: kd ?? 0 })}
          />
        )}
        {closed && law.type !== "P" && (
          <NumberField
            label="tt"
            value={law.tt ?? 0}
            step={0.1}
            help="Anti-windup tracking time. 0 disables tracking."
            onChange={(tt) => onChange({ ...law, tt: tt ?? 0 })}
          />
        )}
      </div>
      {law.type === "open_loop" && (
        <p className="hint" style={{ margin: "6px 0 0" }}>
          The blend is set from the target and the source humidities, with no correction from the
          reading.
        </p>
      )}
    </fieldset>
  );
}

export interface PaceEditorProps {
  value: Pace;
  onChange(value: Pace): void;
}

export function PaceEditor({ value, onChange }: PaceEditorProps) {
  return (
    <fieldset>
      <legend>Pace</legend>
      <div className="grid cols-2" style={{ gap: 8 }}>
        <SelectField<Pace["kind"]>
          label="Given as"
          value={value.kind}
          options={[
            { value: "rate", label: "A rate" },
            { value: "duration", label: "A total time" },
          ]}
          onChange={(kind) =>
            onChange(
              kind === "rate"
                ? { kind: "rate", value: 5, per: "minute" }
                : { kind: "duration", seconds: 300 },
            )
          }
        />
        {value.kind === "rate" ? (
          <>
            <NumberField
              label="Rate"
              value={value.value}
              min={0}
              step={0.1}
              unit="%"
              onChange={(next) => onChange({ ...value, value: next ?? 0 })}
            />
            <SelectField
              label="Per"
              value={value.per}
              options={TIME_UNITS.map((unit) => ({ value: unit.value, label: unit.label }))}
              onChange={(per) => onChange({ ...value, per })}
            />
          </>
        ) : (
          <NumberField
            label="Over"
            value={value.seconds}
            min={0}
            step={1}
            unit="s"
            onChange={(seconds) => onChange({ kind: "duration", seconds: seconds ?? 0 })}
          />
        )}
      </div>
    </fieldset>
  );
}

export { FLOW_SCALE_LABELS };
