export interface ValueViewProps {
  value: unknown;
  /**
   * How to label an object's keys: a raw key (`last_raw`, `kp`) to a label
   * and an optional hover hint. Default: the key as given, no hint -- for
   * arbitrary JSON (an event's `details`) where the keys are not this
   * library's vocabulary to translate.
   */
  describeKey?(key: string): { label: string; hint?: string };
}

/** Any JSON, laid out like `ObjectView` but without a schema: objects as rows, arrays inline, numbers trimmed. */
export function ValueView({ value, describeKey }: ValueViewProps) {
  if (value === null || value === undefined) return <span className="fb-muted">—</span>;
  if (typeof value === "number") return <>{Number.isInteger(value) ? value : value.toFixed(3)}</>;
  if (typeof value === "boolean") return <>{value ? "yes" : "no"}</>;
  if (typeof value === "string") return <>{value}</>;
  if (Array.isArray(value)) {
    return (
      <>
        {value.map((item, i) => (
          <span key={i} className="fb-array-item">
            <ValueView value={item} describeKey={describeKey} />
          </span>
        ))}
      </>
    );
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return <span className="fb-muted">empty</span>;
  return (
    <dl className="fb-state">
      {entries.map(([k, v]) => {
        const { label, hint } = describeKey ? describeKey(k) : { label: k, hint: undefined };
        return (
          <div key={k} className="fb-state-row">
            <dt title={hint}>{label}</dt>
            <dd>
              <ValueView value={v} describeKey={describeKey} />
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
