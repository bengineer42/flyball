import { type SessionDetail, useSessionSeries, useEverShown } from "../hooks/useSession.js";
import { useEffect, useRef, useState } from "react";
import { describeDevice, describeEdge, describeEventCode, describeStateKey, describeSubject, isHousekeeping, isNumeric, isScratch, type Dtype, type SignalOut, type SignalRow, fixed } from "@flyball/client";
import { MultiSeries, type MultiSeriesTrace } from "./MultiSeries.js";
import { TimeSeries } from "./TimeSeries.js";
import type { YScale } from "./yscale.js";
import { ValueView } from "./ValueView.js";
import { Ref } from "../links.js";
import { useVisible } from "../hooks/useVisible.js";

/** A device's recorded config, if any, as a plain object minus what the row already says. */
function deviceConfig(config: unknown): { label: string | null; rest: Record<string, unknown> } {
  if (!config || typeof config !== "object") return { label: null, rest: {} };
  const { label, type: _type, name: _name, driver: _driver, ...rest } = config as Record<string, unknown>;
  return { label: typeof label === "string" ? label : null, rest };
}

/** A recorded dtype as the current `Dtype` union; an older recording may carry a numpy-style string (`float64`), mapped onto the nearest fit. */
function asDtype(dtype: string): Dtype {
  if (dtype === "float" || dtype === "int" || dtype === "bool" || dtype === "str" || dtype === "enum" || dtype === "json") return dtype;
  if (dtype.startsWith("float")) return "float";
  if (dtype.startsWith("int") || dtype.startsWith("uint")) return "int";
  if (dtype.startsWith("bool")) return "bool";
  return "str";
}

/** The last point of a trace, formatted -- a generator-based controller write can record a non-numeric value (a ramp's `regulate` object, say), so this guards the same way `ControllerPanel.tsx`'s `value()`/`describeReference` do. */
function latest(v: (number | null)[], precision: number): string {
  const last = v[v.length - 1];
  return typeof last === "number" ? fixed(last, precision) : last === null ? "—" : "?";
}

/** A recorded signal as the charts take one: the row's metadata, no role or tags (not recorded) and no live values. */
function asSignal(row: SignalRow): SignalOut {
  return {
    name: row.address.slice(row.address.lastIndexOf(".") + 1),
    address: row.address,
    access: row.access,
    label: row.label ?? "",
    quantity: row.quantity,
    unit: row.unit,
    dimension: null,
    dtype: asDtype(row.dtype),
    shape: row.shape,
    role: "readout",
    tags: {},
    initial: null,
    range: row.range,
    precision: row.precision,
    warning: row.warning,
    alarm: row.alarm,
    poll_s: null,
    limits: row.limits,
    latest: null,
    write: null,
  };
}

/** A law config (`{type, kp, ki, tt, ...}`) as `PI` plus a short `kp 100 · ki 0.15 · tt 30 s` line: the gains as the field names a controls engineer already knows, a unit only where one is fixed. */
function lawSummary(config: unknown): { type: string | null; gains: string } {
  if (!config || typeof config !== "object") return { type: null, gains: "" };
  const { type, ...gains } = config as Record<string, unknown>;
  const gains_ = Object.entries(gains)
    .filter(([, v]) => v !== null && v !== undefined && typeof v !== "object")
    .map(([k, v]) => `${k} ${String(v)}${k === "tt" ? " s" : ""}`)
    .join(" · ");
  return { type: typeof type === "string" ? type : null, gains: gains_ };
}

/** `recorder.py`'s `event()` wraps the severity, scope and message around the emitter's own `details`. */
function storedEvent(detail: unknown): { severity?: string; message?: string; details: unknown } {
  if (!detail || typeof detail !== "object" || !("message" in detail)) return { details: detail };
  const { severity, message, details } = detail as { severity?: string; message?: string; details?: unknown };
  return { severity: typeof severity === "string" ? severity : undefined, message, details };
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
  /** Given: the name box is editable, and a Save button appears once its text differs from the current name. Omit for a read-only header. */
  onRename?(name: string): void | Promise<void>;
}

const when = (s: number) => new Date(s * 1000).toLocaleString();

/**
 * Whether a recorded signal can ever be a line on a chart: a scalar
 * (`float`/`int`, `schema.ts`'s `isNumeric`) that is not a device's own
 * housekeeping trace (`isHousekeeping`: `<device>.conditions`,
 * `<device>.last.*` -- invocation records and condition lists, not
 * readings). Everything else -- `enum`, `json`, `str`, `bool`, plus those
 * two families by name -- is recorded and listed plainly below the charts
 * (`UnchartedSignals`), never fetched as a series: asking the store for
 * `blender.mode`'s "series" on every session open is a wasted round trip
 * for a value that was never going to sit on a %RH axis.
 */
function chartable(row: SignalRow): boolean {
  return isNumeric(asDtype(row.dtype)) && !isHousekeeping({ name: "", address: row.address });
}

/** Chartable signals grouped by unit so every signal of a kind shares one axis, in first-seen order. */
function byUnit(signals: readonly SignalRow[]): Array<[string, SignalRow[]]> {
  const groups = new Map<string, SignalRow[]>();
  for (const row of signals.filter(chartable)) {
    (groups.get(row.unit) ?? groups.set(row.unit, []).get(row.unit)!).push(row);
  }
  return [...groups];
}

/** A recorded signal that is never charted, plainly: its address, dtype and why -- not dropped from the page, just off the axis it was never going on. */
function UnchartedSignals({ signals }: { signals: readonly SignalRow[] }) {
  const rows = signals.filter((r) => !chartable(r));
  if (rows.length === 0) return null;
  return (
    <div className="fb-session-uncharted fb-muted">
      <div>Recorded, not charted -- not a value a line chart can show:</div>
      <ul>
        {rows.map((row) => (
          <li key={row.address}>
            <Ref kind="signal" name={row.address} /> · {asDtype(row.dtype)}
            {isHousekeeping({ name: "", address: row.address }) ? " · housekeeping" : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}
const duration = (s: number) => {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
};
/** `duration()`, but never negative: a `nowS` that hasn't caught up to `start_ns` yet reads "just started". */
const fmtDuration = (s: number) => (s < 0 ? "just started" : duration(s));

const pad = (n: number) => String(n).padStart(2, "0");
/** "#3 2026-09-18 11:41:05" -- what an unnamed session reads as where there's no box to type
 * into (the read-only header): the session's own id and its actual start (not "now"). Never
 * used to pre-fill the *editable* name box itself -- that starts blank, a placeholder says
 * "session name" instead, so clicking save without typing can't silently store this text. */
const defaultSessionName = (session: { id: number; start_ns: number }): string => {
  const d = new Date(session.start_ns / 1e6);
  return `#${session.id} ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};

/**
 * A placeholder the size of the chart it stands in for: shown until a
 * group's series has actually landed, so a chart still loading never reads
 * as a chart that recorded nothing (`fb-chart-placeholder` is the same class
 * `MultiSeries`'s own full-size placeholder uses, for the same look).
 */
function ChartLoading({ height }: { height: number }) {
  return (
    <div className="fb-chart fb-chart-placeholder" style={{ height }}>
      <span className="fb-muted">loading…</span>
    </div>
  );
}

/**
 * One unit's chart in the "by unit" grouping. Its own series fetch, gated on
 * having actually been on screen (`useVisible` + `useEverShown`) -- a group
 * nobody scrolls to never asks the store for anything. `useSessionSeries`
 * caches by session id/address/point budget, so switching to "each signal"
 * and back, or scrolling this group away and back, costs nothing further.
 */
function SessionUnitChart({ sessionId, unit, signals, startS, height, yScale, every, live, exports }: { sessionId: number; unit: string; signals: SignalRow[]; startS: number; height: number; yScale?: YScale; every?: number; live: boolean; exports?: SessionExports }) {
  const ref = useRef<HTMLDivElement>(null);
  const shown = useEverShown(useVisible(ref, "200px", false));
  const { traces, loading } = useSessionSeries(sessionId, signals, startS, shown);
  const loaded = shown && !loading && signals.every((s) => s.address in traces);
  return (
    <div className="fb-channel" ref={ref}>
      <h4>
        <span>
          {signals.map((row, i) => (
            <span key={row.address}>
              {i > 0 && ", "}
              <Ref kind="signal" name={row.address} />
            </span>
          ))}
        </span>
        <span className="fb-latest">{unit}</span>
      </h4>
      {loaded ? (
        <MultiSeries
          unit={unit}
          title={unit}
          height={height}
          yScale={yScale}
          every={every}
          live={live}
          exportHref={exports && signals.length === 1 ? exports.series(signals[0]!.address, "csv") : undefined}
          series={signals.map(
            (row): MultiSeriesTrace => ({
              label: row.address,
              unit,
              t: traces[row.address]!.t,
              v: traces[row.address]!.v,
              precision: row.precision ?? 2,
            }),
          )}
        />
      ) : (
        <ChartLoading height={height} />
      )}
      <div className="fb-muted fb-session-latest">
        {signals.map((row) => {
          const tr = traces[row.address];
          return (
            <span key={row.address}>
              {row.address}: {!loaded ? "loading…" : tr!.v.length ? `${latest(tr!.v, row.precision ?? 2)} ${unit} · ${tr!.v.length} pts` : "no points"}
              {exports && <Download what={row.address} href={(f) => exports.series(row.address, f)} />}
            </span>
          );
        })}
      </div>
    </div>
  );
}

/**
 * One signal's chart in the "each signal" grouping -- same lazy-fetch and
 * cache-sharing story as `SessionUnitChart`, one signal at a time.
 */
function SessionSignalChart({ sessionId, row, startS, height, yScale, every, live, exports }: { sessionId: number; row: SignalRow; startS: number; height: number; yScale?: YScale; every?: number; live: boolean; exports?: SessionExports }) {
  const ref = useRef<HTMLDivElement>(null);
  const shown = useEverShown(useVisible(ref, "200px", false));
  const signals = [row];
  const { traces, loading } = useSessionSeries(sessionId, signals, startS, shown);
  const tr = traces[row.address];
  const loaded = shown && !loading && tr !== undefined;
  return (
    <div className="fb-channel" ref={ref}>
      <h4>
        <Ref kind="signal" name={row.address} />
        <span className="fb-latest">{!loaded ? "loading…" : tr!.v.length ? `${latest(tr!.v, row.precision ?? 2)} ${row.unit} · ${tr!.v.length} pts` : "no points"}</span>
        {exports && <Download what={row.address} href={(f) => exports.series(row.address, f)} />}
      </h4>
      {loaded ? (
        <TimeSeries signal={asSignal(row)} t={tr!.t} v={tr!.v} height={height} yScale={yScale} every={every} exportHref={exports?.series(row.address, "csv")} live={live} />
      ) : (
        <ChartLoading height={height} />
      )}
    </div>
  );
}

/**
 * One recorded session, top to bottom: header, a chart per signal over the
 * whole session (with spans as annotations under it), the devices, writes
 * and controllers as recorded, then events. Pure: `useSession` supplies
 * the shell; each chart fetches its own series once it is actually shown.
 */
export function SessionPanel({ detail, height = 180, grouping, onGrouping, yScale, controls, every, exports, nowS, onRename }: SessionPanelProps) {
  const [own, setOwn] = useState<SessionGrouping>("unit");
  const mode = grouping ?? own;
  const setMode = (g: SessionGrouping) => (onGrouping ? onGrouping(g) : setOwn(g));
  const { session, signals, devices, writes, controllers, events, spans, startS } = detail;
  const endS = session.end_ns ? session.end_ns / 1e9 : (nowS ?? Date.now() / 1000);
  // A closed session has no live edge to return to: its charts get no "live" button.
  const live = session.end_ns === null;
  const details = session.details as Record<string, unknown> | null;
  const savedName = details && typeof details.name === "string" ? details.name : "";
  // The read-only header (no onRename) falls back to something identifiable when there's no
  // real name; the *editable* box does not -- it starts genuinely empty and says so as
  // placeholder text only, never as text that would get saved just by clicking without typing.
  const name = savedName || defaultSessionName(session);
  const [draft, setDraft] = useState(savedName);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  // A different session (or a rename landing from elsewhere) resets the draft to match it.
  useEffect(() => setDraft(savedName), [savedName, session.id]);
  // A scratch record has no real stored name to compare against -- `savedName` is always "" for
  // one -- and "save" on one always promotes it to a real session regardless of whether the text
  // changed, so any non-empty draft is enough. An already-real session only has something to do
  // once the draft actually differs from what's actually stored.
  const dirty = onRename !== undefined && draft.trim() !== "" && (isScratch(session) || draft !== savedName);
  useEffect(() => {
    if (!saved) return undefined;
    const id = setTimeout(() => setSaved(false), 3000);
    return () => clearTimeout(id);
  }, [saved]);
  const save = async () => {
    if (!onRename || !dirty) return;
    setSaving(true);
    setSaved(false);
    try {
      await onRename(draft.trim());
      setSaved(true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <article className="fb-panel fb-session">
      <header className="fb-source-head">
        {onRename ? (
          <span className="fb-session-name-edit">
            <input
              className="fb-session-name-input"
              value={draft}
              placeholder="session name"
              onChange={(e) => {
                setDraft(e.target.value);
                setSaved(false);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") void save();
                else if (e.key === "Escape") setDraft(savedName);
              }}
              disabled={saving}
              aria-label="session name"
              title="session name"
            />
            {/* Always rendered, greyed out until there's something to save -- an
                appearing/disappearing button here is exactly the layout-shift bug
                brain/UI.md's "No layout shift from a state change" convention exists for;
                caught this one only once it was live, hence this comment. */}
            {/* Blue (the accent, matching the app's other primary/save actions -- Rig.tsx's
                `variant="contained"` save is the same colour) once there's something to save;
                the "saved" state (a click already happened) stays plain, not blue -- nothing
                pending to draw the eye to. */}
            <button type="button" className={`fb-tb${dirty && !saving ? " active" : ""}`} onClick={() => void save()} disabled={saving || !dirty} data-testid="save-session">
              {saving ? "saving…" : saved ? "saved" : isScratch(session) ? "save session" : "save name"}
            </button>
          </span>
        ) : (
          <h3>{name}</h3>
        )}
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
          signals
            .filter(chartable)
            .map((row) => (
              <SessionSignalChart key={row.address} sessionId={session.id} row={row} startS={startS} height={height} yScale={yScale} every={every} live={live} exports={exports} />
            ))}
        {mode === "unit" &&
          byUnit(signals).map(([unit, group]) => (
            <SessionUnitChart key={unit} sessionId={session.id} unit={unit} signals={group} startS={startS} height={height} yScale={yScale} every={every} live={live} exports={exports} />
          ))}
        {signals.filter(chartable).length === 0 && <div className="fb-muted">no chartable signals recorded</div>}
        <UnchartedSignals signals={signals} />
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
              const { type, gains } = lawSummary(c.law);
              return (
                <div key={c.name} className="fb-state-row">
                  <dt>
                    controller <Ref kind="controller" name={c.name} />
                    {exports && <Download what={`${c.name}'s ticks`} href={(f) => exports.ticks(c.name, f)} />}
                  </dt>
                  <dd>
                    <Ref kind="signal" name={c.measured} /> → <Ref kind="signal" name={c.name} />
                    {type && <> · <span className="fb-tag">{type}</span></>}
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
                const { severity, message, details } = storedEvent(e.detail);
                return (
                <tr key={e.id ?? i}>
                  <td className="fb-muted">+{duration(e.offset_ns / 1e9)}</td>
                  <td title={e.code}>
                    {severity && <span className={`fb-badge fb-event-${severity}`}>{severity}</span>} <span className="fb-tag">{describeEventCode(e.code)}</span>
                    {e.edge && <span className={`fb-event-edge fb-event-${e.edge}`}>{describeEdge(e.edge, details)}</span>}
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
