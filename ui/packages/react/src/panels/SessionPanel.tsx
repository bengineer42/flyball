import type { SessionDetail, SessionTrace } from "../hooks/useSession.js";
import { useState } from "react";
import { MultiSeries, type MultiSeriesTrace } from "./MultiSeries.js";
import { TimeSeries } from "./TimeSeries.js";
import type { YScale } from "./yscale.js";
import { ValueView } from "./ValueView.js";
import { Ref } from "../links.js";

export type SessionGrouping = "unit" | "channel";

export interface SessionPanelProps {
  detail: SessionDetail;
  /** Chart height per channel. */
  height?: number;
  /** One chart per unit (default), or one per channel. Uncontrolled unless given. */
  grouping?: SessionGrouping;
  onGrouping?(grouping: SessionGrouping): void;
  /** y axis scaling for every chart. */
  yScale?: YScale;
  /** Rendered at the end of the Channels heading: the app's window/scale controls. */
  controls?: React.ReactNode;
  /** Draw one point in `every`. */
  every?: number;
}

const when = (s: number) => new Date(s * 1000).toLocaleString();

/** Traces grouped by unit so every channel of a kind shares one axis, in first-seen order. */
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

/**
 * One recorded session, top to bottom: header, a chart per channel over the
 * whole session (with spans as annotations under it), loops and actuators as
 * recorded, then events. Pure: `useSession` supplies the detail.
 */
export function SessionPanel({ detail, height = 180, grouping, onGrouping, yScale, controls, every }: SessionPanelProps) {
  const [own, setOwn] = useState<SessionGrouping>("unit");
  const mode = grouping ?? own;
  const setMode = (g: SessionGrouping) => (onGrouping ? onGrouping(g) : setOwn(g));
  const { session, traces, actuators, loops, events, spans, startS } = detail;
  const endS = session.end_ns ? session.end_ns / 1e9 : Date.now() / 1000;
  const details = session.details as Record<string, unknown> | null;
  const name = details && typeof details.name === "string" ? details.name : `session ${session.id}`;

  return (
    <article className="fb-panel fb-session">
      <header className="fb-source-head">
        <h3>{name}</h3>
        <span className="fb-muted">
          #{session.id} · {when(startS)} → {session.end_ns ? when(endS) : "open"} · {duration(endS - startS)}
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
          <span>Channels</span>
          <span className="fb-segmented" role="radiogroup" aria-label="chart grouping">
            {(["unit", "channel"] as const).map((g) => (
              <button
                key={g}
                type="button"
                role="radio"
                aria-checked={mode === g}
                className={mode === g ? "active" : ""}
                onClick={() => setMode(g)}
              >
                {g === "unit" ? "by unit" : "each channel"}
              </button>
            ))}
          </span>
          {controls && <span className="fb-source-controls">{controls}</span>}
        </h4>
        {mode === "channel" &&
          Object.entries(traces).map(([key, tr]) => (
            <div key={key} className="fb-channel">
              <h4>
                <Ref kind="channel" name={tr.channel.source.name} measurand={tr.channel.measurand.name} />
                <span className="fb-latest">
                  {tr.v.length ? `${tr.v[tr.v.length - 1]!.toFixed(2)} ${tr.unit} · ${tr.v.length} pts` : "no points"}
                </span>
              </h4>
              <TimeSeries
                channel={{
                  source: tr.channel.source.name,
                  measurand: tr.channel.measurand.name,
                  unit: tr.unit,
                  label: tr.channel.measurand.label ?? tr.channel.measurand.name,
                  range: null,
                  precision: 2,
                }}
                t={tr.t}
                v={tr.v}
                height={height}
                yScale={yScale}
                every={every}
              />
            </div>
          ))}
        {mode === "unit" && byUnit(traces).map(([unit, group]) => (
          <div key={unit} className="fb-channel">
            <h4>
              <span>
                {group.map((tr, i) => (
                  <span key={tr.key}>
                    {i > 0 && ", "}
                    <Ref kind="channel" name={tr.channel.source.name} measurand={tr.channel.measurand.name} />
                  </span>
                ))}
              </span>
              <span className="fb-latest">{unit}</span>
            </h4>
            <MultiSeries
              unit={unit}
              height={height}
              yScale={yScale}
              every={every}
              series={group.map(
                (tr): MultiSeriesTrace => ({
                  label: tr.key,
                  unit,
                  t: tr.t,
                  v: tr.v,
                  precision: 2,
                }),
              )}
            />
            <div className="fb-muted fb-session-latest">
              {group.map((tr) => (
                <span key={tr.key}>
                  {tr.key}: {tr.v.length ? `${tr.v[tr.v.length - 1]!.toFixed(2)} ${unit} · ${tr.v.length} pts` : "no points"}
                </span>
              ))}
            </div>
          </div>
        ))}
        {Object.keys(traces).length === 0 && <div className="fb-muted">no channels recorded</div>}
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

      {(loops.length > 0 || actuators.length > 0) && (
        <section className="fb-session-section">
          <h4>Equipment</h4>
          <dl className="fb-state">
            {actuators.map((a) => (
              <div key={a.name} className="fb-state-row">
                <dt><Ref kind="actuator" name={a.name} /></dt>
                <dd>
                  {a.kind} <ValueView value={a.config} />
                </dd>
              </div>
            ))}
            {loops.map((l, i) => (
              <div key={i} className="fb-state-row">
                <dt>loop {l.name ?? i}</dt>
                <dd>
                  <Ref kind="channel" name={l.channel.source.name} measurand={l.channel.measurand.name} /> →{" "}
                  <Ref kind="actuator" name={l.actuator.name} /> <ValueView value={l.config} />
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      <section className="fb-session-section">
        <h4>Events</h4>
        {events.length === 0 ? (
          <div className="fb-muted">none</div>
        ) : (
          <table className="fb-table">
            <tbody>
              {events.map((e, i) => (
                <tr key={e.id ?? i}>
                  <td className="fb-muted">+{duration(e.offset_ns / 1e9)}</td>
                  <td>
                    <span className="fb-tag">{e.kind}</span>
                  </td>
                  <td>{e.source ?? ""}</td>
                  <td>
                    <ValueView value={e.detail} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </article>
  );
}
