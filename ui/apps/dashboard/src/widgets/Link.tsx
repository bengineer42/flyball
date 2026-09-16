import { memo } from "react";
import { Button } from "@mui/material";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import { PAGES, hashFor, type Page } from "../router.js";
import { PAGE_ICONS } from "../icons.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

const LinkWidget = memo(function LinkWidget({ config }: WidgetComponentProps) {
  const page = String(config.page ?? "overview");
  const name = String(config.name ?? "").trim();
  const external = page === "url";
  const href = external ? String(config.url ?? "") : hashFor(page as Page, name || null);
  const Icon = external ? OpenInNewIcon : PAGE_ICONS[page as Page] ?? OpenInNewIcon;
  const label = String(config.label ?? "").trim() || (external ? href : `${PAGES.find((p) => p.id === page)?.label ?? page}${name ? ` › ${name}` : ""}`);
  return (
    <Button
      component="a"
      href={href || undefined}
      target={external ? "_blank" : undefined}
      rel={external ? "noopener noreferrer" : undefined}
      variant={config.variant === "text" ? "text" : "outlined"}
      startIcon={<Icon />}
      sx={{ flex: "1 1 auto", justifyContent: "flex-start", minHeight: 0, textAlign: "left" }}
    >
      {label}
    </Button>
  );
});

export const link: WidgetKind = {
  kind: "link",
  label: "Link",
  description: "A button to a page of the app -- a source, a loop, a program -- or to a URL.",
  category: "layout",
  defaultSize: { w: 2, h: 1 },
  minSize: { w: 1, h: 1 },
  cost: "cheap",
  configSchema: () => ({
    type: "object",
    properties: {
      page: { type: "string", title: "Page", default: "overview", oneOf: [...PAGES.map((p) => ({ const: p.id, title: p.label })), { const: "url", title: "a URL" }] },
      name: { type: "string", title: "Name", default: "", description: "The thing on that page (a source, loop, actuator or program name; a session id); blank for the list." },
      url: { type: "string", title: "URL", default: "", description: "When the page is \"a URL\"." },
      label: { type: "string", title: "Label", default: "", description: "Blank: the page and name." },
      variant: { type: "string", title: "Look", default: "outlined", enum: ["outlined", "text"] },
    },
    required: ["page"],
  }),
  uiSchema: { page: { "ui:widget": "select" } },
  defaultConfig: () => ({ page: "sources", name: "", url: "", label: "", variant: "outlined" }),
  titleFor: () => undefined,
  header: false,
  Component: LinkWidget,
};
