import { memo, useRef } from "react";
import { Gauge, gaugeKindFor, useBandLevel, useReading, type GaugeKind } from "@flyball/react";
import { alarmLevel, deviceOf, signalTitle } from "@flyball/client";
import { useBindings, useRigData } from "../dashboard/context.js";
import { useWidgetChrome } from "../dashboard/chrome.js";
import { isNumeric } from "../valueReadout.js";
import { Missing } from "./Missing.js";
import { signalSchema, SELECTS } from "./schema.js";
import { bodyPx } from "./size.js";
import type { WidgetType, WidgetComponentProps } from "./types.js";

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
  const host = useRef<HTMLDivElement>(null);
  // The drawing takes the tile's body less the number under it.
  const height = bodyPx(widget.h, rowHeight, true);
  const address = String(config.address ?? "");
  const signal = bindings.signalAt(address);
  // From the store: this tile alone re-renders on its signal, at most four times a second.
  const reading = useReading(signal ? address : undefined);
  const value = typeof reading?.value === "number" ? reading.value : null;
  const band = useBandLevel(signal ? address : undefined);
  const level = signal ? alarmLevel(value, signal, band, reading?.quality) : undefined;
  // The frame's dot and border carry the level; the gauge itself draws no stale border here (its footer says why there is no value).
  useWidgetChrome(signal ? { severity: level } : null);
  if (!signal) return <Missing what="signal" name={address} />;
  if (!isNumeric(signal)) return <Missing what="signal" name={address} hint="A gauge needs a numeric signal; this one is not." />;
  const wanted = String(config.kind ?? "auto");
  const kind: GaugeKind = wanted === "auto" ? gaugeKindFor(signal.unit) : (wanted as GaugeKind);
  // The number under the drawing: 1.3em ≈ 17px line plus the gap.
  return (
    <div ref={host} className={`fb-fill fb-gauge-host fb-gauge-host-${kind} fb-alarm-${level}`}>
      <Gauge signal={signal} value={value} kind={kind} height={kind === "bar" ? 14 : Math.max(48, height - 26)} reading={reading} band={band} />
    </div>
  );
});

export const gauge: WidgetType = {
  type: "gauge",
  label: "Gauge",
  description: "One signal as a picture: a dial, bar, thermometer or tank with its warn and alarm zones.",
  category: "readings",
  // 6×6: a 150px body holds a 124px drawing and the number under it (measured, DESIGN-SPEC.md §10).
  defaultSize: { w: 6, h: 6 },
  minSize: { w: 4, h: 4 },
  cost: "cheap",
  configSchema: (bindings) => ({
    type: "object",
    properties: {
      address: signalSchema(bindings, "Signal", true),
      kind: { type: "string", title: "Kind", default: "auto", oneOf: KINDS, description: "By unit: temperatures a thermometer, percentages a tank, else a dial." },
    },
    required: ["address"],
  }),
  uiSchema: { ...SELECTS, kind: { "ui:widget": "select" } },
  defaultConfig: (bindings) => ({ address: bindings.signals.find(isNumeric)?.address ?? "", kind: "auto" }),
  labelFor: (config, bindings) => {
    const address = String(config.address ?? "");
    const s = bindings.signalAt(address);
    return s ? `${bindings.deviceLabel(deviceOf(address))} · ${signalTitle(s, bindings.devices)}` : address;
  },
  Component: GaugeWidget,
};
