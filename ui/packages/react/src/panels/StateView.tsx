import type { ReactNode } from "react";
import type { DeviceState, JsonSchema } from "@flyball/client";
import { ObjectView } from "./ObjectView.js";

export interface StateViewProps {
  schema: JsonSchema;
  state: DeviceState | undefined;
  /** Extra content appended after a field's value, keyed by field name (an "at limit" marker, say). */
  extra?: Record<string, ReactNode>;
}

/** A device's state: every schema field via `ObjectView`, then any conditions as badges. */
export function StateView({ schema, state, extra }: StateViewProps) {
  if (!state) return <div className="fb-muted">no state yet</div>;
  return (
    <>
      <ObjectView schema={schema} value={state} omit={["conditions"]} extra={extra} />
      {state.conditions?.length > 0 && (
        <div className="fb-conditions">
          {state.conditions.map((c) => (
            <span key={c.kind} className={`fb-condition fb-level-${c.level}`} title={c.message}>
              {c.kind}
            </span>
          ))}
        </div>
      )}
    </>
  );
}
