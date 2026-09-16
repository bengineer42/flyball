import { Component, memo, useState, type ErrorInfo, type ReactNode } from "react";
import { IconButton, ListItemIcon, ListItemText, Menu, MenuItem, Tooltip } from "@mui/material";
import MoreVertIcon from "@mui/icons-material/MoreVert";
import DragIndicatorIcon from "@mui/icons-material/DragIndicator";
import TuneIcon from "@mui/icons-material/Tune";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import { Tile } from "@flyball/react";
import type { DashboardWidget } from "@flyball/client";
import { widgetKind } from "../widgets/registry.js";
import { Missing } from "../widgets/Missing.js";
import { useBindings } from "./context.js";

export type WidgetAction = "configure" | "duplicate" | "remove";

/** A widget that throws stays a tile that says so; the rest of the dashboard is unaffected. */
class Boundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  override state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  override componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("widget failed", error, info.componentStack);
  }
  override render() {
    return this.state.error ? <Missing what="widget" name={this.state.error.message} hint="It threw while drawing; reconfigure or remove it." /> : this.props.children;
  }
}

export interface WidgetFrameProps {
  widget: DashboardWidget;
  editing: boolean;
  onAction(id: string, action: WidgetAction): void;
}

/**
 * The tile around one widget: title from the document or the kind, the
 * kind's component inside, and in edit mode a drag handle and a menu.
 * Memoised on the widget, so a drag of another tile or a sample arriving
 * does not touch it; the widget itself reads live data from context.
 */
export const WidgetFrame = memo(function WidgetFrame({ widget, editing, onAction }: WidgetFrameProps) {
  const bindings = useBindings();
  const kind = widgetKind(widget.kind);
  const [menu, setMenu] = useState<HTMLElement | null>(null);
  const own = widget.title ?? kind?.titleFor?.(widget.config, bindings);
  const header = editing || kind?.header !== false || Boolean(own);
  const title = header ? own || (editing ? kind?.label ?? widget.kind : undefined) : undefined;
  const act = (action: WidgetAction) => {
    setMenu(null);
    onAction(widget.id, action);
  };
  const controls = editing ? (
    <>
      <Tooltip title="Drag to move">
        <DragIndicatorIcon fontSize="small" className="fb-tile-grip" sx={{ color: "text.disabled" }} />
      </Tooltip>
      <IconButton size="small" aria-label={`widget menu ${widget.id}`} onClick={(e) => setMenu(e.currentTarget)}>
        <MoreVertIcon fontSize="small" />
      </IconButton>
      <Menu open={Boolean(menu)} anchorEl={menu} onClose={() => setMenu(null)}>
        <MenuItem onClick={() => act("configure")}>
          <ListItemIcon>
            <TuneIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Configure…</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => act("duplicate")}>
          <ListItemIcon>
            <ContentCopyIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Duplicate</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => act("remove")} sx={{ color: "error.main" }}>
          <ListItemIcon>
            <DeleteOutlineIcon fontSize="small" color="error" />
          </ListItemIcon>
          <ListItemText>Remove</ListItemText>
        </MenuItem>
      </Menu>
    </>
  ) : undefined;
  return (
    <Tile
      title={title}
      subtitle={editing && own ? kind?.label : undefined}
      menu={controls}
      handleClassName={editing ? "fb-tile-drag" : undefined}
      className={[`dash-widget-${widget.kind}`, editing ? "fb-tile-editing" : ""].filter(Boolean).join(" ")}
      body={widget.kind === "spacer" ? "none" : "fill"}
    >
      <Boundary key={JSON.stringify(widget.config)}>
        {kind ? <kind.Component widget={widget} config={widget.config} editing={editing} /> : <Missing what="widget kind" name={widget.kind} hint="This app has no widget of that kind; it was saved by another version." />}
      </Boundary>
    </Tile>
  );
});
