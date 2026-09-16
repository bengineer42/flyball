import { memo } from "react";
import { Typography } from "@mui/material";
import { Markdown } from "./markdown.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

const TextWidget = memo(function TextWidget({ config }: WidgetComponentProps) {
  const text = String(config.text ?? "");
  // Spacing and type come from dashboard.css `.dash-text`; the body clips rather than scrolls -- size the widget to the note.
  return <div className="dash-text">{config.markdown === false ? <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>{text}</Typography> : <Markdown text={text} />}</div>;
});

export const text: WidgetKind = {
  kind: "text",
  label: "Text",
  description: "A note: markdown (headings, lists, bold, links) or plain text.",
  category: "layout",
  // 6×4: a 78px body holds the default note (a heading and two lines) (DESIGN-SPEC.md §10).
  defaultSize: { w: 6, h: 4 },
  minSize: { w: 3, h: 2 },
  cost: "cheap",
  configSchema: () => ({
    type: "object",
    properties: {
      text: { type: "string", title: "Text", default: "" },
      markdown: { type: "boolean", title: "Markdown", default: true },
    },
  }),
  uiSchema: { text: { "ui:widget": "textarea", "ui:options": { rows: 8 } } },
  defaultConfig: () => ({ text: "## Notes\n\nWhat this dashboard is for, who to call, what to watch.", markdown: true }),
  titleFor: () => undefined,
  Component: TextWidget,
};
