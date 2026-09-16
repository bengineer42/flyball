import type { ReactNode } from "react";
import { PanelFrame, type PanelSeverity } from "./PanelFrame.js";

export interface TileProps {
  /** The heading; without one (and without a menu) the tile has no header row. */
  title?: ReactNode;
  /** After the title, muted: a unit, a name, a channel. */
  subtitle?: ReactNode;
  /** Before the menu: a badge, a chip, a dot. */
  status?: ReactNode;
  /** At the end of the header: the embedding page's menu button, a drag handle. */
  menu?: ReactNode;
  /** Class on the drag handle the header becomes; the embedding grid names the handle it listens for. */
  handleClassName?: string;
  /** `"fill"` (default): the body is a flex column that fills the tile; `"scroll"`: it scrolls; `"none"`: no padding, the body draws its own edges. */
  body?: "fill" | "scroll" | "none";
  /** Border + dot colour/line-type; omitted draws a plain tile (DESIGN-SPEC.md §2). */
  severity?: PanelSeverity;
  /** 12px fg-2 line under the body, e.g. "last sample 42 s ago". */
  footer?: ReactNode;
  className?: string;
  children: ReactNode;
}

/**
 * A thin adapter over `PanelFrame` (its `menu` is `PanelFrame`'s `actions`),
 * kept for call sites written against the older name. One frame per widget
 * (DESIGN-SPEC.md §10): never mount a `Tile` inside another `PanelFrame` or
 * `WidgetFrame` -- a dashboard widget is a body, and the frame is drawn once
 * by `WidgetFrame`.
 */
export function Tile({ menu, ...rest }: TileProps) {
  return <PanelFrame {...rest} actions={menu} />;
}
