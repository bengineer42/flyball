import { memo, useEffect, useMemo, useState, type CSSProperties, type ReactNode, type RefObject } from "react";
import { ResponsiveGridLayout, useContainerWidth, verticalCompactor, type Layout, type LayoutItem } from "react-grid-layout";
import type { DashboardGrid as GridSpec, DashboardWidget } from "@flyball/client";
import { widgetKind } from "../widgets/registry.js";
import { GRID_MARGIN } from "./document.js";
import { gridGap } from "../widgets/size.js";

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
  renderWidget(widget: DashboardWidget): ReactNode;
}

export interface DashboardEditGridProps extends DashboardGridProps {
  /** The widgets' new positions after a drag, a resize or a compaction, at the full-width breakpoint only. */
  onLayout(placements: Placement[]): void;
}

/** Below this container width the grid halves its columns; below `SM` it is one column. Tiles keep their heights. */
const MD = 900;
const SM = 520;
/** Stable references: an inline object/array literal here would be a new identity every render, which is what fed
 * react-grid-layout's internal `deepEqual` effects a changed prop on every tick and caused the "Maximum update
 * depth exceeded" loop on this page (only reproduced with RGL mounted; view mode no longer mounts it at all). */
const BREAKPOINTS = { lg: MD, md: SM, sm: 0 };
const CONTAINER_PADDING: [number, number] = [0, 0];

const samePlacement = (widgets: DashboardWidget[], layout: Layout) =>
  layout.every((item) => {
    const w = widgets.find((x) => x.id === item.i);
    return w && w.x === item.x && w.y === item.y && w.w === item.w && w.h === item.h;
  });

/**
 * View mode: a plain CSS grid, `grid-column`/`grid-row` per widget, no drag
 * library mounted -- no transforms, no ResizeObserver per item, nothing to
 * subscribe or reflow while an operator only watches. Below 900px it stacks
 * full-width in `(y, x)` document order (the same order the tiles are laid
 * out in here), which is also the DOM order screen readers and Tab see.
 */
export const DashboardViewGrid = memo(function DashboardViewGrid({ widgets, grid, renderWidget }: DashboardGridProps) {
  const ordered = useMemo(() => [...widgets].sort((a, b) => a.y - b.y || a.x - b.x), [widgets]);
  const style = { "--dash-cols": grid.cols, "--dash-row": `${grid.row_height}px` } as CSSProperties;
  return (
    <div className="dash-view-grid" style={style}>
      {ordered.map((w) => (
        <div key={w.id} className="dash-view-item" style={{ gridColumn: `${w.x + 1} / span ${w.w}`, gridRow: `${w.y + 1} / span ${w.h}`, "--dash-span": w.h } as CSSProperties}>
          {renderWidget(w)}
        </div>
      ))}
    </div>
  );
});

/**
 * Edit mode only: the tiles on a `react-grid-layout` grid, `grid.cols`
 * across at full width, half that on a narrow container, one column on a
 * phone (the last two derived from the first, never saved). Drag by the
 * tile header and resize by the corner. Memoised so the page's live data
 * flows to the widgets through context, not through this component: a
 * sample never re-renders the grid.
 */
export const DashboardEditGrid = memo(function DashboardEditGrid({ widgets, grid, onLayout, renderWidget }: DashboardEditGridProps) {
  const { width, containerRef, mounted } = useContainerWidth();
  const [breakpoint, setBreakpoint] = useState("lg");
  // The gutter is the view grid's `gap: var(--fb-gap)` (dashboard.css), read once so edit and view mode place tiles identically.
  const [margin, setMargin] = useState<[number, number]>(GRID_MARGIN as [number, number]);
  useEffect(() => {
    const gap = gridGap();
    setMargin((m) => (m[0] === gap ? m : [gap, gap]));
  }, []);
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
  const dragConfig = useMemo(() => ({ enabled: full, handle: ".fb-tile-drag", cancel: ".fb-tile-menu, button, input, select, textarea, a" }), [full]);
  const resizeConfig = useMemo(() => ({ enabled: full, handles: ["se", "e", "s"] as const }), [full]);
  return (
    <div ref={containerRef as RefObject<HTMLDivElement>} className="dash-grid dash-editing" data-breakpoint={breakpoint}>
      {mounted && (
        <ResponsiveGridLayout
          width={width}
          breakpoints={BREAKPOINTS}
          cols={cols}
          layouts={layouts}
          rowHeight={grid.row_height}
          margin={margin}
          containerPadding={CONTAINER_PADDING}
          compactor={verticalCompactor}
          dragConfig={dragConfig}
          resizeConfig={resizeConfig}
          onBreakpointChange={(b) => setBreakpoint(b)}
          onLayoutChange={(current) => {
            if (!full || samePlacement(widgets, current)) return;
            onLayout(current.map((item) => ({ id: item.i, x: item.x, y: item.y, w: item.w, h: item.h })));
          }}
        >
          {children}
        </ResponsiveGridLayout>
      )}
    </div>
  );
});
