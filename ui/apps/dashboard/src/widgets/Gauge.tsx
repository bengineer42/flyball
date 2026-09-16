import { memo, useRef } from "react";
import { Gauge, gaugeKindFor, type GaugeKind } from "@flyball/react";
import { alarmLevel } from "@flyball/client";
import { useBindings, useRigData, useTraces } from "../dashboard/context.js";
import { Missing } from "./Missing.js";
import { channelKeyOf, channelSchema, SELECTS } from "./schema.js";
import { bodyPx } from "./size.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

const KINDS: Array<{ const: string; title: string }> = [
  { const: "auto", title: "by unit" },
  { const: "dial", title: "dial" },
  { const: "bar", title: "bar" },
  { const: "thermometer", title: "thermometer" },
  { const: "tank", title: "tank" },
];

const GaugeWidget = memo(function GaugeWidget({ config, widget }: WidgetComponentProps) {
  const bindings = useBindings();
  const traces = useTraces();
  const { rowHeight } = useRigData();
  const host = useRef<HTMLDivElement>(null);
  // The drawing takes the tile's body less the number under it.
  const height = bodyPx(widget.h, rowHeight, true);
  const key = String(config.channel ?? "");
  const channel = bindings.channels.find((c) => channelKeyOf(c) === key);
  if (!channel) return <Missing what="channel" name={key} />;
  const trace = traces[key];
  const value = trace && trace.v.length ? trace.v[trace.v.length - 1] : undefined;
  const wanted = String(config.kind ?? "auto");
  const kind: GaugeKind = wanted === "auto" ? gaugeKindFor(channel.unit) : (wanted as GaugeKind);
  const level = alarmLevel(value, channel);
  return (
    <div ref={host} className={`fb-fill fb-gauge-host fb-gauge-host-${kind} fb-alarm-${level}`}>
      <Gauge channel={channel} value={value} kind={kind} height={kind === "bar" ? 14 : Math.max(48, height - 34)} />
    </div>
  );
});

export const gauge: WidgetKind = {
  kind: "gauge",
  label: "Gauge",
  description: "One channel as a picture: a dial, bar, thermometer or tank with its warn and alarm zones.",
  category: "readings",
  defaultSize: { w: 2, h: 5 },
  minSize: { w: 2, h: 3 },
  cost: "cheap",
  configSchema: (bindings) => ({
    type: "object",
    properties: {
      channel: channelSchema(bindings),
      kind: { type: "string", title: "Kind", default: "auto", oneOf: KINDS, description: "By unit: temperatures a thermometer, percentages a tank, else a dial." },
    },
    required: ["channel"],
  }),
  uiSchema: { ...SELECTS, kind: { "ui:widget": "select" } },
  defaultConfig: (bindings) => ({ channel: bindings.channels[0] ? channelKeyOf(bindings.channels[0]) : "", kind: "auto" }),
  titleFor: (config, bindings) => {
    const key = String(config.channel ?? "");
    const c = bindings.channels.find((ch) => channelKeyOf(ch) === key);
    return c ? `${bindings.sourceLabel(c.source)} · ${c.label || c.measurand}` : key;
  },
  Component: GaugeWidget,
};
