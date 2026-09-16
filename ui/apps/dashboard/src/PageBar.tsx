import type { ReactNode } from "react";
import { Box } from "@mui/material";

/**
 * The bar under the app bar that a page's own controls live in: actions on
 * the left, chart settings (window, sample, y axis) on the right, once per
 * page rather than once per section. Sticks under the app bar as the page
 * scrolls; wraps onto two rows on a phone.
 */
export function PageBar({ children, end }: { children?: ReactNode; end?: ReactNode }) {
  return (
    <Box
      className="page-bar"
      sx={{
        position: "sticky",
        top: 48,
        zIndex: 2,
        bgcolor: "background.default",
        borderBottom: 1,
        borderColor: "divider",
        mt: "-16px",
        mb: "16px",
        mx: { xs: "-16px", md: "-24px" },
        px: { xs: "16px", md: "24px" },
        py: "8px",
        minHeight: 44,
        boxSizing: "border-box",
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: "8px 16px",
      }}
    >
      {children}
      {end && <Box sx={{ ml: "auto", display: "flex", alignItems: "center", flexWrap: "wrap", gap: "8px 16px" }}>{end}</Box>}
    </Box>
  );
}
