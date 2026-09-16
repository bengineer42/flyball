import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { describeSignal, deviceOf, type SignalOut, type WriteOut } from "@flyball/client";
import { Ref } from "../links.js";
import { useRig } from "../provider.js";
import { useController, useWriteState } from "../store/hooks.js";
import { PanelFrame } from "./PanelFrame.js";

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
  /** Body only, no `PanelFrame`: for a caller that draws its own frame. */
  bare?: boolean;
  /** Decimal places; default the signal's `precision`, else 2. */
  precision?: number;
  /** The frame's heading; default the signal's label. */
  title?: ReactNode;
}

/** A value's fraction of `limits`, clamped to [0, 1]; null with no value or no limits. */
const fractionOf = (value: number | null, limits: [number, number] | null): number | null =>
  value == null || !limits ? null : Math.min(1, Math.max(0, (value - limits[0]) / (limits[1] - limits[0])));

/**
 * One writable signal: what it was last set to (after limits), the raw
 * request when the clamp changed it, and an entry box to set it -- refused
 * while a controller is attached to it, in any mode (the box says which:
 * move its setpoint, or detach it). Live through `useWriteState` and
 * `useController`; the frame carries the label and the device.
 */
export function WritePanel({ signal, write: given, onDemand, compact = false, bare = false, precision: precisionProp, title }: WritePanelProps) {
  const rig = useRig();
  const live = useWriteState(signal.address);
  const write = live ?? given ?? signal.write ?? null;
  // A controller is named by its target, so the store's entry under this address is the one attached here.
  const attached = useController(signal.address);
  const precision = precisionProp ?? signal.precision ?? 2;
  const fmt = (value: number | null | undefined) => (value == null ? "—" : `${value.toFixed(precision)} ${signal.unit}`);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The box is seeded with the committed value once, and again after the signal changes hands.
  useEffect(() => {
    setText(write?.value == null ? "" : String(Number(write.value.toFixed(precision))));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signal.address, write?.controller]);

  const driven = attached?.name ?? write?.controller ?? null;
  const together = signal.together.length ? signal.together : null;
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
      await (onDemand ? onDemand(value) : rig.demand(signal.address, value));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const entry = (
    <form className="fb-write-entry fb-unit-input" onSubmit={(e) => void submit(e)} title={driven ? `Controller ${driven} is attached: move its setpoint, or detach it to set this by hand.` : together ? `Set with ${together.join(", ")} as one demand on the device.` : `Set ${describeSignal(signal)}, in ${signal.unit}`}>
      <input
        type="number"
        step="any"
        min={limits?.[0]}
        max={limits?.[1]}
        value={text}
        style={compact ? { width: "5.5rem" } : undefined}
        disabled={busy || !!driven || !!together}
        onChange={(e) => setText(e.target.value)}
        aria-label={`${describeSignal(signal)} demand`}
      />
      <span className="fb-unit">{signal.unit}</span>
      <button type="submit" className="fb-signal-go" disabled={busy || !!driven || !!together || !text.trim()}>
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
    <div className={`fb-range${edge ? ` fb-range-limit-${edge}` : ""}`} style={compact ? { flex: "1 1 5rem", minWidth: "3rem" } : undefined} title={edge ? `at its ${atLimit} limit; requested ${fmt(write?.requested)}` : `${limits![0]} – ${limits![1]} ${signal.unit}`}>
      <div className="fb-range-fill" style={{ width: `${fraction * 100}%` }} />
    </div>
  );
  const note = requested != null ? `requested ${fmt(requested)}` : driven ? <>driven by <Ref kind="controller" name={driven} /></> : together ? `set with ${together.join(", ")}` : null;
  // One wrapping line for a row inside a device's tree: the panel owns no stylesheet rule, so the layout is inline.
  const body = compact ? (
    <div className="fb-write fb-write-compact" style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.3rem 0.6rem", minWidth: 0 }}>
      <span style={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }} title="what the signal was last set to, after limits">
        {fmt(write?.value)} {badge}
      </span>
      {bar}
      {entry}
      {note && <span className="fb-muted">{note}</span>}
      {error && <div className="fb-error" style={{ flexBasis: "100%" }}>{error}</div>}
    </div>
  ) : (
    <div className="fb-write">
      <dl className="fb-loop-rows">
        <div className="fb-loop-row">
          <dt title="what the signal was last set to, after limits">Value</dt>
          <dd>
            {fmt(write?.value)}
            {badge}
          </dd>
          {bar || <span />}
          <span className="fb-loop-caption">{note ?? " "}</span>
        </div>
        <div className="fb-loop-row">
          <dt title="a new value, in the signal's unit">Set</dt>
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
          {limits && ` · ${limits[0]} – ${limits[1]} ${signal.unit}`}
        </>
      }
      status={driven ? <span className={`fb-badge fb-mode-${attached?.mode ?? "regulating"}`} title={`Controller ${driven} is attached (${attached?.mode ?? "regulating"})`}>{attached?.mode === "manual" ? "controller attached" : "driven"}</span> : undefined}
    >
      {body}
    </PanelFrame>
  );
}
