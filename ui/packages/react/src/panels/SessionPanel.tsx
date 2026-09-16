import type { SessionDetail, SessionTrace } from "../hooks/useSession.js";
import { useState } from "react";
import { describeDevice, describeEventKind, describeStateKey, describeSubject, type SignalOut, type SignalRow } from "@flyball/client";
import { MultiSeries, type MultiSeriesTrace } from "./MultiSeries.js";
import { TimeSeries } from "./TimeSeries.js";
import type { YScale } from "./yscale.js";
import { ValueView } from "./ValueView.js";
import { Ref } from "../links.js";

/** A device's recorded config, if any, as a plain object minus what the row already says. */
function deviceConfig(config: unknown): { label: string | null; rest: Record<string, unknown> } {
  if (!config || typeof config !== "object") return { label: null, rest: {} };
  const { label, tag: _tag, name: _name, driver: _driver, ...rest } = config as Record<string, unknown>;
  return { label: typeof label === "string" ? label : null, rest };
}

/** A recorded signal as the charts take one: the row's metadata, with `together` (not recorded) empty and no live values. */
function asSignal(row: SignalRow): SignalOut {
  return {
    name: row.address.slice(row.address.lastIndexOf(".") + 1),
    address: row.address,
    access: row.access,
    label: row.label ?? "",
    quantity: row.quantity,
    unit: row.unit,
    dimension: null,
    dtype: row.dtype,
    shape: row.shape,
    range: row.range,
    precision: row.precision,
    warn: row.warn,
    alarm: row.alarm,
    poll_s: null,
    limits: row.limits,
    together: [],
    latest: null,
    write: null,
  };
}

/** A law config (`{tag, kp, ki, tt, ...}`) as `PI` plus a short `kp 100 · ki 0.15 · tt 30 s` line: the gains as the field names a controls engineer already knows, a unit only where one is fixed. */
function lawSummary(config: unknown): { tag: string | null; gains: string } {
  if (!config || typeof config !== "object") return { tag: null, gains: "" };
  const { tag, ...gains } = config as Record<string, unknown>;
  const gains_ = Object.entries(gains)
    .filter(([, v]) => v !== null && v !== undefined && typeof v !== "object")
    .map(([k, v]) => `${k} ${String(v)}${k === "tt" ? " s" : ""}`)
    .join(" · ");
  return { tag: typeof tag === "string" ? tag : null, gains: gains_ };
}

/** `recorder.py`'s `event()` wraps the level, scope and message around the emitter's own `details`. */
const LEVEL_NAME: Record<number, string> = { 10: "DEBUG", 20: "INFO", 30: "WARNING", 40: "ERROR" };
function storedEvent(detail: unknown): { level?: string; message?: string; details: unknown } {
  if (!detail || typeof detail !== "object" || !("message" in detail)) return { details: detail };
  const { level, message, details } = detail as { level?: number; message?: string; details?: unknown };
  return { level: typeof level === "number" ? LEVEL_NAME[level] : undefined, message, details };
}

export type SessionGrouping = "unit" | "signal";

/** What the store will send this session as. */
export type SessionDownload = "csv" | "json";

/**
 * Where the store keeps this session's tables, as export URLs (the app's
 * client builds them). Given, every signal, write, controller and the
 * events get a link to their own file, and each chart's download menu
 * offers the stored copy beside what the browser is holding.
 */
export interface SessionExports {
  series(address: string, format: SessionDownload): string;
  writes(address: string, format: SessionDownload): string;
  ticks(controller: string, format: SessionDownload): string;
  events(format: SessionDownload): string;
}

/** One table as a file, in either format: two small links beside whatever they belong to. */
function Download({ what, href }: { what: string; href(format: SessionDownload): string }) {
  return (
    <span className="fb-download">
      {(["csv", "json"] as const).map((format) => (
        <a key={format} className="fb-tb" href={href(format)} download title={`Download ${what} as ${format.toUpperCase()}`}>
          {format === "csv" ? "⭳ csv" : "json"}
        </a>
      ))}
    </span>
  );
}

export interface SessionPanelProps {
  detail: SessionDetail;
  /** Chart height per signal. */
  height?: number;
  /** One chart per unit (default), or one per signal. Uncontrolled unless given. */
  grouping?: SessionGrouping;
  onGrouping?(grouping: SessionGrouping): void;
  /** y axis scaling for every chart. */
  yScale?: YScale;
  /** Rendered at the end of the Signals heading: the app's window/scale controls. */
  controls?: React.ReactNode;
  /** Draw one point in `every`. */
  every?: number;
  /** URL builders for the store's own files; without them the panel shows no download links. */
  exports?: SessionExports;
  /**
   * The rig's clock, in seconds (e.g. `useNowS()`), for an open session's
   * elapsed time. A simulated clock can run far ahead of (or independent of)
   * the wall clock, so `Date.now()` would show a nonsensical duration.
   * Omit to fall back to the wall clock.
   */
  nowS?: number;
}

const when = (s: number) => new Date(s * 1000).toLocaleString();

/** Traces grouped by unit so every signal of a kind shares one axis, in first-seen order. */
function byUnit(traces: SessionDetail["traces"]): Array<[string, Array<SessionTrace & { key: string }>]> {
  const groups = new Map<string, Array<SessionTrace & { key: string }>>();
  for (const [key, trace] of Object.entries(traces)) {
    (groups.get(trace.unit) ?? groups.set(trace.unit, []).get(trace.unit)!).push({ ...trace, key });
  }
  return [...groups];
}
const duration = (s: number) => {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
};
/** `duration()`, but never negative: a `nowS` that hasn't caught up to `start_ns` yet reads "just started". */
const fmtDuration = (s: number) => (s < 0 ? "just started" : duration(s));

/**
 * One recorded session, top to bottom: header, a chart per signal over the
 * whole session (with spans as annotations under it), the devices, writes
 * and controllers as recorded, then events. Pure: `useSession` supplies
 * the detail.
 */
export function SessionPanel({ detail, height = 180, grouping, onGrouping, yScale, controls, every, exports, nowS }: SessionPanelProps) {
  const [own, setOwn] = useState<SessionGrouping>("unit");
  const mode = grouping ?? own;
  const setMode = (g: SessionGrouping) => (onGrouping ? onGrouping(g) : setOwn(g));
  const { session, traces, devices, writes, controllers, events, spans, startS } = detail;
  const endS = session.end_ns ? session.end_ns / 1e9 : (nowS ?? Date.now() / 1000);
  const details = session.details as Record<string, unknown> | null;
  const name = details && typeof details.name === "string" ? details.name : `session ${session.id}`;

  return (
    <article className="fb-panel fb-session">
      <header className="fb-source-head">
        <h3>{name}</h3>
        <span className="fb-muted">
          #{session.id} · {when(startS)} → {session.end_ns ? when(endS) : "open"} · {fmtDuration(endS - startS)}
          {session.hardware ? ` · ${String(session.hardware)}` : ""}
        </span>
      </header>

      {details && Object.keys(details).length > 0 && (
        <section className="fb-session-section">
          <ValueView value={details} />
        </section>
      )}

      {session.config != null && (
        <details className="fb-section">
          <summary>rig config as recorded{session.version ? ` · version ${String(session.version)}` : ""}</summary>
          <ValueView value={session.config} />
        </details>
      )}

      <section className="fb-session-section">
        <h4 className="fb-session-heading">
          <span>Signals</span>
          <span className="fb-segmented" role="radiogroup" aria-label="chart grouping">
            {(["unit", "signal"] as const).map((g) => (
              <button
                key={g}
                type="button"
                role="radio"
                aria-checked={mode === g}
                className={mode === g ? "active" : ""}
                onClick={() => setMode(g)}
              >
                {g === "unit" ? "by unit" : "each signal"}
              </button>
            ))}
          </span>
          {controls && <span className="fb-source-controls">{controls}</span>}
        </h4>
        {mode === "signal" &&
          Object.entries(traces).map(([key, tr]) => (
            <div key={key} className="fb-channel">
              <h4>
                <Ref kind="signal" name={tr.signal.address} />
                <span className="fb-latest">
                  {tr.v.length ? `${tr.v[tr.v.length - 1]!.toFixed(tr.signal.precision ?? 2)} ${tr.unit} · ${tr.v.length} pts` : "no points"}
                </span>
                {exports && <Download what={key} href={(f) => exports.series(tr.signal.address, f)} />}
              </h4>
              <TimeSeries signal={asSignal(tr.signal)} t={tr.t} v={tr.v} height={height} yScale={yScale} every={every} exportHref={exports?.series(tr.signal.address, "csv")} />
            </div>
          ))}
        {mode === "unit" && byUnit(traces).map(([unit, group]) => (
          <div key={unit} className="fb-channel">
            <h4>
              <span>
                {group.map((tr, i) => (
                  <span key={tr.key}>
                    {i > 0 && ", "}
                    <Ref kind="signal" name={tr.signal.address} />
                  </span>
                ))}
              </span>
              <span className="fb-latest">{unit}</span>
            </h4>
            <MultiSeries
              unit={unit}
              title={unit}
              height={height}
              yScale={yScale}
              every={every}
              exportHref={
                exports && group.length === 1 ? exports.series(group[0]!.signal.address, "csv") : undefined
              }
              series={group.map(
                (tr): MultiSeriesTrace => ({
                  label: tr.key,
                  unit,
                  t: tr.t,
                  v: tr.v,
                  precision: tr.signal.precision ?? 2,
                }),
              )}
            />
            <div className="fb-muted fb-session-latest">
              {group.map((tr) => (
                <span key={tr.key}>
                  {tr.key}: {tr.v.length ? `${tr.v[tr.v.length - 1]!.toFixed(tr.signal.precision ?? 2)} ${unit} · ${tr.v.length} pts` : "no points"}
                  {exports && <Download what={tr.key} href={(f) => exports.series(tr.signal.address, f)} />}
                </span>
              ))}
            </div>
          </div>
        ))}
        {Object.keys(traces).length === 0 && <div className="fb-muted">no signals recorded</div>}
      </section>

      {spans.length > 0 && (
        <section className="fb-session-section">
          <h4>Spans</h4>
          <ul className="fb-spans">
            {spans.map((s) => (
              <li key={s.id} style={{ marginLeft: s.parent_id ? "1rem" : 0 }}>
                <span className="fb-tag">{s.kind}</span> {s.label}
                <span className="fb-muted">
                  {" "}
                  {duration(s.start_ns / 1e9)} → {s.end_ns === null ? "…" : duration(s.end_ns / 1e9)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {(devices.length > 0 || writes.length > 0 || controllers.length > 0) && (
        <section className="fb-session-section">
          <h4>Equipment</h4>
          <dl className="fb-state">
            {devices.map((d) => {
              const { label, rest } = deviceConfig(d.config);
              return (
                <div key={d.id} className="fb-state-row">
                  <dt><Ref kind="device" name={d.address}>{d.label ?? label ?? d.address}</Ref></dt>
                  <dd>
                    {d.driver ? describeDevice(d.driver) : "device"}
                    {(d.label ?? label) && <> · {d.address}</>}
                    {Object.keys(rest).length > 0 && <ValueView value={rest} describeKey={describeStateKey} />}
                  </dd>
                </div>
              );
            })}
            {writes.map((w) => (
              <div key={w.signal.address} className="fb-state-row">
                <dt>
                  <Ref kind="signal" name={w.signal.address} />
                  {exports && <Download what={`${w.signal.address}'s writes`} href={(f) => exports.writes(w.signal.address, f)} />}
                </dt>
                <dd>
                  written · {w.signal.unit}
                  {w.limits && <span className="fb-muted"> · limits {w.limits[0]} – {w.limits[1]} {w.signal.unit}</span>}
                </dd>
              </div>
            ))}
            {controllers.map((c) => {
              const { tag, gains } = lawSummary(c.law);
              return (
                <div key={c.name} className="fb-state-row">
                  <dt>
                    controller <Ref kind="controller" name={c.name} />
                    {exports && <Download what={`${c.name}'s ticks`} href={(f) => exports.ticks(c.name, f)} />}
                  </dt>
                  <dd>
                    <Ref kind="signal" name={c.source} /> → <Ref kind="signal" name={c.name} />
                    {tag && <> · <span className="fb-tag">{tag}</span></>}
                    {gains && <span className="fb-muted"> · {gains}</span>}
                  </dd>
                </div>
              );
            })}
          </dl>
        </section>
      )}

      <section className="fb-session-section">
        <h4>
          Events
          {exports && events.length > 0 && <Download what="the events" href={exports.events} />}
        </h4>
        {events.length === 0 ? (
          <div className="fb-muted">none</div>
        ) : (
          <table className="fb-table">
            <tbody>
              {events.map((e, i) => {
                const { level, message, details } = storedEvent(e.detail);
                return (
                <tr key={e.id ?? i}>
                  <td className="fb-muted">+{duration(e.offset_ns / 1e9)}</td>
                  <td title={e.kind}>
                    {level && <span className={`fb-badge fb-event-${level}`}>{level}</span>} <span className="fb-tag">{describeEventKind(e.kind)}</span>
                  </td>
                  <td>{e.source ? describeSubject(e.source) : ""}</td>
                  <td>
                    {message}
                    {details !== undefined && details !== null && (
                      <div style={{ fontFamily: "var(--fb-mono, monospace)" }}>
                        <ValueView value={details} />
                      </div>
                    )}
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>
    </article>
  );
}
