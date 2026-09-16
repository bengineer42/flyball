import { memo } from "react";
import { Box, Typography } from "@mui/material";
import { Markdown } from "./markdown.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

const TextWidget = memo(function TextWidget({ config }: WidgetComponentProps) {
  const text = String(config.text ?? "");
  return (
    <Box sx={{ overflow: "auto", minHeight: 0, flex: "1 1 auto", fontSize: "0.9rem", "& .fb-markdown > :first-of-type": { mt: 0 }, "& .fb-markdown > :last-child": { mb: 0 }, "& h2, & h3, & h4": { mt: 1.5, mb: 0.5 }, "& p": { my: 0.75 }, "& code": { fontSize: "0.85em", px: 0.5, bgcolor: "action.hover", borderRadius: 0.5 } }}>
      {config.markdown === false ? <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>{text}</Typography> : <Markdown text={text} />}
    </Box>
  );
});

export const text: WidgetKind = {
  kind: "text",
  label: "Text",
  description: "A note: markdown (headings, lists, bold, links) or plain text.",
  category: "layout",
  defaultSize: { w: 4, h: 4 },
  minSize: { w: 2, h: 1 },
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
