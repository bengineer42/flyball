import { useEffect, useRef, useState, type ReactNode } from "react";

export type PanelSeverity = "ok" | "warn" | "alarm" | "stale";

export interface PanelFrameProps {
  /** The heading; without one (and without status/actions/severity) the frame has no header row. */
  title?: ReactNode;
  /** After the title, muted: a unit, a name, a channel. 12px. */
  subtitle?: ReactNode;
  /** Before actions, always visible: a mode chip or other text status (DESIGN-SPEC.md §2 — chips stay only where text matters). */
  status?: ReactNode;
  /** At the end of the header: menu button, drag handle. 35% opacity, 100% on hover/focus-within. */
  actions?: ReactNode;
  /** Class the header also carries, for an embedding grid's drag-handle selector. */
  handleClassName?: string;
  /**
   * "ok" (default when omitted, no dot): plain border. "warn"/"alarm": coloured
   * solid/double border, filled dot, one-shot pulse on entering the level.
   * "stale": dashed border, hollow dot, no pulse — DESIGN-SPEC.md §2.
   */
  severity?: PanelSeverity;
  /** Tooltip on the dot; defaults to the severity's name. */
  severityLabel?: string;
  /** 12px fg-2 line under the body, e.g. "5m · 1/2" or "last sample 42 s ago". */
  footer?: ReactNode;
  /** `"fill"` (default): the body is a flex column that fills the frame; `"scroll"`: it scrolls; `"none"`: no padding, the body draws its own edges. */
  body?: "fill" | "scroll" | "none";
  className?: string;
  children: ReactNode;
}

const SEVERITY_LABEL: Record<PanelSeverity, string> = {
  ok: "normal",
  warn: "warning",
  alarm: "alarm",
  stale: "stale — no recent sample",
};

/**
 * Chrome for every panel and dashboard widget (DESIGN-SPEC.md §2 "Widget /
 * panel chrome"): a 28px title row (status dot, title, unit/subtitle,
 * hover-revealed actions), a body, and an optional footer. `severity` drives
 * the border and dot together (colour + line type, so it reads without
 * colour too) and plays a one-shot border pulse when it enters warn/alarm.
 * Reuses the `.fb-tile*` classes so it drops in wherever `Tile` already did.
 */
export function PanelFrame({
  title,
  subtitle,
  status,
  actions,
  handleClassName,
  severity,
  severityLabel,
  footer,
  body = "fill",
  className,
  children,
}: PanelFrameProps) {
  const prevSeverity = useRef(severity);
  const [pulsing, setPulsing] = useState(false);
  useEffect(() => {
    const was = prevSeverity.current;
    prevSeverity.current = severity;
    const entering = (severity === "warn" || severity === "alarm") && was !== severity;
    if (!entering) return undefined;
    setPulsing(true);
    const id = setTimeout(() => setPulsing(false), 600);
    return () => clearTimeout(id);
  }, [severity]);

  const header = title !== undefined && title !== null && title !== "" ? true : Boolean(status || actions || severity);
  const cls = ["fb-tile", `fb-tile-body-${body}`, severity ? `fb-alarm-${severity}` : "", pulsing ? "fb-pulse" : "", className]
    .filter(Boolean)
    .join(" ");
  return (
    <section className={cls}>
      {header && (
        <header className={["fb-tile-head", handleClassName].filter(Boolean).join(" ")}>
          {severity && <span className={`fb-panel-dot fb-panel-dot-${severity}`} title={severityLabel ?? SEVERITY_LABEL[severity]} aria-hidden="true" />}
          {title !== undefined && title !== null && title !== "" && <h3 className="fb-tile-title">{title}</h3>}
          {subtitle && <span className="fb-tile-subtitle fb-muted">{subtitle}</span>}
          {status && <span className="fb-tile-status">{status}</span>}
          {actions && <span className="fb-tile-menu">{actions}</span>}
        </header>
      )}
      <div className="fb-tile-body">{children}</div>
      {footer && <div className="fb-tile-footer fb-muted">{footer}</div>}
    </section>
  );
}
