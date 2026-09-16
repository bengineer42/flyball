import { memo, useRef } from "react";
import { Gauge, gaugeKindFor, useFreshness, useLatest, type GaugeKind, useReaderPeriods } from "@flyball/react";
import { alarmLevel } from "@flyball/client";
import { useBindings, useRigData } from "../dashboard/context.js";
import { useWidgetChrome } from "../dashboard/chrome.js";
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
  const { rowHeight } = useRigData();
  const periods = useReaderPeriods();
  const host = useRef<HTMLDivElement>(null);
  // The drawing takes the tile's body less the number under it.
  const height = bodyPx(widget.h, rowHeight, true);
  const key = String(config.channel ?? "");
  const channel = bindings.channels.find((c) => channelKeyOf(c) === key);
  // From the store: this tile alone re-renders on its channel, at most four times a second.
  const value = useLatest(channel ? key : undefined)?.v;
  const reader = channel && Object.values(bindings.schema.readers).find((r) => r.sources.some((s) => s.name === channel.source));
  const fresh = useFreshness(channel ? key : undefined, reader ? periods[reader.name] : undefined);
  const level = channel ? alarmLevel(value, channel, fresh) : undefined;
  // The frame's dot and border carry the level; the gauge itself draws no stale border here (`fresh` stays for the footer age).
  useWidgetChrome(channel ? { severity: level } : null);
  if (!channel) return <Missing what="channel" name={key} />;
  const wanted = String(config.kind ?? "auto");
  const kind: GaugeKind = wanted === "auto" ? gaugeKindFor(channel.unit) : (wanted as GaugeKind);
  // The number under the drawing: 1.3em ≈ 17px line plus the gap.
  return (
    <div ref={host} className={`fb-fill fb-gauge-host fb-gauge-host-${kind} fb-alarm-${level}`}>
      <Gauge channel={channel} value={value} kind={kind} height={kind === "bar" ? 14 : Math.max(48, height - 26)} fresh={fresh} />
    </div>
  );
});

export const gauge: WidgetKind = {
  kind: "gauge",
  label: "Gauge",
  description: "One channel as a picture: a dial, bar, thermometer or tank with its warn and alarm zones.",
  category: "readings",
  // 6×6: a 150px body holds a 124px drawing and the number under it (measured, DESIGN-SPEC.md §10).
  defaultSize: { w: 6, h: 6 },
  minSize: { w: 4, h: 4 },
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
