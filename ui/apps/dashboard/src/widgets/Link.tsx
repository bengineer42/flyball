import { memo } from "react";
import { Button } from "@mui/material";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import { PAGES, hashFor, type Page } from "../router.js";
import { PAGE_ICONS } from "../icons.js";
import { isSafeHref } from "./markdown.js";
import type { WidgetType, WidgetComponentProps } from "./types.js";

const LinkWidget = memo(function LinkWidget({ config }: WidgetComponentProps) {
  const page = String(config.page ?? "dashboards");
  const name = String(config.name ?? "").trim();
  const external = page === "url";
  // `hashFor` builds this app's own hash links (always safe); an "a URL" link is an operator-supplied
  // string from a stored dashboard config, so it goes through the same scheme allow-list as the
  // Markdown widget's links before it ever reaches an `href`.
  const rawHref = external ? String(config.url ?? "") : hashFor(page as Page, name || null);
  const href = external && rawHref && !isSafeHref(rawHref) ? "" : rawHref;
  const Icon = external ? OpenInNewIcon : PAGE_ICONS[page as Page] ?? OpenInNewIcon;
  const label = String(config.label ?? "").trim() || (external ? rawHref : `${PAGES.find((p) => p.id === page)?.label ?? page}${name ? ` › ${name}` : ""}`);
  return (
    <Button
      component="a"
      href={href || undefined}
      target={external ? "_blank" : undefined}
      rel={external ? "noopener noreferrer" : undefined}
      variant={config.variant === "text" ? "text" : "outlined"}
      size="small"
      startIcon={<Icon />}
      className="dash-link"
    >
      {label}
    </Button>
  );
});

export const link: WidgetType = {
  type: "link",
  label: "Link",
  description: "A button to a page of the app -- a device, a signal, a controller, a program -- or to a URL.",
  category: "layout",
  defaultSize: { w: 4, h: 1 },
  minSize: { w: 2, h: 1 },
  cost: "cheap",
  configSchema: () => ({
    type: "object",
    properties: {
      page: { type: "string", title: "Page", default: "dashboards", oneOf: [...PAGES.map((p) => ({ const: p.id, title: p.label })), { const: "url", title: "a URL" }] },
      name: { type: "string", title: "Name", default: "", description: "The thing on that page (a device or program name, a signal's or controller's address, a session id); blank for the list." },
      url: { type: "string", title: "URL", default: "", description: "When the page is \"a URL\".", pattern: "^$|^(https?:|mailto:|#|/)" },
      label: { type: "string", title: "Label", default: "", description: "Blank: the page and name." },
      variant: { type: "string", title: "Look", default: "outlined", enum: ["outlined", "text"] },
    },
    required: ["page"],
  }),
  uiSchema: { page: { "ui:widget": "select" } },
  defaultConfig: () => ({ page: "readings", name: "", url: "", label: "", variant: "outlined" }),
  labelFor: () => undefined,
  header: false,
  Component: LinkWidget,
};
