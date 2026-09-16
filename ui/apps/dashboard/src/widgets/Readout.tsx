import { memo, useMemo } from "react";
import { Readout, readoutLevel, Ref, useFreshness, useSignal, useTraceRef } from "@flyball/react";
import { describeSignal, deviceOf } from "@flyball/client";
import { useBindings, useRigData } from "../dashboard/context.js";
import { useWidgetChrome } from "../dashboard/chrome.js";
import { Missing } from "./Missing.js";
import { signalSchema, SELECTS } from "./schema.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

/**
 * One signal's value and sparkline, read from the telemetry store: the value
 * re-renders this tile alone (at most 4 Hz), the sparkline draws itself twice
 * a second while on screen. Body only (`bare`): the frame is `WidgetFrame`'s,
 * which gets the signal link, the device and the severity through
 * `useWidgetChrome`.
 */
const ReadoutWidget = memo(function ReadoutWidget({ config, widget }: WidgetComponentProps) {
  const bindings = useBindings();
  const { charts, exports } = useRigData();
  const address = String(config.address ?? "");
  const signal = bindings.signalAt(address);
  const source = useTraceRef(useMemo(() => (signal ? [address] : []), [signal, address]));
  const last = useSignal(signal ? address : undefined)?.v;
  // The B-3 stale threshold (max(3 × period_s, 5s)) with the period of the signal's device, from its run.
  const fresh = useFreshness(signal ? address : undefined);
  const showDevice = config.showDevice !== false;
  const { level, label, footer } = signal ? readoutLevel(signal, last, fresh) : { level: undefined, label: undefined, footer: undefined };
  const title = useMemo(
    () =>
      signal ? (
        <Ref kind="signal" name={address}>
          {describeSignal(signal)}
        </Ref>
      ) : undefined,
    [signal, address],
  );
  const subtitle = useMemo(() => (signal && showDevice ? <Ref kind="device" name={deviceOf(address)}>{bindings.deviceLabel(deviceOf(address))}</Ref> : undefined), [signal, address, showDevice, bindings]);
  useWidgetChrome(signal ? { title, subtitle, severity: level, severityLabel: label, footer } : null);
  if (!signal) return <Missing what="signal" name={address} hint="Configure the widget to pick one of this rig's publishing signals." />;
  // The 44px sparkline needs the fifth row (body 36h − 66: 114 at h=5, 78 at h=4 -- value row and range bar only).
  return <Readout bare signal={signal} source={source} sparkline={config.sparkline !== false && widget.h >= 5} showDevice={showDevice} windowS={charts.windowS} every={charts.every} exportHref={exports.series(address)} fresh={fresh} />;
});

export const readout: WidgetKind = {
  kind: "readout",
  label: "Readout",
  description: "One signal: its value, unit, position in range and a sparkline.",
  category: "readings",
  // 6×5: value row 50 + range bar 20 + sparkline 44 = the 114px body exactly (measured, DESIGN-SPEC.md §10).
  // 4×4 is the floor: value and bar in 78px; the sparkline is dropped below h=5. (§3.1 said 4×3; 42px cannot hold a 40px readout-m line.)
  defaultSize: { w: 6, h: 5 },
  minSize: { w: 4, h: 4 },
  cost: "chart",
  configSchema: (bindings) => ({
    type: "object",
    properties: {
      address: signalSchema(bindings),
      sparkline: { type: "boolean", title: "Sparkline", default: true, description: "A small trace under the value; off, the tile is a number only." },
      showDevice: { type: "boolean", title: "Show device", default: true },
    },
    required: ["address"],
  }),
  uiSchema: SELECTS,
  defaultConfig: (bindings) => ({ address: bindings.signals[0]?.address ?? "", sparkline: true, showDevice: true }),
  titleFor: (config, bindings) => {
    const address = String(config.address ?? "");
    const s = bindings.signalAt(address);
    return s ? describeSignal(s) : address;
  },
  Component: ReadoutWidget,
};
