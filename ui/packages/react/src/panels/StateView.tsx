import type { DeviceState, JsonSchema } from "@flyball/client";
import { ObjectView } from "./ObjectView.js";

export interface StateViewProps {
  schema: JsonSchema;
  state: DeviceState | undefined;
}

/** A device's state: every schema field via `ObjectView`, then any conditions as badges. */
export function StateView({ schema, state }: StateViewProps) {
  if (!state) return <div className="fb-muted">no state yet</div>;
  return (
    <>
      <ObjectView schema={schema} value={state} omit={["conditions"]} />
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
