import { useCallback, useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

export interface ChartOverlayProps {
  title: ReactNode;
  onClose(): void;
  children: ReactNode;
}

/**
 * The element focused before an overlay opened. `MultiSeries` re-mounts its
 * toolbar into the overlay, so by the time the overlay's effect runs the
 * expand button that was clicked is gone from the document; this captures it
 * on the way in (a capture-phase `focusin`, module-wide) so the overlay can
 * hand focus back to its replacement on the way out.
 */
let lastFocus: HTMLElement | null = null;
if (typeof document !== "undefined") {
  document.addEventListener("focusin", (e) => (lastFocus = e.target instanceof HTMLElement ? e.target : null), true);
}

/** The expand button (`⤢`) of a chart toolbar under `root`, if any. */
const expandButtonIn = (root: ParentNode | null) => (root ? [...root.querySelectorAll<HTMLButtonElement>(".fb-toolbar .fb-tb")].find((b) => b.textContent?.trim() === "⤢") ?? null : null);

/**
 * A chart opened full-size: a fixed overlay over the page with the chart
 * filling it, closed by its Close button, the toolbar's ⤡, Escape, or a
 * click on the backdrop. On close, focus returns to the expand button it was
 * opened from (or to whatever was focused before, if that still exists). No
 * dependency on any UI kit, so it works wherever the panels do; the app's
 * `--fb-*` variables style it.
 */
/** How long after opening a backdrop click reads as the tail of the gesture that opened us, not a dismissal. */
const SETTLE_MS = 400;

export function ChartOverlay({ title, onClose, children }: ChartOverlayProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  // What opened us was a click, and the *second* click of a double-click lands here, on the
  // backdrop, the instant we mount -- opening and closing again so fast it reads as "nothing
  // happened" (double-clicking a controller's trend). A backdrop dismissal counts
  // only once the pointer has had time to be lifted and put down again.
  const openedAt = useRef(Date.now());
  const dismiss = useCallback(() => {
    if (Date.now() - openedAt.current > SETTLE_MS) onClose();
  }, [onClose]);
  // Where to send focus back, fixed at first render (before our own Close button takes focus): the element
  // focused when we opened, and the container the chart toolbar stood in, where its replacement will be.
  const origin = useRef<{ opener: HTMLElement | null; home: HTMLElement | null } | null>(null);
  if (origin.current === null) origin.current = { opener: lastFocus, home: lastFocus?.closest(".fb-chart-wrap")?.parentElement ?? null };
  useEffect(() => {
    const { opener, home } = origin.current!;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
      // After React has put the toolbar back in the page.
      requestAnimationFrame(() => {
        const target = opener && opener.isConnected && !opener.closest(".fb-chart-expanded") ? opener : expandButtonIn(home);
        target?.focus();
      });
    };
  }, [onClose]);
  return createPortal(
    <div className="fb-chart-expanded" onClick={dismiss}>
      <div className="fb-chart-expanded-panel" role="dialog" aria-modal="true" aria-label={typeof title === "string" ? title : undefined} onClick={(e) => e.stopPropagation()}>
        <header className="fb-chart-expanded-head">
          <h3>{title}</h3>
          <button ref={closeRef} type="button" className="fb-chart-close" onClick={onClose} aria-label="Close" title="Close (Esc)">
            Close <span aria-hidden="true">✕</span>
          </button>
        </header>
        <div className="fb-chart-expanded-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

/** The density-aware chart floor (DESIGN-SPEC.md §2): `--fb-chart-min-h`, 160px comfortable / 140px compact. */
function chartMinH(el: HTMLElement): number {
  const raw = getComputedStyle(el).getPropertyValue("--fb-chart-min-h");
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) ? parsed : 160;
}

/** Height of the plot (uPlot's `height`: the canvas, not the legend) for a host element, given the chart's height policy. */
export function plotHeight(el: HTMLElement, height: number | "auto", fill: boolean): number {
  const minH = chartMinH(el);
  if (fill) {
    const legend = el.querySelector<HTMLElement>(".u-legend");
    return Math.max(minH, el.clientHeight - (legend?.offsetHeight ?? 0));
  }
  if (height === "auto") return Math.min(360, Math.max(minH, Math.round(el.clientWidth * 0.3)));
  return height;
}
