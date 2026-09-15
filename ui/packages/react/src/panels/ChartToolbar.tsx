import type uPlot from "uplot";
import type { Navigation } from "./navigation.js";

export interface ChartToolbarProps {
  nav: Navigation;
  chart: () => uPlot | null;
  following: boolean;
  /** Fit the y axis to the data again after a fixed range was set. */
  onFitY?: () => void;
}

/** Back / forward / zoom / fit / live, for the chart above it. Wheel zooms, drag pans, double-click returns to live. */
export function ChartToolbar({ nav, chart, following, onFitY }: ChartToolbarProps) {
  const run = (f: (u: uPlot) => void) => () => {
    const u = chart();
    if (u) f(u);
  };
  const btn = (label: string, title: string, onClick: () => void, active = false) => (
    <button type="button" className={`fb-tb${active ? " active" : ""}`} title={title} onClick={onClick}>
      {label}
    </button>
  );
  return (
    <span className="fb-toolbar" role="toolbar" aria-label="chart navigation">
      {btn("◀", "Back half a window", run((u) => nav.step(u, -0.5)))}
      {btn("▶", "Forward half a window", run((u) => nav.step(u, 0.5)))}
      {btn("−", "Zoom out", run((u) => nav.zoom(u, 1.25)))}
      {btn("+", "Zoom in", run((u) => nav.zoom(u, 0.8)))}
      {btn("fit x", "Show everything held", run((u) => nav.fitX(u)))}
      {onFitY && btn("fit y", "Fit the y axis to the data", onFitY)}
      {btn("live", following ? "Following the newest data" : "Return to live: follow the newest data", run((u) => nav.follow(u)), following)}
    </span>
  );
}
