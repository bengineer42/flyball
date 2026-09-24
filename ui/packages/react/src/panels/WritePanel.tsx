import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { describeSignal, describeUnit, deviceOf, publishes, verbLabel, withUnit, type SignalOut, type WriteOut, fixed } from "@flyball/client";
import { Ref } from "../links.js";
import { useRig } from "../provider.js";
import { useController, useSignal, useWriteState } from "../store/hooks.js";
import { PanelFrame } from "./PanelFrame.js";
import { errorText } from "./CommandForm.js";

export interface WritePanelProps {
  /** The writable signal: its unit, limits, precision and label. */
  signal: SignalOut;
  /** Its write state until the store has one (`signal.write` as `GET /api/devices` gave it, say). */
  write?: WriteOut | null;
  /**
   * What "Set" does with the entered value, in the signal's unit. Omitted,
   * the panel puts the demand itself through `PUT /api/signals/{address}`.
   */
  onDemand?(value: number): Promise<unknown>;
  /** One line: value and entry, no caption row; for a row inside a device's tree. */
  compact?: boolean;
  /** The entry, its note and errors only, no committed value: for a row that shows the signal's value already (a setting read on demand). Implies `compact`. */
  entryOnly?: boolean;
  /** Body only, no `PanelFrame`: for a caller that draws its own frame. */
  bare?: boolean;
  /** Decimal places; default the signal's `precision`, else 2. */
  precision?: number;
  /** The frame's heading; default the signal's label. */
  title?: ReactNode;
  /** This browser may drive the rig (the server's `AuthState.canOperate`); default true. False disables the entry and Set button, greyed out but still visible -- a proactive echo of the 401 the server would otherwise give. */
  canOperate?: boolean;
}

/** A value's fraction of `limits`, clamped to [0, 1]; null with no value or no limits. */
const fractionOf = (value: number | null, limits: [number, number] | null): number | null =>
  value == null || !limits ? null : Math.min(1, Math.max(0, (value - limits[0]) / (limits[1] - limits[0])));

/** A number as an entry box shows it: at the signal's precision, trailing zeros dropped. */
export const demandText = (value: number | null | undefined, precision: number): string => (value == null ? "" : String(Number(value.toFixed(precision))));

export interface DemandEntryProps {
  /** The signal the value is for: its `limits` bound the slider, its unit labels the box. */
  signal: SignalOut;
  /** The box's text, owned by the caller (a group holds one per member). */
  text: string;
  onText(text: string): void;
  disabled?: boolean;
  /** Decimal places the slider steps in; default the signal's `precision`, else 2. */
  precision?: number;
}

/**
 * The entry for one demand: with `limits`, a slider spanning exactly them,
 * the limits at its ends, and a number box in step with it (the schema
 * form's `slider` widget, by look); without, the box alone. Neither
 * submits: the caller's form does.
 */
export function DemandEntry({ signal, text, onText, disabled = false, precision: precisionProp }: DemandEntryProps) {
  const precision = precisionProp ?? signal.precision ?? 2;
  const step = 10 ** -precision;
  const limits = signal.limits;
  const unit = describeUnit(signal.unit);
  const label = `${describeSignal(signal)} demand`;
  const box = (
    <input
      type="number"
      step={step}
      min={limits?.[0]}
      max={limits?.[1]}
      value={text}
      disabled={disabled}
      onChange={(e) => onText(e.target.value)}
      aria-label={label}
    />
  );
  if (!limits) {
    return (
      <span className="fb-unit-input fb-demand">
        {box}
        {unit && <span className="fb-unit">{unit}</span>}
      </span>
    );
  }
  const value = Number(text);
  return (
    <span className="fb-unit-input fb-slider fb-demand fb-demand-bounded" title={withUnit(`${limits[0]} – ${limits[1]}`, signal.unit)}>
      <span className="fb-demand-limit">{limits[0]}</span>
      <input
        type="range"
        className="fb-slider-track"
        value={text.trim() === "" || Number.isNaN(value) ? limits[0] : value}
        min={limits[0]}
        max={limits[1]}
        step={step}
        disabled={disabled}
        aria-label={`${label} slider`}
        aria-valuetext={text ? withUnit(text, signal.unit) : ""}
        onChange={(e) => onText(e.target.value)}
      />
      <span className="fb-demand-limit">{limits[1]}</span>
      {box}
      {unit && <span className="fb-unit">{unit}</span>}
    </span>
  );
}

/**
 * One writable signal: what it was last set to (after limits), the raw
 * request when the clamp changed it, and an entry to set it -- a slider
 * over its limits when it has them, a box otherwise. While a controller is
 * attached to it, in any mode, there is nothing to type into: the rig
 * refuses a manual demand, so the row says which controller instead
 * (move its setpoint, or detach it). A refused write shows the rig's reason
 * word for word: a readback's names the command that moves it. Live through
 * `useWriteState` and `useController`; the frame carries the label and
 * the device.
 */
export function WritePanel({ signal, write: given, onDemand, compact: compactProp = false, entryOnly = false, bare = false, precision: precisionProp, title, canOperate = true }: WritePanelProps) {
  const compact = compactProp || entryOnly;
  const rig = useRig();
  const live = useWriteState(signal.address);
  const write = live ?? given ?? signal.write ?? null;
  // A controller is named by its output, so the store's entry under this address is the one attached here.
  const attached = useController(signal.address);
  // Until something has been set, the last reading stands in for the set value: a published signal's live readback, else the last read the rig has.
  const liveReading = useSignal(publishes(signal) ? signal.address : undefined);
  const reading = liveReading ?? (signal.latest && typeof signal.latest.value === "number" ? { t: signal.latest.time_ns / 1e9, v: signal.latest.value } : undefined);
  const precision = precisionProp ?? signal.precision ?? 2;
  const fmt = (value: number | null | undefined) =>
    typeof value === "number" && Number.isFinite(value) ? withUnit(fixed(value, precision), signal.unit) : "—";
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The box is seeded with the committed value (else the last reading) once it is known -- which may be a
  // moment after mount, when the store has caught up -- and again after the signal changes hands. Typing
  // afterwards is never overwritten by a readback.
  const seedValue = write?.value ?? reading?.v;
  const seededFor = useRef<string | null>(null);
  useEffect(() => {
    const key = `${signal.address}|${write?.controller ?? ""}`;
    if (seededFor.current === key || seedValue == null) return;
    seededFor.current = key;
    setText(demandText(seedValue, precision));
  }, [signal.address, write?.controller, seedValue, precision]);

  const driven = attached?.name ?? write?.controller ?? null;
  const limits = signal.limits;
  const atLimit = write?.at_limit ?? null;
  const requested = atLimit && write?.requested != null && write.value != null && write.requested !== write.value ? write.requested : null;
  const fraction = fractionOf(write?.value ?? null, limits);
  const edge = atLimit === "high" ? "hi" : atLimit === "low" ? "lo" : null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const value = Number(text);
    if (!text.trim() || Number.isNaN(value)) return;
    setBusy(true);
    setError(null);
    try {
      await (onDemand ? onDemand(value) : rig.write(signal.address, value));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const drivenNote = driven && (
    <span className="fb-muted" title={`Controller ${driven} is attached (${attached?.mode ?? "regulating"}): move its setpoint, or detach it to set this by hand.`}>
      driven by <Ref kind="controller" name={driven} />
    </span>
  );
  // A controller's output has nothing to type into: it says which controller instead.
  const entry = driven ? (
    drivenNote
  ) : (
    <form className="fb-write-entry fb-unit-input" onSubmit={(e) => void submit(e)} title={withUnit(`${verbLabel("Set", describeSignal(signal))}, in`, signal.unit)}>
      <DemandEntry signal={signal} text={text} onText={setText} disabled={busy || !canOperate} precision={precision} />
      <button type="submit" className="fb-signal-go" disabled={busy || !canOperate || !text.trim()}>
        Set
      </button>
    </form>
  );

  const badge = atLimit && (
    <span className="fb-badge" style={{ background: "var(--fb-alarm-fill)", color: "var(--fb-alarm)" }} title={`Pinned to its ${atLimit} limit; requested ${fmt(write?.requested)}`}>
      at limit
    </span>
  );
  const bar = fraction !== null && (
    <div className={`fb-range${edge ? ` fb-range-limit-${edge}` : ""}`} title={edge ? `at its ${atLimit} limit; requested ${fmt(write?.requested)}` : withUnit(`${limits![0]} – ${limits![1]}`, signal.unit)}>
      <div className="fb-range-fill" style={{ width: `${fraction * 100}%` }} />
    </div>
  );
  const note = requested != null ? `requested ${fmt(requested)}` : null;
  const committed =
    write?.value != null ? (
      <span className="fb-write-value" style={atLimit ? { color: "var(--fb-alarm)" } : undefined} title="what the signal was last set to, after limits">
        {fmt(write.value)} {badge}
      </span>
    ) : reading ? (
      <span className="fb-write-value fb-muted" title="nothing set yet: the last reading">
        {fmt(reading.v)}
      </span>
    ) : (
      <span className="fb-write-value" title="nothing set yet">
        —
      </span>
    );
  // One row inside a device's tree: the set value beside its entry, so the two read as one thing.
  const body = compact ? (
    <div className="fb-write fb-write-compact">
      {!entryOnly && committed}
      {entry}
      {/* Reserved even when there's nothing to say, matching the full (non-compact) row below --
          an appearing/disappearing caption in this flex row pushes the entry beside it around. */}
      <span className="fb-muted">{note ?? " "}</span>
      {error && <div className="fb-error fb-write-error">{error}</div>}
    </div>
  ) : (
    <div className="fb-write">
      <dl className="fb-loop-rows">
        <div className="fb-loop-row">
          <dt title="what the signal was last set to, after limits">Value</dt>
          <dd>{committed}</dd>
          {bar || <span />}
          <span className="fb-loop-caption">{note ?? " "}</span>
        </div>
        <div className="fb-loop-row">
          <dt title={driven ? "who sets it" : "a new value, in the signal's unit"}>Set</dt>
          <dd>{entry}</dd>
        </div>
      </dl>
      {error && <div className="fb-error">{error}</div>}
    </div>
  );
  if (bare) return body;
  return (
    <PanelFrame
      className="fb-write-panel"
      title={title ?? <Ref kind="signal" name={signal.address}>{describeSignal(signal)}</Ref>}
      subtitle={
        <>
          <Ref kind="device" name={deviceOf(signal.address)} />
          {limits && ` · ${withUnit(`${limits[0]} – ${limits[1]}`, signal.unit)}`}
        </>
      }
      status={driven ? <span className={`fb-badge fb-mode-${attached?.mode ?? "regulating"}`} title={`Controller ${driven} is attached (${attached?.mode ?? "regulating"})`}>{attached?.mode === "manual" ? "controller attached" : "driven"}</span> : undefined}
    >
      {body}
    </PanelFrame>
  );
}
