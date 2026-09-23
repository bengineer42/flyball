import { memo, useEffect, useMemo, useRef, useState } from "react";
import { ControllerPanel, Ref, useFreshness, useVisible, type ControllerTrace } from "@flyball/react";
import { alarmLevel, describeController, signalsOf, signalTitle } from "@flyball/client";
import { useBindings, useControllersData, useRigData } from "../dashboard/context.js";
import { useWidgetChrome } from "../dashboard/chrome.js";
import { Missing } from "./Missing.js";
import { controllerSchema, SELECTS } from "./schema.js";
import { useFrozen } from "./size.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

const EMPTY: ControllerTrace = { t: [], reference: [], measured: [], output: [], expected: [], correction: [] };

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
 * A controller's faceplate at a widget's size (§3.4): Measured/Setpoint/Output
 * rows always, the Process/Drive trends beside them (or below, once the
 * faceplate's own container query stacks at a narrow width) when the `full`
 * view is asked for AND the tile's measured box actually has room --
 * `trendBudget` above sizes them from that, since the tile's body never
 * scrolls. Law and feedforward never show here; that stays behind the L3
 * Controllers page's `detail` toggle. No controls: a dashboard widget is a
 * read view. Body only (`bare`): the frame is `WidgetFrame`'s, fed the
 * name/source/mode through `useWidgetChrome`. The document's kind stays
 * `loop` (the wire's name for the widget); its binding is `controller`.
 */
const ControllerWidget = memo(function ControllerWidget({ config }: WidgetComponentProps) {
  const { charts, exports } = useRigData();
  const { controllers, history } = useControllersData();
  const bindings = useBindings();
  const host = useRef<HTMLDivElement>(null);
  const visible = useVisible(host);
  const box = useBox(host);
  const name = String(config.controller ?? "");
  const controller = controllers[name];
  const wantsTrends = config.view === "full";
  const { show: trends, height: trendHeight } = trendBudget(box, wantsTrends);
  const trace = useFrozen(history[name] ?? EMPTY, visible);
  // The source signal (units, bands) from the bindings; the target (limits) from its device's tree, which may not publish.
  const source = controller ? bindings.signalAt(controller.measured_signal) : undefined;
  const target = controller ? bindings.devices.flatMap((d) => signalsOf(d.signals)).find((s) => s.address === controller.output_signal) : undefined;
  // Source-offline (B-3): the source signal's own staleness, its device's period against its last sample.
  const fresh = useFreshness(controller?.measured_signal);
  const offline = alarmLevel(null, {}, fresh) === "stale";
  // A controller is named by its target's label; a target with none is titled like any signal, never by its address.
  const title = useMemo(
    () => (controller ? <Ref kind="controller" name={controller.name}>{controller.label ? describeController(controller) : target ? signalTitle(target, bindings.devices) : controller.name}</Ref> : undefined),
    [controller?.name, controller?.label, target, bindings], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const subtitle = useMemo(
    () => (controller ? <Ref kind="signal" name={controller.measured_signal}>{source ? `regulates ${signalTitle(source, bindings.devices)}` : controller.measured_signal}</Ref> : undefined),
    [controller?.measured_signal, source, bindings], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const status = useMemo(() => (controller ? <span className={`fb-badge fb-mode fb-mode-${controller.mode}`}>{controller.mode}</span> : undefined), [controller?.mode]); // eslint-disable-line react-hooks/exhaustive-deps
  useWidgetChrome(controller ? { title, subtitle, status, severity: offline ? "stale" : undefined } : null);
  if (!controller) return <Missing what="controller" name={name} hint={bindings.controllers.length ? "Configure the widget to pick one of this rig's controllers." : "This rig has no controllers."} />;
  if (!source) return <Missing what="signal" name={controller.measured_signal} hint="The controller's source does not publish on this rig." />;
  return (
    <div ref={host} className="fb-fill fb-loop-host">
      <ControllerPanel controller={controller} source={source} target={target} history={trace} trends={trends} trendHeight={trendHeight} windowS={charts.windowS} yScale={charts.yScale} exportHref={exports.ticks(controller.name)} bare />
    </div>
  );
});

export const loop: WidgetKind = {
  kind: "loop",
  label: "Controller",
  description: "A controller's faceplate: measured, setpoint and output rows, with the Process/Drive trends beside them.",
  category: "control",
  defaultSize: { w: 8, h: 8 },
  minSize: { w: 6, h: 4 },
  cost: "chart",
  configSchema: (bindings) => ({
    type: "object",
    properties: {
      controller: controllerSchema(bindings),
      view: {
        type: "string",
        title: "View",
        default: "full",
        oneOf: [
          { const: "compact", title: "compact: measured/setpoint/output rows only" },
          { const: "full", title: "full: rows and the Process/Drive trends" },
        ],
      },
    },
    required: ["controller"],
  }),
  uiSchema: { ...SELECTS, view: { "ui:widget": "select" } },
  defaultConfig: (bindings) => ({ controller: bindings.controllers[0]?.name ?? "", view: "full" }),
  // Fallback title before `useWidgetChrome`'s richer one (a `Ref` link) lands, and while editing.
  titleFor: (config, bindings) => {
    const name = String(config.controller ?? "");
    if (!name) return undefined;
    const c = bindings.controllers.find((l) => l.name === name);
    return c ? describeController(c) : name;
  },
  Component: ControllerWidget,
};
