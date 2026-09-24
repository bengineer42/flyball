import { Component, memo, useCallback, useMemo, useState, type ErrorInfo, type ReactNode } from "react";
import { IconButton, ListItemIcon, ListItemText, Menu, MenuItem, Tooltip } from "@mui/material";
import MoreVertIcon from "@mui/icons-material/MoreVert";
import DragIndicatorIcon from "@mui/icons-material/DragIndicator";
import TuneIcon from "@mui/icons-material/Tune";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import { PanelFrame } from "@flyball/react";
import type { DashboardWidget } from "@flyball/client";
import { widgetType } from "../widgets/registry.js";
import { Missing } from "../widgets/Missing.js";
import { useBindings } from "./context.js";
import { sameChrome, WidgetChromeContext, type WidgetChrome } from "./chrome.js";

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
    return this.state.error ? <Missing what="widget" name={this.state.error.message} failed hint="It threw while drawing; reconfigure or remove it." /> : this.props.children;
  }
}

export interface WidgetFrameProps {
  widget: DashboardWidget;
  editing: boolean;
  onAction(id: string, action: WidgetAction): void;
}

/**
 * The ONE frame around one widget (DESIGN-SPEC.md §2 "Widget / panel chrome",
 * §10 one-frame rule): `PanelFrame` draws the title row -- status dot, title,
 * subtitle, a text status, the actions (drag grip and `⋯` menu in edit mode)
 * -- and the widget draws its body only, frameless, inside `.fb-tile-body`.
 * A widget never mounts a `PanelFrame`/`Tile` of its own. Library panels that
 * carry their own chrome for the L3 pages (`Readout`, `ControllerPanel`,
 * `WritePanel`) take a `bare` prop that drops it; a widget wrapping one
 * passes `bare` and hands what the head needs (severity, subtitle, footer,
 * a mode chip) up through `useWidgetChrome` (./chrome.ts).
 *
 * Title precedence: the document's `label` › the widget's chrome `title`
 * (a `Ref` link) › `type.labelFor(config)` › in edit mode the type's label.
 * Types with `header: false` (heading, spacer, link) get no title row in
 * view mode and a plain one in edit mode, for the grip and the menu.
 *
 * Memoised on the widget, so a drag of another tile or a sample arriving
 * does not touch it; the widget itself reads live data from context, and
 * only a change of chrome (severity, footer) re-renders this frame.
 */
export const WidgetFrame = memo(function WidgetFrame({ widget, editing, onAction }: WidgetFrameProps) {
  const bindings = useBindings();
  const type = widgetType(widget.type);
  const [menu, setMenu] = useState<HTMLElement | null>(null);
  const [chrome, setChromeState] = useState<WidgetChrome | null>(null);
  const setChrome = useCallback((next: WidgetChrome | null) => setChromeState((prev) => (sameChrome(prev, next) ? prev : next)), []);
  const own = widget.label ?? type?.labelFor?.(widget.config, bindings);
  const header = editing || type?.header !== false;
  const title = header ? widget.label || chrome?.title || own || (editing ? type?.label ?? widget.type : undefined) : undefined;
  // The widget's own subtitle (device, signal) wins; edit mode names the type where there is none and the title does not already.
  const subtitle = header ? chrome?.subtitle ?? (editing && own && own !== type?.label ? type?.label : undefined) : undefined;
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
        <MenuItem data-testid="widget-configure" onClick={() => act("configure")}>
          <ListItemIcon>
            <TuneIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Configure…</ListItemText>
        </MenuItem>
        <MenuItem data-testid="widget-duplicate" onClick={() => act("duplicate")}>
          <ListItemIcon>
            <ContentCopyIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Duplicate</ListItemText>
        </MenuItem>
        <MenuItem data-testid="widget-remove" onClick={() => act("remove")} sx={{ color: "error.main" }}>
          <ListItemIcon>
            <DeleteOutlineIcon fontSize="small" color="error" />
          </ListItemIcon>
          <ListItemText>Remove</ListItemText>
        </MenuItem>
      </Menu>
    </>
  ) : undefined;
  // `body="none"` for the frameless layout types: their content is the whole cell (a 1-row link is 24px tall).
  const body = type?.header === false ? "none" : "fill";
  // A widget with no title of its own (the health strip, a heading, a link) has no title row in view mode, so its
  // height holds its body alone. In edit mode it still needs the grip and the menu: they float in the top-right
  // corner (`.fb-tile-editing-float`, dashboard.css) instead of pushing a 28px row into a cell that has no room for one.
  const floating = editing && !(widget.label || chrome?.title || own);
  const boundary = useMemo(
    () => (
      <Boundary key={JSON.stringify(widget.config)}>
        {type ? <type.Component widget={widget} config={widget.config} editing={editing} /> : <Missing what="widget type" name={widget.type} hint="This app has no widget of that type; it was saved by another version." />}
      </Boundary>
    ),
    [type, widget, editing],
  );
  return (
    <WidgetChromeContext.Provider value={setChrome}>
      <PanelFrame
        title={title}
        subtitle={subtitle}
        status={header ? chrome?.status : undefined}
        severity={header ? chrome?.severity : undefined}
        severityLabel={chrome?.severityLabel}
        footer={chrome?.footer}
        actions={controls}
        handleClassName={editing ? "fb-tile-drag" : undefined}
        className={["dash-widget", `dash-widget-${widget.type}`, editing ? "fb-tile-editing" : "", floating ? "fb-tile-editing-float" : ""].filter(Boolean).join(" ")}
        body={body}
      >
        {boundary}
      </PanelFrame>
    </WidgetChromeContext.Provider>
  );
});
