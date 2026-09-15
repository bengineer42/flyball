import type { JsonSchema } from "@flyball/client";
import { fields, formatNumber, formatValue } from "@flyball/client";

export interface ObjectViewProps {
  schema: JsonSchema;
  value: Record<string, unknown> | undefined;
  /** Field names to leave out at the top level. */
  omit?: string[];
}

/**
 * Any object the schema describes, one row per field, labelled and
 * unit-suffixed by the schema; nested objects indent. Config, settings and
 * state all render through this.
 */
export function ObjectView({ schema, value, omit = [] }: ObjectViewProps) {
  if (!value) return <div className="fb-muted">—</div>;
  const rows = fields(schema).filter(([name]) => !omit.includes(name));
  if (rows.length === 0) return <div className="fb-muted">nothing to show</div>;
  return (
    <dl className="fb-state">
      {rows.map(([name, field]) => {
        const v = value[name];
        const nested = field.properties && v && typeof v === "object" && !Array.isArray(v);
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
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
