import type { SessionDetail } from "../hooks/useSession.js";
import { TimeSeries } from "./TimeSeries.js";
import { ValueView } from "./ValueView.js";
import { Ref } from "../links.js";

export interface SessionPanelProps {
  detail: SessionDetail;
  /** Chart height per channel. */
  height?: number;
}

const when = (s: number) => new Date(s * 1000).toLocaleString();
const duration = (s: number) => {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
};

/**
 * One recorded session, top to bottom: header, a chart per channel over the
 * whole session (with spans as annotations under it), loops and actuators as
 * recorded, then events. Pure: `useSession` supplies the detail.
 */
export function SessionPanel({ detail, height = 180 }: SessionPanelProps) {
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

      <section className="fb-session-section">
        <h4>Channels</h4>
        {Object.entries(traces).map(([key, trace]) => (
          <div key={key} className="fb-channel">
            <h4>
              <Ref kind="channel" name={trace.channel.source.name} measurand={trace.channel.measurand.name} />
              <span className="fb-latest">
                {trace.v.length ? `${trace.v.length} pts · ${trace.v[trace.v.length - 1]!.toFixed(2)} ${trace.unit}` : "no points"}
              </span>
            </h4>
            <TimeSeries
              channel={{
                source: trace.channel.source.name,
                measurand: trace.channel.measurand.name,
                unit: trace.unit,
                label: trace.channel.measurand.label ?? trace.channel.measurand.name,
                range: null,
                precision: 2,
              }}
              t={trace.t}
              v={trace.v}
              height={height}
            />
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
