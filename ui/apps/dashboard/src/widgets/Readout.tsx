import { memo, useRef } from "react";
import { Readout, useVisible } from "@flyball/react";
import { useBindings, useRigData, useTraces } from "../dashboard/context.js";
import { Missing } from "./Missing.js";
import { channelKeyOf, channelSchema, SELECTS } from "./schema.js";
import { useFrozen } from "./size.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

const EMPTY: number[] = [];

const ReadoutWidget = memo(function ReadoutWidget({ config }: WidgetComponentProps) {
  const bindings = useBindings();
  const { charts, exports } = useRigData();
  const traces = useTraces();
  const host = useRef<HTMLDivElement>(null);
  const visible = useVisible(host);
  const key = String(config.channel ?? "");
  const channel = bindings.channels.find((c) => channelKeyOf(c) === key);
  const trace = channel ? traces[key] : undefined;
  // Off screen, the sparkline keeps its last arrays: no `setData` until it is seen again.
  const t = useFrozen(trace?.t ?? EMPTY, visible);
  const v = useFrozen(trace?.v ?? EMPTY, visible);
  return (
    <div ref={host} className="fb-fill">
      {channel ? (
        <Readout
          channel={channel}
          t={t}
          v={v}
          sparkline={config.sparkline !== false}
          showSource={config.showSource !== false}
          windowS={charts.windowS}
          every={charts.every}
          exportHref={exports.series(channel)}
        />
      ) : (
        <Missing what="channel" name={key} hint="Configure the widget to pick one of this rig's channels." />
      )}
    </div>
  );
});

export const readout: WidgetKind = {
  kind: "readout",
  label: "Readout",
  description: "One channel: its value, unit, position in range and a sparkline.",
  category: "readings",
  defaultSize: { w: 3, h: 4 },
  minSize: { w: 2, h: 3 },
  cost: "chart",
  configSchema: (bindings) => ({
    type: "object",
    properties: {
      channel: channelSchema(bindings),
      sparkline: { type: "boolean", title: "Sparkline", default: true, description: "A small trace under the value; off, the tile is a number only." },
      showSource: { type: "boolean", title: "Show source", default: true },
    },
    required: ["channel"],
  }),
  uiSchema: SELECTS,
  defaultConfig: (bindings) => ({ channel: bindings.channels[0] ? channelKeyOf(bindings.channels[0]) : "", sparkline: true, showSource: true }),
  titleFor: () => undefined,
  Component: ReadoutWidget,
};
