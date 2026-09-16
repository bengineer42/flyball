import type { ReactNode } from "react";

export interface TileProps {
  /** The heading; without one (and without a menu) the tile has no header row and the body takes the whole height. */
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
  className?: string;
  children: ReactNode;
}

/**
 * The chrome every dashboard widget sits in: a header row with title,
 * subtitle, status and a menu slot, and a body that fills the rest of the
 * tile. Nothing here knows what a widget is; `--fb-*` variables style it.
 */
export function Tile({ title, subtitle, status, menu, handleClassName, body = "fill", className, children }: TileProps) {
  const header = title !== undefined && title !== null && title !== "" ? true : Boolean(status || menu);
  const cls = ["fb-tile", `fb-tile-body-${body}`, className].filter(Boolean).join(" ");
  return (
    <section className={cls}>
      {header && (
        <header className={["fb-tile-head", handleClassName].filter(Boolean).join(" ")}>
          {title !== undefined && title !== null && title !== "" && <h3 className="fb-tile-title">{title}</h3>}
          {subtitle && <span className="fb-tile-subtitle fb-muted">{subtitle}</span>}
          {status && <span className="fb-tile-status">{status}</span>}
          {menu && <span className="fb-tile-menu">{menu}</span>}
        </header>
      )}
      <div className="fb-tile-body">{children}</div>
    </section>
  );
}
