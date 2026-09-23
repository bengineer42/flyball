import type { ReactNode } from "react";
import { AppBar, Box, IconButton, Toolbar, Tooltip, Typography, useMediaQuery, useTheme } from "@mui/material";
import SettingsOutlinedIcon from "@mui/icons-material/SettingsOutlined";
import { hashFor, type Page } from "./router.js";

export interface ShellProps {
  page: Page;
  title: string;
  /** The status chips: conditions, recording, program, server, sim. On a narrow screen they scroll sideways. */
  status: ReactNode;
  /** The software stop. Its slot keeps its width whether or not the viewer may operate, so signing in never shifts the bar. */
  stop?: ReactNode;
  /** Who is signed in, and the way to sign in or out. */
  account?: ReactNode;
  /**
   * The dashboards' tabs, on the dashboards page: in the bar after the title on a wide screen, on
   * a second row of their own on a phone.
   */
  startSlot?: ReactNode;
  children: ReactNode;
}

/** Wide enough for the "Software stop" button (122 px measured, small outlined with its icon, at 1440, 800 and 400 px): the empty slot is the same size. */
export const STOP_SLOT_W = 132;

/**
 * The app frame: one app bar and the page (D-053, no sidebar). The bar holds, left to right, the
 * title (the dashboards' tabs on the dashboards page), the status chips, the stop slot, the
 * account chip and the gear that opens Options. Every page is reached from here: a dashboard by
 * its tab, Events / Sessions / Programs / Simulation by their chips, the rest from Options.
 */
export function Shell({ page, title, status, stop, account, startSlot, children }: ShellProps) {
  const theme = useTheme();
  const phone = useMediaQuery(theme.breakpoints.down("sm"));
  const tabs = startSlot ?? null;
  const onDashboards = page === "dashboards" && tabs !== null;
  return (
    <Box sx={{ minHeight: "100vh" }}>
      <AppBar position="fixed" color="inherit" elevation={0} sx={{ borderBottom: 1, borderColor: "divider" }}>
        <Toolbar variant="dense" sx={{ gap: 1.5 }}>
          <Typography
            variant="h1"
            component="h1"
            noWrap
            title={title}
            // On the dashboards page the tabs say which dashboard this is; the heading stays for a screen reader.
            sx={onDashboards ? visuallyHidden : { flexShrink: 0, minWidth: 0, maxWidth: { xs: "40%", sm: "none" } }}
          >
            {title}
          </Typography>
          {onDashboards && !phone ? tabs : <Box sx={{ flexGrow: 1 }} />}
          {/* The chips scroll sideways rather than push the stop slot or the gear off a phone screen. */}
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0, flexShrink: 1, py: "4px", overflowX: "auto", scrollbarWidth: "none", "&::-webkit-scrollbar": { display: "none" }, "& > *": { flexShrink: 0 } }}>
            {status}
          </Box>
          <Box data-testid="stop-slot" sx={{ width: STOP_SLOT_W, flexShrink: 0, display: "flex", justifyContent: "flex-end" }}>
            {stop}
          </Box>
          {account}
          <Tooltip title="Options: the rig file, appearance, every page">
            <IconButton edge="end" aria-label="options" href={hashFor("options")} color={page === "options" ? "primary" : "default"} data-testid="options-gear">
              <SettingsOutlinedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Toolbar>
        {onDashboards && phone && (
          <Toolbar variant="dense" disableGutters sx={{ px: 1, borderTop: 1, borderColor: "divider" }}>
            {tabs}
          </Toolbar>
        )}
      </AppBar>

      {/* The page uses the width it has: one gutter on a phone, two on a desktop, capped only where a card row would get absurd. */}
      <Box component="main" sx={{ minWidth: 0, px: { xs: "16px", md: "24px" }, pb: "24px", maxWidth: 2200, mx: "auto" }}>
        <Toolbar variant="dense" sx={{ mb: "16px" }} />
        {onDashboards && phone && <Toolbar variant="dense" />}
        {children}
      </Box>
    </Box>
  );
}

/** Off screen, still read: MUI's `visuallyHidden`, inlined to avoid a dependency on `@mui/utils`. */
const visuallyHidden = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: "-1px",
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: "1px",
} as const;
