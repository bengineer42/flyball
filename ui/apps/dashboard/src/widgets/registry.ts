/** Every widget type the app knows, in catalogue order. A document naming a type not here renders as "unknown". */
import { readout } from "./Readout.js";
import { gauge } from "./Gauge.js";
import { chart } from "./Chart.js";
import { loop } from "./Loop.js";
import { device } from "./Device.js";
import { program } from "./Program.js";
import { recording } from "./Recording.js";
import { events } from "./Events.js";
import { health } from "./Health.js";
import { text } from "./Text.js";
import { heading, spacer } from "./Heading.js";
import { link } from "./Link.js";
import type { WidgetCategory, WidgetType } from "./types.js";

export const WIDGET_TYPES: WidgetType[] = [readout, gauge, chart, loop, device, program, recording, events, health, text, heading, spacer, link];

export const CATEGORIES: Array<{ id: WidgetCategory; label: string }> = [
  { id: "readings", label: "Readings" },
  { id: "control", label: "Control" },
  { id: "status", label: "Status" },
  { id: "layout", label: "Layout" },
];

const byType = new Map(WIDGET_TYPES.map((t) => [t.type, t]));

export const widgetType = (type: string): WidgetType | undefined => byType.get(type);

export type { WidgetType, WidgetCategory, WidgetCost, WidgetComponentProps } from "./types.js";
