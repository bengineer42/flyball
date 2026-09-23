import { useState, type ReactNode } from "react";
import { describeDevice, describeSignal, formatValue, humanise, isNamespace, isNumeric, publishes, readable, signalsOf, type Address, type DeviceOut, type NamespaceOut, type SignalOut, type TreeNode, isHousekeeping } from "@flyball/client";
import { Ref } from "../links.js";
import { useDeviceRun, useLatestValue, useTraceRef } from "../store/hooks.js";
import { Readout } from "./Readout.js";
import { ValueView } from "./ValueView.js";
import { WritePanel } from "./WritePanel.js";

export interface DeviceSignalsProps {
  device: DeviceOut;
  /** A sparkline under each publishing signal's value; default on. */
  sparkline?: boolean;
  /** Seconds each sparkline spans; scrolls once full. */
  windowS?: number;
  /** Rendered at the end of the header: a window selector, for instance. */
  controls?: ReactNode;
  /** Draw one point in `every` on the sparklines. */
  every?: number;
  /** Where the store holds a signal, as an export URL; each sparkline's download menu offers it. */
  exportHref?(signal: SignalOut): string | undefined;
  /** No outer card and no header: for a caller that already draws its own frame. */
  bare?: boolean;
}

type Common = Pick<DeviceSignalsProps, "sparkline" | "windowS" | "every" | "exportHref"> & { live: ReturnType<typeof useTraceRef> };

/** The readouts of one level are laid out as a wrapping grid; the panel owns no stylesheet rule for it. */
const GRID = { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(13rem, 100%), 1fr))", gap: "var(--fb-gap)" } as const;

/** Time of day of a rig timestamp. */
const clock = (ns: number) => new Date(ns / 1e6).toLocaleTimeString();

/**
 * The `mode` output (if the driver declared one) and the addresses of the
 * housekeeping signals every device now carries (`mode`, `conditions`) --
 * shown once in the header, not again as ordinary rows.
 */
function specialSignals(device: DeviceOut): { mode?: SignalOut; excluded: Set<Address> } {
  const excluded = new Set<Address>();
  let mode: SignalOut | undefined;
  for (const node of device.signals) {
    if (isNamespace(node)) continue;
    if (node.name === "conditions") excluded.add(node.address);
    else if (node.name === "mode") {
      mode = node;
      excluded.add(node.address);
    }
  }
  return { mode, excluded };
}

/** The device's `mode` output, live, as a prominent chip. */
function ModeChip({ signal }: { signal: SignalOut }) {
  const live = useLatestValue(signal.address);
  const value = live?.value ?? signal.latest?.value ?? signal.initial;
  if (value === null || value === undefined) return null;
  return (
    <span className="fb-badge fb-mode" title={`${describeSignal(signal)} · ${signal.address}`}>
      {String(value)}
    </span>
  );
}

/** What the device follows, one line per bound input: `dry ← hum_sensors.dry.humidity`. */
function InputsLine({ device }: { device: DeviceOut }) {
  const bound = Object.values(device.inputs).filter((i) => i.bound);
  if (!bound.length) return null;
  return (
    <div className="fb-device-inputs">
      {bound.map((i) => (
        <span key={i.name} className="fb-muted" title={`${i.label || humanise(i.name)} follows ${i.bound}`}>
          {i.label || humanise(i.name)} ← <Ref kind="signal" name={i.bound!}>{i.bound}</Ref>
        </span>
      ))}
    </div>
  );
}

/**
 * A signal's value as one row: its live value (published) or its latest
 * known one (a config, fixed at build) formatted by dtype -- a chip for a
 * bool/enum/str, a compact block for json, a number with its unit
 * otherwise. Config rows are muted.
 */
function ValueRow({ signal }: { signal: SignalOut }) {
  const live = useLatestValue(publishes(signal) ? signal.address : undefined);
  const value = live?.value ?? signal.latest?.value ?? signal.initial ?? null;
  return (
    <div className="fb-state-row">
      <dt title={signal.address}>
        <Ref kind="signal" name={signal.address}>{describeSignal(signal)}</Ref>
      </dt>
      <dd className={signal.role === "config" ? "fb-muted" : undefined}>{signal.dtype === "json" ? <ValueView value={value} /> : formatValue(value, signal)}</dd>
    </div>
  );
}

/** A demand: its readback and write entry (requested, at limit, driving controller), no grouping any more. */
function DemandRow({ signal }: { signal: SignalOut }) {
  return (
    <div className="fb-state-row">
      <dt title={signal.address}>
        <Ref kind="signal" name={signal.address}>{describeSignal(signal)}</Ref>
      </dt>
      <dd className="fb-write-cell">
        <WritePanel signal={signal} compact bare />
      </dd>
    </div>
  );
}

/**
 * One level of a device's tree: numeric published signals (`output`/
 * `setting`) as readout tiles; a non-numeric one as a chip row; a `demand`
 * as a write row; a `config` muted; then each namespace as a group. `last`
 * (one json signal per command) is shown beside its command, not here.
 */
function Level({ nodes, common }: { nodes: TreeNode[]; common: Common }) {
  const signals = nodes.filter((n): n is SignalOut => !isNamespace(n));
  const namespaces = nodes.filter(isNamespace).filter((n) => n.name !== "last");
  const demands = signals.filter((s) => s.role === "demand");
  const muted = signals.filter((s) => s.role === "config" || (s.role !== "demand" && !publishes(s) && readable(s)));
  const values = signals.filter((s) => s.role !== "demand" && s.role !== "config" && publishes(s));
  const numericValues = values.filter((s) => isNumeric(s.dtype));
  const chipValues = values.filter((s) => !isNumeric(s.dtype));
  return (
    <>
      {numericValues.length > 0 && (
        <div className="fb-readouts" style={GRID}>
          {/* Each tile in its own cell: a `PanelFrame` is a `<section>`, and an app's `section + section` spacing rule must not stagger the grid. */}
          {numericValues.map((s) => (
            <div key={s.address} style={{ minWidth: 0 }}>
              <Readout signal={s} source={common.live} sparkline={common.sparkline} showDevice={false} windowS={common.windowS} every={common.every} exportHref={common.exportHref?.(s)} />
            </div>
          ))}
        </div>
      )}
      {(chipValues.length > 0 || demands.length > 0 || muted.length > 0) && (
        <dl className="fb-state">
          {chipValues.map((s) => (
            <ValueRow key={s.address} signal={s} />
          ))}
          {demands.map((s) => (
            <DemandRow key={s.address} signal={s} />
          ))}
          {muted.map((s) => (
            <ValueRow key={s.address} signal={s} />
          ))}
        </dl>
      )}
      {namespaces.map((ns) => (
        <Namespace key={ns.address} namespace={ns} common={common} />
      ))}
    </>
  );
}

function Namespace({ namespace, common }: { namespace: NamespaceOut; common: Common }) {
  return (
    <section className="fb-section fb-section-static fb-namespace">
      {/* Atomic (read as one sample, every signal sharing a timestamp) is a hover hint, not text: it says nothing to an operator. */}
      <h4 className="fb-section-title" title={namespace.atomic ? `${namespace.address} · read as one sample` : namespace.address}>
        {namespace.label || humanise(namespace.name)}
      </h4>
      <Level nodes={namespace.signals} common={common} />
    </section>
  );
}

/** The axis name of the device's second grouping (`tags`), when any signal has one; undefined without. */
function tagAxis(device: DeviceOut): string | undefined {
  for (const s of signalsOf(device.signals)) {
    const [axis] = Object.keys(s.tags);
    if (axis) return axis;
  }
  return undefined;
}

/** Every signal (namespaces flattened) grouped by its `tags[axis]` value instead of by namespace. */
function TagPivot({ device, axis, excluded, common }: { device: DeviceOut; axis: string; excluded: Set<Address>; common: Common }) {
  const groups = new Map<string, SignalOut[]>();
  for (const s of signalsOf(device.signals)) {
    if (excluded.has(s.address)) continue;
    const key = s.tags[axis] ?? "—";
    (groups.get(key) ?? groups.set(key, []).get(key)!).push(s);
  }
  return (
    <>
      {[...groups.entries()].map(([key, signals]) => (
        <section key={key} className="fb-section fb-section-static fb-namespace">
          <h4 className="fb-section-title" title={`${axis}: ${key}`}>{humanise(key)}</h4>
          <Level nodes={signals} common={common} />
        </section>
      ))}
    </>
  );
}

/**
 * One device's signals, live: numeric readouts, value chips for anything
 * else, a write row per demand, config muted -- grouped by namespace, or,
 * when the device has a second grouping (`tags`), pivotable onto that axis
 * instead. The header carries the device, its driver, its `mode` and the
 * runtime's conditions on polling it. Store-fed through `useTraceRef`, so
 * it needs a `RigProvider` above it.
 */
export function DeviceSignals({ device, sparkline = true, windowS, controls, every, exportHref, bare = false }: DeviceSignalsProps) {
  const all = signalsOf(device.signals).filter((s) => !isHousekeeping(s)); // the count a person would give: readings and demands
  const live = useTraceRef(all.filter(publishes).map((s) => s.address));
  const run = useDeviceRun(device.name);
  const conditions = run?.conditions ?? device.conditions;
  const lastReadNs = run?.last_read_ns ?? device.run?.last_read_ns ?? null;
  const common: Common = { live, sparkline, windowS, every, exportHref };
  const { mode, excluded } = specialSignals(device);
  const axis = tagAxis(device);
  const [pivot, setPivot] = useState(false);
  const topNodes = device.signals.filter((n) => (isNamespace(n) ? n.name !== "last" : !excluded.has(n.address)));
  const tree = pivot && axis ? <TagPivot device={device} axis={axis} excluded={excluded} common={common} /> : <Level nodes={topNodes} common={common} />;
  const body = (
    <>
      <InputsLine device={device} />
      {tree}
    </>
  );
  if (bare) return <div className="fb-source fb-source-bare">{body}</div>;
  return (
    <article className="fb-panel fb-source">
      <header className="fb-source-head">
        <h3><Ref kind="device" name={device.name}>{device.label ?? device.name}</Ref></h3>
        <span className="fb-muted">
          {device.label && `${device.name} · `}
          {describeDevice(device.driver ?? device.class_name)} · {all.length} signal{all.length === 1 ? "" : "s"}
          {lastReadNs !== null && ` · last read ${clock(lastReadNs)}`}
        </span>
        {mode && <ModeChip signal={mode} />}
        {conditions.length > 0 && (
          <span className="fb-conditions">
            {conditions.map((c) => (
              <span key={c.code} className={`fb-condition fb-severity-${c.severity}`} title={c.message}>
                {c.code}
              </span>
            ))}
          </span>
        )}
        {/* Anchored to the row's end regardless of what the conditions span above does --
            the device's live condition count grows/shrinks while this stays put. */}
        {(axis || controls) && (
          <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "0.5rem" }}>
            {axis && (
              <button type="button" className="fb-tb" onClick={() => setPivot((p) => !p)} title={`Group by ${axis} instead of the signal tree`}>
                {pivot ? "by tree" : `by ${axis}`}
              </button>
            )}
            {controls && <span className="fb-source-controls">{controls}</span>}
          </span>
        )}
      </header>
      {body}
    </article>
  );
}
