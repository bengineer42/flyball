import { memo, useMemo } from "react";
import { Readout, readoutLevel, Ref, useFreshness, useLatest, useTraceRef, useReaderPeriods } from "@flyball/react";
import { useBindings, useRigData } from "../dashboard/context.js";
import { useWidgetChrome } from "../dashboard/chrome.js";
import { Missing } from "./Missing.js";
import { channelKeyOf, channelSchema, SELECTS } from "./schema.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

/**
 * One channel's value and sparkline, read from the telemetry store: the value
 * re-renders this tile alone (at most 4 Hz), the sparkline draws itself twice
 * a second while on screen. Body only (`bare`): the frame is `WidgetFrame`'s,
 * which gets the channel link, the source and the severity through
 * `useWidgetChrome`.
 */
const ReadoutWidget = memo(function ReadoutWidget({ config, widget }: WidgetComponentProps) {
  const bindings = useBindings();
  const { charts, exports } = useRigData();
  const periods = useReaderPeriods();
  const key = String(config.channel ?? "");
  const channel = bindings.channels.find((c) => channelKeyOf(c) === key);
  const source = useTraceRef(channel ? [channel] : undefined);
  const last = useLatest(channel ? key : undefined)?.v;
  // The channel's reader period, for the B-3 stale threshold (max(3 × period_s, 5s)).
  const reader = channel && Object.values(bindings.schema.readers).find((r) => r.sources.some((s) => s.name === channel.source));
  const fresh = useFreshness(channel ? key : undefined, reader ? periods[reader.name] : undefined);
  const showSource = config.showSource !== false;
  const { level, label, footer } = channel ? readoutLevel(channel, last, fresh) : { level: undefined, label: undefined, footer: undefined };
  const title = useMemo(
    () =>
      channel ? (
        <Ref kind="channel" name={channel.source} measurand={channel.measurand}>
          {channel.label}
        </Ref>
      ) : undefined,
    [channel],
  );
  const subtitle = useMemo(() => (channel && showSource ? <Ref kind="source" name={channel.source}>{bindings.sourceLabel(channel.source)}</Ref> : undefined), [channel, showSource, bindings]);
  useWidgetChrome(channel ? { title, subtitle, severity: level, severityLabel: label, footer } : null);
  if (!channel) return <Missing what="channel" name={key} hint="Configure the widget to pick one of this rig's channels." />;
  // The 44px sparkline needs the fifth row (body 36h − 66: 114 at h=5, 78 at h=4 -- value row and range bar only).
  return <Readout bare channel={channel} source={source} sparkline={config.sparkline !== false && widget.h >= 5} showSource={showSource} windowS={charts.windowS} every={charts.every} exportHref={exports.series(channel)} fresh={fresh} />;
});

export const readout: WidgetKind = {
  kind: "readout",
  label: "Readout",
  description: "One channel: its value, unit, position in range and a sparkline.",
  category: "readings",
  // 6×5: value row 50 + range bar 20 + sparkline 44 = the 114px body exactly (measured, DESIGN-SPEC.md §10).
  // 4×4 is the floor: value and bar in 78px; the sparkline is dropped below h=5. (§3.1 said 4×3; 42px cannot hold a 40px readout-m line.)
  defaultSize: { w: 6, h: 5 },
  minSize: { w: 4, h: 4 },
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
  titleFor: (config, bindings) => {
    const key = String(config.channel ?? "");
    const c = bindings.channels.find((ch) => channelKeyOf(ch) === key);
    return c ? c.label || c.measurand : key;
  },
  Component: ReadoutWidget,
};
