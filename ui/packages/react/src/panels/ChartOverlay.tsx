import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

export interface ChartOverlayProps {
  title: ReactNode;
  onClose(): void;
  children: ReactNode;
}

/**
 * A chart opened full-size: a fixed overlay over the page with the chart
 * filling it, closed by its button, Escape, or a click on the backdrop. No
 * dependency on any UI kit, so it works wherever the panels do; the app's
 * `--fb-*` variables style it.
 */
export function ChartOverlay({ title, onClose, children }: ChartOverlayProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);
  return createPortal(
    <div className="fb-chart-expanded" onClick={onClose}>
      <div className="fb-chart-expanded-panel" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <header className="fb-chart-expanded-head">
          <h3>{title}</h3>
          <button type="button" className="fb-tb fb-chart-close" onClick={onClose} aria-label="Close" title="Close (Esc)">
            ✕
          </button>
        </header>
        <div className="fb-chart-expanded-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

/** Height of the plot (uPlot's `height`: the canvas, not the legend) for a host element, given the chart's height policy. */
export function plotHeight(el: HTMLElement, height: number | "auto", fill: boolean): number {
  if (fill) {
    const legend = el.querySelector<HTMLElement>(".u-legend");
    return Math.max(120, el.clientHeight - (legend?.offsetHeight ?? 0));
  }
  if (height === "auto") return Math.min(360, Math.max(160, Math.round(el.clientWidth * 0.3)));
  return height;
}
