import { memo } from "react";
import { Typography } from "@mui/material";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

const HeadingWidget = memo(function HeadingWidget({ config }: WidgetComponentProps) {
  const size = String(config.size ?? "section");
  return (
    <Typography
      component={size === "page" ? "h2" : "h3"}
      variant={size === "page" ? "h1" : "h2"}
      color={size === "page" ? "text.primary" : "text.secondary"}
      sx={{ alignSelf: "flex-end", m: 0, fontSize: size === "page" ? "1.3rem" : undefined, borderBottom: 1, borderColor: "divider", pb: 0.5, width: "100%" }}
    >
      {String(config.text ?? "")}
    </Typography>
  );
});

export const heading: WidgetKind = {
  kind: "heading",
  label: "Heading",
  description: "A section title across the grid, to group the tiles under it.",
  category: "layout",
  defaultSize: { w: 12, h: 1 },
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
  titleFor: () => undefined,
  header: false,
  Component: HeadingWidget,
};

const SpacerWidget = memo(function SpacerWidget() {
  return null;
});

export const spacer: WidgetKind = {
  kind: "spacer",
  label: "Spacer",
  description: "Empty room, to push tiles apart or start a new row.",
  category: "layout",
  defaultSize: { w: 3, h: 1 },
  minSize: { w: 1, h: 1 },
  cost: "cheap",
  configSchema: () => ({ type: "object", properties: {} }),
  titleFor: () => undefined,
  header: false,
  Component: SpacerWidget,
};
