import { memo } from "react";
import { Typography } from "@mui/material";
import type { WidgetType, WidgetComponentProps } from "./types.js";

const HeadingWidget = memo(function HeadingWidget({ config }: WidgetComponentProps) {
  const size = String(config.size ?? "section");
  // A section head as on every list page (DESIGN-SPEC.md §2): 11px uppercase label, or 16/600 for `page`; sits on the cell's bottom edge (dashboard.css `.dash-heading`).
  return <Typography component={size === "page" ? "h2" : "h3"} className={`dash-heading dash-heading-${size}`}>{String(config.text ?? "")}</Typography>;
});

export const heading: WidgetType = {
  type: "heading",
  label: "Heading",
  description: "A section title across the grid, to group the tiles under it.",
  category: "layout",
  defaultSize: { w: 24, h: 1 },
  minSize: { w: 2, h: 1 },
  cost: "cheap",
  configSchema: () => ({
    type: "object",
    properties: {
      text: { type: "string", title: "Text", default: "Section" },
      size: { type: "string", title: "Size", default: "section", oneOf: [{ const: "section", title: "section" }, { const: "page", title: "page" }] },
    },
    required: ["text"],
  }),
  defaultConfig: () => ({ text: "Section", size: "section" }),
  labelFor: () => undefined,
  header: false,
  Component: HeadingWidget,
};

const SpacerWidget = memo(function SpacerWidget() {
  return null;
});

export const spacer: WidgetType = {
  type: "spacer",
  label: "Spacer",
  description: "Empty room, to push tiles apart or start a new row.",
  category: "layout",
  defaultSize: { w: 6, h: 1 },
  minSize: { w: 1, h: 1 },
  cost: "cheap",
  configSchema: () => ({ type: "object", properties: {} }),
  labelFor: () => undefined,
  header: false,
  Component: SpacerWidget,
};
