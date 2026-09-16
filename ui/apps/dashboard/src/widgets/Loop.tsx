import { memo, useRef } from "react";
import { LoopPanel, useVisible, type LoopTrace } from "@flyball/react";
import { useBindings, useLoopsData, useRigData } from "../dashboard/context.js";
import { Missing } from "./Missing.js";
import { loopSchema, SELECTS } from "./schema.js";
import { bodyPx, useFrozen } from "./size.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

const EMPTY: LoopTrace = { t: [], reference: [], reading: [], demand: [], expected: [], correction: [] };

const LoopWidget = memo(function LoopWidget({ config, widget, editing }: WidgetComponentProps) {
  const { charts, exports, rowHeight } = useRigData();
  const { loops, history } = useLoopsData();
  const bindings = useBindings();
  const host = useRef<HTMLDivElement>(null);
  const visible = useVisible(host);
  const name = String(config.loop ?? "");
  const loop = loops[name];
  const compact = config.view !== "full";
  const trace = useFrozen(history[name] ?? EMPTY, visible);
  if (!loop) return <Missing what="loop" name={name} hint={bindings.loops.length ? "Configure the widget to pick one of this rig's loops." : "This rig has no loops."} />;
  // Compact: one chart, so it gets what is left after the panel's header, the chart title, the legend and the readouts row. Full: two charts of a fixed height, and the tile scrolls.
  const height = compact ? Math.max(60, bodyPx(widget.h, rowHeight, editing || Boolean(widget.title)) - 32 - 22 - 27 - 78) : 150;
  return (
    <div ref={host} className="fb-fill fb-loop-host">
      <LoopPanel loop={loop} history={trace} compact={compact} height={height} windowS={charts.windowS} yScale={charts.yScale} every={charts.every} exportHref={exports.ticks(loop.name)} />
    </div>
  );
});

export const loop: WidgetKind = {
  kind: "loop",
  label: "Loop",
  description: "A control loop's faceplate: setpoint, reading, demand and mode over its process chart.",
  category: "control",
  defaultSize: { w: 6, h: 8 },
  minSize: { w: 3, h: 5 },
  cost: "chart",
  configSchema: (bindings) => ({
    type: "object",
    properties: {
      loop: loopSchema(bindings),
      view: {
        type: "string",
        title: "View",
        default: "compact",
        oneOf: [
          { const: "compact", title: "compact: process chart and readouts" },
          { const: "full", title: "full: process and drive charts, law and feedforward" },
        ],
      },
    },
    required: ["loop"],
  }),
  uiSchema: { ...SELECTS, view: { "ui:widget": "select" } },
  defaultConfig: (bindings) => ({ loop: bindings.loops[0]?.name ?? "", view: "compact" }),
  header: false,
  Component: LoopWidget,
};
