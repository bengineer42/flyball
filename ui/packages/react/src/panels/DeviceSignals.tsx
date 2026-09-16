import { useState, type ReactNode } from "react";
import { describeDevice, describeSignal, humanise, isNamespace, publishes, signalsOf, writable, type DeviceOut, type NamespaceOut, type ReadOut, type SignalOut, type TreeNode } from "@flyball/client";
import { Ref } from "../links.js";
import { useRig } from "../provider.js";
import { useDeviceRun, useTraceRef } from "../store/hooks.js";
import { Readout } from "./Readout.js";
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
 * A signal that is read only when asked (`r`/`rw`, never published): a
 * setting, typically. Its last value comes from the device's own read, and
 * the button asks the hardware again through `GET /api/read/{address}?fresh`.
 */
function OnDemand({ signal }: { signal: SignalOut }) {
  const rig = useRig();
  const [latest, setLatest] = useState(signal.latest);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const precision = signal.precision ?? 2;
  const read = async () => {
    setBusy(true);
    setError(null);
    try {
      const result: ReadOut = await rig.read(signal.address, true);
      if ("reading" in result) setLatest({ time_ns: result.reading.time_ns, value: result.reading.value });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="fb-state-row">
      <dt title={`${signal.address} · read on demand, never published`}>
        <Ref kind="signal" name={signal.address}>{describeSignal(signal)}</Ref>
      </dt>
      <dd>
        <span className="fb-num">{latest ? latest.value.toFixed(precision) : "—"}</span>
        <span className="fb-num-unit">{signal.unit}</span>
        <span className="fb-muted" title="A setting: the rig reads it only when asked, so its value is as of the last read.">
          {" "}
          · read on demand{latest ? ` · ${clock(latest.time_ns)}` : ""}
        </span>{" "}
        <button type="button" className="fb-tb" disabled={busy} onClick={() => void read()} title="Read the hardware now">
          Read now
        </button>
        {error && <div className="fb-error">{error}</div>}
      </dd>
    </div>
  );
}

/** One level of a device's tree: its readouts, then its settings and writable signals, then each namespace as a group. */
function Level({ nodes, common }: { nodes: TreeNode[]; common: Common }) {
  const signals = nodes.filter((n): n is SignalOut => !isNamespace(n));
  const namespaces = nodes.filter(isNamespace);
  const published = signals.filter(publishes);
  const others = signals.filter((s) => !publishes(s));
  return (
    <>
      {published.length > 0 && (
        <div className="fb-readouts" style={GRID}>
          {/* Each tile in its own cell: a `PanelFrame` is a `<section>`, and an app's `section + section` spacing rule must not stagger the grid. */}
          {published.map((s) => (
            <div key={s.address} style={{ minWidth: 0 }}>
              <Readout signal={s} source={common.live} sparkline={common.sparkline} showDevice={false} windowS={common.windowS} every={common.every} exportHref={common.exportHref?.(s)} />
            </div>
          ))}
        </div>
      )}
      {others.length > 0 && (
        <dl className="fb-state">
          {others.map((s) => (
            <OnDemandOrWrite key={s.address} signal={s} />
          ))}
        </dl>
      )}
      {namespaces.map((ns) => (
        <Namespace key={ns.address} namespace={ns} common={common} />
      ))}
    </>
  );
}

/** A never-published signal: a read-on-demand row for what can be read, a write entry for what can be set. */
function OnDemandOrWrite({ signal }: { signal: SignalOut }) {
  const readable = signal.access.toLowerCase().includes("r");
  return (
    <>
      {readable && <OnDemand signal={signal} />}
      {writable(signal) && (
        <div className="fb-state-row">
          <dt title={signal.address}>{readable ? "" : <Ref kind="signal" name={signal.address}>{describeSignal(signal)}</Ref>}</dt>
          <dd>
            <WritePanel signal={signal} compact bare />
          </dd>
        </div>
      )}
    </>
  );
}

function Namespace({ namespace, common }: { namespace: NamespaceOut; common: Common }) {
  return (
    <section className="fb-section fb-section-static fb-namespace">
      <h4 className="fb-section-title" title={namespace.address}>
        {namespace.label || humanise(namespace.name)}
        {namespace.atomic && (
          <span className="fb-muted" title="Read and written as one sample: every signal here shares a timestamp">
            {" "}
            · as one
          </span>
        )}
      </h4>
      <Level nodes={namespace.signals} common={common} />
    </section>
  );
}

/**
 * One device's signals, live: the tree with a `Readout` per publishing
 * signal (namespaces as groups), a `WritePanel` beside each writable one,
 * and a read-now row for a setting. The header carries the device, its
 * driver and the runtime's conditions on polling it. Store-fed through
 * `useTraceRef`, so it needs a `RigProvider` above it.
 */
export function DeviceSignals({ device, sparkline = true, windowS, controls, every, exportHref, bare = false }: DeviceSignalsProps) {
  const all = signalsOf(device.signals);
  const live = useTraceRef(all.filter(publishes).map((s) => s.address));
  const run = useDeviceRun(device.name);
  const conditions = run?.conditions ?? device.conditions;
  const lastReadNs = run?.last_read_ns ?? device.run?.last_read_ns ?? null;
  const common: Common = { live, sparkline, windowS, every, exportHref };
  const tree = <Level nodes={device.signals} common={common} />;
  if (bare) return <div className="fb-source fb-source-bare">{tree}</div>;
  return (
    <article className="fb-panel fb-source">
      <header className="fb-source-head">
        <h3><Ref kind="device" name={device.name}>{device.label ?? device.name}</Ref></h3>
        <span className="fb-muted">
          {device.label && `${device.name} · `}
          {describeDevice(device.driver ?? device.type)} · {all.length} signal{all.length === 1 ? "" : "s"}
          {lastReadNs !== null && ` · last read ${clock(lastReadNs)}`}
        </span>
        {conditions.length > 0 && (
          <span className="fb-conditions">
            {conditions.map((c) => (
              <span key={c.kind} className={`fb-condition fb-level-${c.level}`} title={c.message}>
                {c.kind}
              </span>
            ))}
          </span>
        )}
        {controls && <span className="fb-source-controls">{controls}</span>}
      </header>
      {tree}
    </article>
  );
}
