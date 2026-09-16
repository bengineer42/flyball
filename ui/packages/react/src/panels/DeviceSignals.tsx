import { useCallback, useEffect, useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";
import { describeDevice, describeSignal, describeUnit, deviceOf, humanise, withUnit, isNamespace, publishes, signalsOf, writable, type Address, type ControllerOut, type DeviceOut, type NamespaceOut, type ReadOut, type SignalOut, type TreeNode } from "@flyball/client";
import { Ref } from "../links.js";
import { useRig, useTelemetry } from "../provider.js";
import { READOUT_MS, useDeviceRun, useSignal, useTraceRef, useWriteState, useWriteStates } from "../store/hooks.js";
import { Readout } from "./Readout.js";
import { DemandEntry, WritePanel, demandText } from "./WritePanel.js";

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

/** Whether the signal can be read at all (`r`); a `w`-only signal has no value to ask for. */
const readable = (signal: SignalOut) => signal.access.toLowerCase().includes("r");

/** A signal's name relative to its device, the key `PUT /api/devices/{name}/demand` takes. */
const relativeName = (signal: SignalOut) => signal.address.slice(deviceOf(signal.address).length + 1);

/** Every controller's latest state by name (the target's address), at most four times a second; seeded from `GET /api/controllers`. */
function useAttachedControllers(): Record<Address, ControllerOut> {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => store.subscribeController(null, cb, READOUT_MS), [store]);
  useSyncExternalStore(subscribe, () => store.controllerVersion());
  useEffect(() => {
    void store.seedControllers();
  }, [store]);
  return store.controllers();
}

/**
 * The value the rig last read from a signal that is read only when asked
 * (`r`/`rw`, never published): from the device's own read, refreshed
 * through `GET /api/read/{address}?fresh`, which asks the hardware again.
 */
function useOnDemand(signal: SignalOut) {
  const rig = useRig();
  const [latest, setLatest] = useState(signal.latest);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
  return { latest, busy, error, read };
}

/** The value part of an on-demand row: the last read, when it was, and the button that reads again. */
function OnDemandValue({ signal, latest, busy, read }: { signal: SignalOut } & Pick<ReturnType<typeof useOnDemand>, "latest" | "busy" | "read">) {
  const precision = signal.precision ?? 2;
  return (
    <>
      <span className="fb-num">{latest ? latest.value.toFixed(precision) : "—"}</span>
      {describeUnit(signal.unit) && <span className="fb-num-unit">{describeUnit(signal.unit)}</span>}
      <span className="fb-muted" title="A setting: the rig reads it only when asked, so its value is as of the last read.">
        {" "}
        · read on demand{latest ? ` · ${clock(latest.time_ns)}` : ""}
      </span>{" "}
      <button type="button" className="fb-tb" disabled={busy} onClick={() => void read()} title="Read the hardware now">
        Read now
      </button>
    </>
  );
}

/** A signal that is read only when asked and cannot be set (`r`): a setting the driver exposes but does not take. */
function OnDemand({ signal }: { signal: SignalOut }) {
  const { latest, busy, error, read } = useOnDemand(signal);
  return (
    <div className="fb-state-row">
      <dt title={`${signal.address} · read on demand, never published`}>
        <Ref kind="signal" name={signal.address}>{describeSignal(signal)}</Ref>
      </dt>
      <dd>
        <OnDemandValue signal={signal} latest={latest} busy={busy} read={read} />
        {error && <div className="fb-error">{error}</div>}
      </dd>
    </div>
  );
}

/**
 * A setting (`rw`: set, and read on demand, never published) as one row:
 * its last read value, when, Read now, then the entry to set it. A
 * successful Set reads the device again, so the value shown is what it
 * took, not what was asked.
 */
function Setting({ signal }: { signal: SignalOut }) {
  const rig = useRig();
  const { latest, busy, error, read } = useOnDemand(signal);
  return (
    <div className="fb-state-row">
      <dt title={`${signal.address} · a setting: read on demand, never published`}>
        <Ref kind="signal" name={signal.address}>{describeSignal(signal)}</Ref>
      </dt>
      <dd className="fb-write-cell">
        <div className="fb-setting">
          <span className="fb-setting-value">
            <OnDemandValue signal={signal} latest={latest} busy={busy} read={read} />
          </span>
          <WritePanel
            signal={signal}
            entryOnly
            bare
            onDemand={async (value) => {
              await rig.demand(signal.address, value);
              await read();
            }}
          />
        </div>
        {error && <div className="fb-error">{error}</div>}
      </dd>
    </div>
  );
}

/**
 * The writable signals of one level in the order the tree declares them,
 * each `together` group as one entry: the closure of a member's siblings
 * (a member lists the others; the walk follows every listing), so a group
 * is never set in part. A signal in no group is an entry of one.
 */
export function writeGroupsOf(signals: readonly SignalOut[]): [SignalOut, ...SignalOut[]][] {
  const byName = new Map(signals.map((s) => [s.name, s] as const));
  // Listing is one way on the wire; the group is the same whichever member lists which.
  const linked = new Map<SignalOut, Set<SignalOut>>(signals.map((s) => [s, new Set()]));
  for (const signal of signals) {
    for (const name of signal.together) {
      const sibling = byName.get(name);
      if (!sibling) continue;
      linked.get(signal)?.add(sibling);
      linked.get(sibling)?.add(signal);
    }
  }
  const seen = new Set<SignalOut>();
  const groups: [SignalOut, ...SignalOut[]][] = [];
  for (const signal of signals) {
    if (seen.has(signal)) continue;
    const members = new Set<SignalOut>();
    const queue = [signal];
    while (queue.length) {
      const next = queue.pop()!;
      if (members.has(next)) continue;
      members.add(next);
      seen.add(next);
      for (const sibling of linked.get(next) ?? []) if (!members.has(sibling)) queue.push(sibling);
    }
    groups.push([signal, ...signals.filter((s) => s !== signal && members.has(s))]);
  }
  return groups;
}

/**
 * What to call a `together` group: the part of the members' names they
 * share, when they share one. `dry_flow` + `wet_flow` share a trailing
 * `flow`, and a shared tail names the kind of thing, so it is pluralised
 * ("Flows"); `position_x` + `position_y` share a leading `position`, the
 * thing itself, kept singular ("Position"). Otherwise the members' names,
 * joined.
 */
export function writeGroupLabel(members: readonly SignalOut[]): string {
  const parts = members.map((m) => m.name.split(/[_-]+|(?<=[a-z0-9])(?=[A-Z])/).filter(Boolean));
  const shortest = Math.min(...parts.map((p) => p.length));
  const shared = (at: (p: string[], i: number) => string | undefined) => {
    const out: string[] = [];
    for (let i = 0; i < shortest - 1; i++) {
      const token = at(parts[0] ?? [], i);
      if (token !== undefined && parts.every((p) => at(p, i) === token)) out.push(token);
      else break;
    }
    return out;
  };
  const tail = shared((p, i) => p[p.length - 1 - i]).reverse();
  if (tail.length) {
    const label = humanise(tail.join(" "));
    return label.endsWith("s") ? label : `${label}s`;
  }
  const head = shared((p, i) => p[i]);
  if (head.length) return humanise(head.join(" "));
  return members.map(describeSignal).join(" · ");
}

/**
 * One member of a `together` group: its label, what it was last set to
 * (after limits; the readback until something has been set, for a
 * published one), and its entry -- or, while a controller drives the
 * group, no entry at all.
 */
function GroupMember({ signal, text, onText, disabled, locked }: { signal: SignalOut; text: string; onText(text: string): void; disabled: boolean; locked: boolean }) {
  const live = useWriteState(signal.address);
  const state = live ?? signal.write ?? null;
  const reading = useSignal(publishes(signal) ? signal.address : undefined);
  const precision = signal.precision ?? 2;
  const fmt = (value: number | null | undefined) => (value == null ? "—" : withUnit(value.toFixed(precision), signal.unit));
  const atLimit = state?.at_limit ?? null;
  const requested = atLimit && state?.requested != null && state.value != null && state.requested !== state.value ? state.requested : null;
  return (
    <span className="fb-write-member" title={signal.address}>
      <Ref kind="signal" name={signal.address}>{describeSignal(signal)}</Ref>
      {state?.value != null ? (
        <span className="fb-write-value" title={requested != null ? `what it was last set to, after limits; requested ${fmt(requested)}` : "what it was last set to, after limits"}>
          {fmt(state.value)}
          {atLimit && (
            <span className="fb-badge" style={{ background: "var(--fb-alarm-fill)", color: "var(--fb-alarm)" }} title={`Pinned to its ${atLimit} limit; requested ${fmt(state.requested)}`}>
              at limit
            </span>
          )}
        </span>
      ) : (
        <span className="fb-write-value fb-muted" title={reading ? "nothing set yet: the readback" : "nothing set yet"}>
          {reading ? fmt(reading.v) : "—"}
        </span>
      )}
      {!locked && <DemandEntry signal={signal} text={text} onText={onText} disabled={disabled} precision={precision} />}
    </span>
  );
}

/**
 * A `together` group as one control: an entry per member, one Set that
 * puts every value on the device as one demand
 * (`PUT /api/devices/{name}/demand`), which is how the rig takes a group
 * -- a lone member is refused. A member a controller drives locks the
 * whole group, since the rig refuses any demand on it: the row says which
 * controller and offers nothing to type into. `alternatives` are the
 * level's other writable signals: when a controller in an active mode
 * drives one of them, the hint says the controller keeps demanding it
 * after this is set, which is all the rig promises -- what the device
 * makes of the two is the device's.
 */
function WriteGroup({ members, alternatives }: { members: [SignalOut, ...SignalOut[]]; alternatives: SignalOut[] }) {
  const rig = useRig();
  const writes = useWriteStates();
  const controllers = useAttachedControllers();
  const device = deviceOf(members[0].address);
  const drivenBy = (s: SignalOut) => controllers[s.address]?.name ?? (writes[s.address] ?? s.write)?.controller ?? null;
  const [texts, setTexts] = useState<Record<Address, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const driven = members.map(drivenBy).find((d) => d !== null) ?? null;
  const addresses = members.map((m) => m.address).join(" ");
  // Each box is seeded once with what its signal was last set to (else its latest readback), and again after the group changes hands.
  useEffect(() => {
    setTexts(Object.fromEntries(members.map((m) => [m.address, demandText((writes[m.address] ?? m.write)?.value ?? m.latest?.value, m.precision ?? 2)])));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addresses, driven]);

  const values = members.map((m) => ({ member: m, text: texts[m.address] ?? "", value: Number(texts[m.address] ?? "") }));
  const complete = values.every(({ text, value }) => text.trim() !== "" && !Number.isNaN(value));
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!complete) return;
    setBusy(true);
    setError(null);
    try {
      await rig.demandNode(device, Object.fromEntries(values.map(({ member, value }) => [relativeName(member), value])));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  // A controller in an active mode on another writable signal of this level demands it again on every tick.
  const ticking = alternatives.map((s) => controllers[s.address]).filter((c): c is ControllerOut => c !== undefined && c.mode !== "manual");
  const label = writeGroupLabel(members);
  const names = members.map((m) => m.name).join(", ");
  return (
    <div className="fb-state-row">
      <dt title={`${names} · set together, as one demand on ${device}`}>{label}</dt>
      <dd className="fb-write-cell">
        <form className="fb-write fb-write-group" onSubmit={(e) => void submit(e)} title={driven ? `Controller ${driven} is attached: move its setpoint, or detach it to set these by hand.` : `Set ${names} as one demand on ${device}`}>
          {values.map(({ member, text }) => (
            <GroupMember key={member.address} signal={member} text={text} onText={(t) => setTexts((all) => ({ ...all, [member.address]: t }))} disabled={busy} locked={!!driven} />
          ))}
          {driven ? (
            <span className="fb-muted">
              driven by <Ref kind="controller" name={driven} />
            </span>
          ) : (
            <button type="submit" className="fb-signal-go" disabled={busy || !complete} title={complete ? `Set ${names} as one` : "Every member takes a value: the device sets them as one"}>
              Set
            </button>
          )}
          {!driven && ticking.length > 0 && (
            <span className="fb-write-hint">
              {ticking.map((c, i) => (
                <span key={c.name}>
                  {i > 0 && "; "}
                  takes effect now; controller <Ref kind="controller" name={c.name} /> goes on driving {describeSignal(alternatives.find((s) => s.address === c.name) ?? { name: c.name, label: "" })} and demands it again on its next tick
                </span>
              ))}
            </span>
          )}
          {error && <div className="fb-error fb-write-error">{error}</div>}
        </form>
      </dd>
    </div>
  );
}

/** A never-published signal that is set and not read (`w`): its write entry. */
function WriteOnly({ signal }: { signal: SignalOut }) {
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
 * One level of a device's tree: its readouts, then its settings and
 * writable signals as rows -- a `together` group as one row, an `rw`
 * setting as one row -- then each namespace as a group.
 */
function Level({ nodes, common }: { nodes: TreeNode[]; common: Common }) {
  const signals = nodes.filter((n): n is SignalOut => !isNamespace(n));
  const namespaces = nodes.filter(isNamespace);
  const published = signals.filter(publishes);
  const settable = signals.filter(writable);
  const onDemandOnly = signals.filter((s) => !publishes(s) && !writable(s) && readable(s));
  const groups = writeGroupsOf(settable);
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
      {(onDemandOnly.length > 0 || groups.length > 0) && (
        <dl className="fb-state">
          {onDemandOnly.map((s) => (
            <OnDemand key={s.address} signal={s} />
          ))}
          {groups.map(([signal, ...rest]) => {
            if (rest.length > 0) return <WriteGroup key={signal.address} members={[signal, ...rest]} alternatives={settable.filter((s) => s !== signal && !rest.includes(s))} />;
            if (!publishes(signal) && readable(signal)) return <Setting key={signal.address} signal={signal} />;
            return <WriteOnly key={signal.address} signal={signal} />;
          })}
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

/**
 * One device's signals, live: the tree with a `Readout` per publishing
 * signal (namespaces as groups), a write row for each writable one -- a
 * `together` group as one row with one Set, an `rw` setting as one row
 * with its read-now value -- and a read-now row for a setting that cannot
 * be set. The header carries the device, its driver and the runtime's
 * conditions on polling it. Store-fed through `useTraceRef`, so it needs
 * a `RigProvider` above it.
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
