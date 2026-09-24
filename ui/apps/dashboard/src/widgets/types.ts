/**
 * A widget type: what the catalogue shows, what its `config` looks like (as
 * JSON Schema, rendered by the generic form), how big it starts, what it
 * costs to keep live, and the component that draws it. The document only
 * ever names a `type`; everything else is looked up here at render time.
 */
import type { ComponentType } from "react";
import type { DashboardWidget, JsonSchema } from "@flyball/client";
import type { UiSchema } from "@rjsf/utils";
import type { Bindings } from "../dashboard/context.js";

export type WidgetCategory = "readings" | "control" | "status" | "layout";
/** `cheap`: text and numbers; `chart`: one canvas redrawn per sample; `heavy`: several charts or a form-heavy panel. */
export type WidgetCost = "cheap" | "chart" | "heavy";

export interface WidgetSize {
  w: number;
  h: number;
}

export interface WidgetComponentProps {
  widget: DashboardWidget;
  config: Record<string, unknown>;
  editing: boolean;
}

export interface WidgetType {
  /** What a document's widget names: `readout`, `chart`, ... */
  type: string;
  /** What the catalogue calls it: "Readout". */
  label: string;
  description: string;
  category: WidgetCategory;
  defaultSize: WidgetSize;
  minSize: WidgetSize;
  cost: WidgetCost;
  /** The config's schema for this rig; `config` is the current value, for fields whose options depend on another (a device's commands). */
  configSchema(bindings: Bindings, config: Record<string, unknown>): JsonSchema;
  /** Extra form hints over what the schema implies (a select instead of segments, a textarea). */
  uiSchema?: UiSchema;
  /** The config a freshly added widget starts with: the first signal, say. */
  defaultConfig?(bindings: Bindings): Record<string, unknown>;
  /** The tile's heading when the widget has no `label` of its own; undefined for none. */
  labelFor?(config: Record<string, unknown>, bindings: Bindings): string | undefined;
  /** Whether the tile draws a header when it has no label: off for widgets that carry their own (a loop faceplate). */
  header?: boolean;
  Component: ComponentType<WidgetComponentProps>;
}
