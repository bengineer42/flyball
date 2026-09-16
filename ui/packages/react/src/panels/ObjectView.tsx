import type { ReactNode } from "react";
import type { JsonSchema } from "@flyball/client";
import { fields, formatNumber, formatValue, liveOf, resolveLive } from "@flyball/client";

export interface ObjectViewProps {
  schema: JsonSchema;
  value: Record<string, unknown> | undefined;
  /** Field names to leave out at the top level. */
  omit?: string[];
  /**
   * What a field's `live` path resolves against (a device's view, a
   * simulated plant's description), so a configured value is shown beside
   * where the quantity is now. Nothing is shown without it.
   */
  live?: unknown;
  /** Extra content appended after a field's value, keyed by field name (an "at limit" marker, say). */
  extra?: Record<string, ReactNode>;
}

/**
 * Any object the schema describes, one row per field, labelled and
 * unit-suffixed by the schema; nested objects indent. Config, settings and
 * state all render through this.
 */
export function ObjectView({ schema, value, omit = [], live, extra }: ObjectViewProps) {
  if (!value) return <div className="fb-muted">—</div>;
  const rows = fields(schema).filter(([name]) => !omit.includes(name));
  if (rows.length === 0) return <div className="fb-muted">nothing to show</div>;
  return (
    <dl className="fb-state">
      {rows.map(([name, field]) => {
        const v = value[name];
        const nested = field.properties && v && typeof v === "object" && !Array.isArray(v);
        const path = live === undefined ? undefined : liveOf(field);
        const now = path === undefined ? undefined : liveNow(resolveLive(path, live), field);
        return (
          <div key={name} className="fb-state-row">
            <dt title={field.description}>{field.title ?? name}</dt>
            <dd>
              {nested ? (
                <ObjectView schema={{ ...field, $defs: schema.$defs }} value={v as Record<string, unknown>} />
              ) : typeof v === "number" ? (
                <>
                  <span className="fb-num">{formatNumber(v, field)}</span>
                  {field.unit && <span className="fb-num-unit">{field.unit}</span>}
                </>
              ) : (
                formatValue(v, field)
              )}
              {now && <span className="fb-muted"> (now {now})</span>}
              {extra?.[name]}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/**
 * A resolved `live` value as text in the field's unit and precision: one
 * number, or every port's joined with ` / ` (`604.2 / 609.1 / 601.0`).
 * Undefined when there is nothing to show.
 */
function liveNow(resolved: unknown, field: JsonSchema): string | undefined {
  const values =
    resolved && typeof resolved === "object" && !Array.isArray(resolved)
      ? Object.values(resolved as Record<string, unknown>)
      : [resolved];
  const shown = values
    .filter((x) => x !== undefined && x !== null)
    .map((x) => (typeof x === "number" ? formatNumber(x, field) : String(x)));
  if (shown.length === 0) return undefined;
  return field.unit ? `${shown.join(" / ")} ${field.unit}` : shown.join(" / ");
}
