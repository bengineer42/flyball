import { memo, useEffect, useMemo, useRef, useState } from "react";
import { LoopPanel, Ref, channelKey, useActuatorState, useFreshness, useReaderPeriods, useVisible, type LoopTrace } from "@flyball/react";
import { alarmLevel } from "@flyball/client";
import { useBindings, useLoopsData, useRigData } from "../dashboard/context.js";
import { useWidgetChrome } from "../dashboard/chrome.js";
import { Missing } from "./Missing.js";
import { loopSchema, SELECTS } from "./schema.js";
import { useFrozen } from "./size.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

const EMPTY: LoopTrace = { t: [], reference: [], reading: [], demand: [], expected: [], correction: [] };

// The faceplate's own container query stacks rows above trends below this width
// (packages/react styles.css: `@container (max-width: 39.99rem)`); matched here so the trends get
// a height budget computed from what the tile actually has, not a fixed guess -- a dashboard
// tile's body never scrolls (`.fb-tile-body { overflow: hidden }`), so anything past its measured
// height is simply cut, not just ugly.
const STACK_BREAKPOINT_PX = 640;
const ROWS_BLOCK_PX = 150; // measured: the three rows + captions, roughly fixed regardless of width
const CAPTION_PX = 22;
const TREND_GAP_PX = 8;
const MIN_TREND_PX = 50;

/** How tall each of the two trends may be, and whether there is room for them at all, from the tile's measured box. */
function trendBudget(box: { w: number; h: number }, wanted: boolean): { show: boolean; height: number } {
  if (!wanted || box.h <= 0) return { show: false, height: MIN_TREND_PX };
  const stacked = box.w > 0 && box.w < STACK_BREAKPOINT_PX;
  const available = stacked ? box.h - ROWS_BLOCK_PX - TREND_GAP_PX : box.h;
  const perTrend = Math.floor((available - 2 * CAPTION_PX - TREND_GAP_PX) / 2);
  return { show: perTrend >= MIN_TREND_PX, height: Math.max(MIN_TREND_PX, perTrend) };
}

/** The host element's content box, kept in state so a resize recomputes the trend budget above. */
function useBox(ref: React.RefObject<HTMLElement | null>): { w: number; h: number } {
  const [box, setBox] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const r = entry?.contentRect;
      if (!r) return;
      setBox((prev) => (Math.round(r.width) === prev.w && Math.round(r.height) === prev.h ? prev : { w: Math.round(r.width), h: Math.round(r.height) }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return box;
}

/**
 * A controller's faceplate at a widget's size (§3.4): Reading/Target/Output
 * rows always, the Process/Drive trends beside them (or below, once the
 * faceplate's own container query stacks at a narrow width) when the `full`
 * view is asked for AND the tile's measured box actually has room --
 * `trendBudget` above sizes them from that, since the tile's body never
 * scrolls. Law and feedforward never show here; that stays behind the L3
 * Controllers page's `detail` toggle. No controls: a dashboard widget is a
 * read view. Body only (`bare`): the frame is `WidgetFrame`'s, fed the
 * name/channel/mode through `useWidgetChrome`.
 */
const LoopWidget = memo(function LoopWidget({ config }: WidgetComponentProps) {
  const { charts, exports } = useRigData();
  const { loops, history } = useLoopsData();
  const bindings = useBindings();
  const host = useRef<HTMLDivElement>(null);
  const visible = useVisible(host);
  const box = useBox(host);
  const name = String(config.loop ?? "");
  const loop = loops[name];
  const wantsTrends = config.view === "full";
  const { show: trends, height: trendHeight } = trendBudget(box, wantsTrends);
  const trace = useFrozen(history[name] ?? EMPTY, visible);
  // The loop is named for its actuator; read the live state from the store directly
  // (as the Actuator widget does) rather than the dashboard's `states` context, which
  // Dashboards.tsx leaves empty on purpose -- widgets read samples/loops/states themselves.
  const state = useActuatorState(name);
  const periods = useReaderPeriods();
  // Reader-offline (B-3): the channel's own staleness, its reader's period against its last sample.
  const reader = loop && Object.values(bindings.schema.readers).find((r) => r.sources.some((s) => s.name === loop.channel.source));
  const fresh = useFreshness(loop ? channelKey(loop.channel) : undefined, reader ? periods[reader.name] : undefined);
  const readerOffline = alarmLevel(null, {}, fresh) === "stale";
  const title = useMemo(() => (loop ? <Ref kind="loop" name={loop.name}>{loop.label ?? loop.name}</Ref> : undefined), [loop?.name, loop?.label]);
  const subtitle = useMemo(
    () => (loop ? <Ref kind="channel" name={loop.channel.source} measurand={loop.channel.measurand} /> : undefined),
    [loop?.channel.source, loop?.channel.measurand],
  );
  const status = useMemo(() => (loop ? <span className={`fb-badge fb-mode fb-mode-${loop.mode}`}>{loop.mode}</span> : undefined), [loop?.mode]);
  useWidgetChrome(loop ? { title, subtitle, status, severity: readerOffline ? "stale" : undefined } : null);
  if (!loop) return <Missing what="loop" name={name} hint={bindings.loops.length ? "Configure the widget to pick one of this rig's loops." : "This rig has no loops."} />;
  const outputRange = state?.output_range ?? null;
  return (
    <div ref={host} className="fb-fill fb-loop-host">
      <LoopPanel loop={loop} history={trace} trends={trends} trendHeight={trendHeight} outputRange={outputRange} readerOffline={readerOffline} windowS={charts.windowS} yScale={charts.yScale} every={charts.every} exportHref={exports.ticks(loop.name)} bare />
    </div>
  );
});

export const loop: WidgetKind = {
  kind: "loop",
  label: "Controller",
  description: "A controller's faceplate: reading, target and output rows, with the Process/Drive trends beside them.",
  category: "control",
  defaultSize: { w: 8, h: 8 },
  minSize: { w: 6, h: 4 },
  cost: "chart",
  configSchema: (bindings) => ({
    type: "object",
    properties: {
      loop: loopSchema(bindings),
      view: {
        type: "string",
        title: "View",
        default: "full",
        oneOf: [
          { const: "compact", title: "compact: reading/target/output rows only" },
          { const: "full", title: "full: rows and the Process/Drive trends" },
        ],
      },
    },
    required: ["loop"],
  }),
  uiSchema: { ...SELECTS, view: { "ui:widget": "select" } },
  defaultConfig: (bindings) => ({ loop: bindings.loops[0]?.name ?? "", view: "full" }),
  // Fallback title before `useWidgetChrome`'s richer one (a `Ref` link) lands, and while editing.
  titleFor: (config, bindings) => {
    const name = String(config.loop ?? "");
    if (!name) return undefined;
    return bindings.loops.find((l) => l.name === name)?.label || name;
  },
  Component: LoopWidget,
};
