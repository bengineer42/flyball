/** Any JSON, laid out like `ObjectView` but without a schema: objects as rows, arrays inline, numbers trimmed. */
export function ValueView({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="fb-muted">—</span>;
  if (typeof value === "number") return <>{Number.isInteger(value) ? value : value.toFixed(3)}</>;
  if (typeof value === "boolean") return <>{value ? "yes" : "no"}</>;
  if (typeof value === "string") return <>{value}</>;
  if (Array.isArray(value)) {
    return (
      <>
        {value.map((item, i) => (
          <span key={i} className="fb-array-item">
            <ValueView value={item} />
          </span>
        ))}
      </>
    );
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return <span className="fb-muted">empty</span>;
  return (
    <dl className="fb-state">
      {entries.map(([k, v]) => (
        <div key={k} className="fb-state-row">
          <dt>{k}</dt>
          <dd>
            <ValueView value={v} />
          </dd>
        </div>
      ))}
    </dl>
  );
}
