import { memo, useMemo, useState, type ReactNode, type RefObject } from "react";
import { ResponsiveGridLayout, useContainerWidth, verticalCompactor, type Layout, type LayoutItem } from "react-grid-layout";
import type { DashboardGrid as GridSpec, DashboardWidget } from "@flyball/client";
import { widgetKind } from "../widgets/registry.js";
import { GRID_MARGIN } from "./document.js";

export interface Placement {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface DashboardGridProps {
  widgets: DashboardWidget[];
  grid: GridSpec;
  editing: boolean;
  /** The widgets' new positions after a drag, a resize or a compaction, at the full-width breakpoint only. */
  onLayout(placements: Placement[]): void;
  renderWidget(widget: DashboardWidget): ReactNode;
}

/** Below this container width the grid halves its columns; below `SM` it is one column. Tiles keep their heights. */
const MD = 900;
const SM = 520;

const samePlacement = (widgets: DashboardWidget[], layout: Layout) =>
  layout.every((item) => {
    const w = widgets.find((x) => x.id === item.i);
    return w && w.x === item.x && w.y === item.y && w.w === item.w && w.h === item.h;
  });

/**
 * The tiles on a `react-grid-layout` grid: `grid.cols` across at full width,
 * half that on a narrow container, one column on a phone (the last two
 * derived from the first, never saved). Drag by the tile header and resize
 * by the corner only in edit mode. Memoised so the page's live data flows to
 * the widgets through context, not through this component: a sample never
 * re-renders the grid.
 */
export const DashboardGrid = memo(function DashboardGrid({ widgets, grid, editing, onLayout, renderWidget }: DashboardGridProps) {
  const { width, containerRef, mounted } = useContainerWidth();
  const [breakpoint, setBreakpoint] = useState("lg");
  const full = breakpoint === "lg";
  const layout = useMemo<LayoutItem[]>(
    () =>
      widgets.map((w) => {
        const kind = widgetKind(w.kind);
        return { i: w.id, x: w.x, y: w.y, w: w.w, h: w.h, minW: kind?.minSize.w ?? 1, minH: kind?.minSize.h ?? 1 };
      }),
    [widgets],
  );
  const layouts = useMemo(() => ({ lg: layout }), [layout]);
  const cols = useMemo(() => ({ lg: grid.cols, md: Math.max(1, Math.round(grid.cols / 2)), sm: 1 }), [grid.cols]);
  const children = useMemo(
    () =>
      widgets.map((w) => (
        <div key={w.id} className="dash-item">
          {renderWidget(w)}
        </div>
      )),
    [widgets, renderWidget],
  );
  const dragConfig = useMemo(() => ({ enabled: editing && full, handle: ".fb-tile-drag", cancel: ".fb-tile-menu, button, input, select, textarea, a" }), [editing, full]);
  const resizeConfig = useMemo(() => ({ enabled: editing && full, handles: ["se", "e", "s"] as const }), [editing, full]);
  return (
    <div ref={containerRef as RefObject<HTMLDivElement>} className={`dash-grid${editing ? " dash-editing" : ""}`} data-breakpoint={breakpoint}>
      {mounted && (
        <ResponsiveGridLayout
          width={width}
          breakpoints={{ lg: MD, md: SM, sm: 0 }}
          cols={cols}
          layouts={layouts}
          rowHeight={grid.row_height}
          margin={GRID_MARGIN}
          containerPadding={[0, 0]}
          compactor={verticalCompactor}
          dragConfig={dragConfig}
          resizeConfig={resizeConfig}
          onBreakpointChange={(b) => setBreakpoint(b)}
          onLayoutChange={(current) => {
            if (!full || !editing || samePlacement(widgets, current)) return;
            onLayout(current.map((item) => ({ id: item.i, x: item.x, y: item.y, w: item.w, h: item.h })));
          }}
        >
          {children}
        </ResponsiveGridLayout>
      )}
    </div>
  );
});
