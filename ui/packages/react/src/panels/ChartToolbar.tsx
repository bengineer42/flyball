import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import type uPlot from "uplot";
import type { Navigation } from "./navigation.js";

export interface ChartToolbarProps {
  nav: Navigation;
  chart: () => uPlot | null;
  following: boolean;
  /** Fit the y axis to the data again after a fixed range was set. */
  onFitY?: () => void;
  /** What the y axis carries, for its fit button: a unit ("°C", "W") or a quantity; "y" when mixed or unitless. */
  yLabel?: string;
  /** Open the chart full-size (or close it again when `expanded`). */
  onExpand?: () => void;
  expanded?: boolean;
  /** Write what the chart is holding as a file; the download menu calls it per format. */
  onDownload?: (format: "csv" | "json") => void;
  /** The same data in the store, as an export URL; offered beside the two above. */
  exportHref?: string;
}

/** Back / forward / zoom / fit / live / download / expand, for the chart above it. Wheel zooms, drag pans, double-click opens the chart full-size. */
export function ChartToolbar({ nav, chart, following, onFitY, yLabel, onExpand, expanded = false, onDownload, exportHref }: ChartToolbarProps) {
  const [menu, setMenu] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!menu) return;
    const away = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setMenu(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenu(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", key);
    };
  }, [menu]);

  const run = (f: (u: uPlot) => void) => () => {
    const u = chart();
    if (u) f(u);
  };
  const btn = (label: string, title: string, onClick: () => void, active = false, keys?: string) => (
    <button type="button" className={`fb-tb${active ? " active" : ""}`} title={keys ? `${title} (${keys})` : title} aria-keyshortcuts={keys} onClick={onClick}>
      {label}
    </button>
  );
  const save = (format: "csv" | "json") => () => {
    setMenu(false);
    onDownload?.(format);
  };
  // Pan/zoom/reset/live by keyboard, for whichever chart's toolbar has focus (DESIGN-SPEC §7 B-9):
  // arrows step half a window, +/- zoom about the centre, 0 resets to everything held (as "fit time"
  // does), l returns to live. A plain keydown on the toolbar, not a roving tabindex: the toolbar is one
  // stop in the page's tab order and its own buttons need not each repeat every shortcut.
  const onKeyDown = (e: ReactKeyboardEvent<HTMLSpanElement>) => {
    const u = chart();
    if (!u) return;
    switch (e.key) {
      case "ArrowLeft":
        nav.step(u, -0.5);
        break;
      case "ArrowRight":
        nav.step(u, 0.5);
        break;
      case "-":
      case "_":
        nav.zoom(u, 1.25);
        break;
      case "+":
      case "=":
        nav.zoom(u, 0.8);
        break;
      case "0":
        nav.fitX(u);
        break;
      case "l":
      case "L":
        nav.follow(u);
        break;
      default:
        return;
    }
    e.preventDefault();
  };
  return (
    <span className="fb-toolbar" role="toolbar" aria-label="chart navigation" onKeyDown={onKeyDown}>
      {btn("◀", "Back half a window", run((u) => nav.step(u, -0.5)), false, "ArrowLeft")}
      {btn("▶", "Forward half a window", run((u) => nav.step(u, 0.5)), false, "ArrowRight")}
      {btn("−", "Zoom out", run((u) => nav.zoom(u, 1.25)), false, "-")}
      {btn("+", "Zoom in", run((u) => nav.zoom(u, 0.8)), false, "+")}
      {btn("fit time", "Show everything held on the time axis", run((u) => nav.fitX(u)), false, "0")}
      {onFitY && btn(`fit ${yLabel || "y"}`, `Fit the ${yLabel ? `${yLabel} axis` : "y axis"} to the data`, onFitY)}
      {btn("live", following ? "Following the newest data" : "Return to live: follow the newest data", run((u) => nav.follow(u)), following, "l")}
      {(onDownload || exportHref) && (
        <span className="fb-tb-menu-wrap" ref={wrap}>
          <button
            type="button"
            className={`fb-tb${menu ? " active" : ""}`}
            title="Download this chart's data"
            aria-label="Download this chart's data"
            aria-haspopup="menu"
            aria-expanded={menu}
            onClick={() => setMenu(!menu)}
          >
            ⭳
          </button>
          {menu && (
            <span className="fb-tb-menu" role="menu">
              {onDownload && (
                <button type="button" role="menuitem" className="fb-tb-menu-item" onClick={save("csv")}>
                  CSV — what's shown
                </button>
              )}
              {onDownload && (
                <button type="button" role="menuitem" className="fb-tb-menu-item" onClick={save("json")}>
                  JSON — what's shown
                </button>
              )}
              {exportHref && (
                <a role="menuitem" className="fb-tb-menu-item" href={exportHref} download onClick={() => setMenu(false)}>
                  CSV from the store
                </a>
              )}
            </span>
          )}
        </span>
      )}
      {onExpand && btn(expanded ? "⤡" : "⤢", expanded ? "Back to the page (Esc)" : "Open full-size (or double-click the chart)", onExpand)}
    </span>
  );
}
