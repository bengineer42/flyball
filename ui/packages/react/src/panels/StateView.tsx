import type { ReactNode } from "react";
import type { JsonSchema } from "@flyball/client";
import { ObjectView } from "./ObjectView.js";

export interface StateViewProps {
  schema: JsonSchema;
  state: Record<string, unknown> | undefined;
  /** Extra content appended after a field's value, keyed by field name (an "at limit" marker, say). */
  extra?: Record<string, ReactNode>;
}

/**
 * Any schema-described object as rows via `ObjectView` -- a thin, named
 * wrapper kept for callers that already have a plain object and its schema
 * (a device's config, a command's arguments) rather than a live signal
 * tree, which `DeviceSignals` renders directly.
 */
export function StateView({ schema, state, extra }: StateViewProps) {
  if (!state) return <div className="fb-muted">nothing to show</div>;
  return <ObjectView schema={schema} value={state} extra={extra} />;
}
