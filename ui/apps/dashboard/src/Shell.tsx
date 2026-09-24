import { useEffect, useRef, type ReactNode } from "react";
import { AppBar, Box, IconButton, Toolbar, Tooltip, Typography, useMediaQuery, useTheme } from "@mui/material";
import HomeOutlinedIcon from "@mui/icons-material/HomeOutlined";
import SettingsOutlinedIcon from "@mui/icons-material/SettingsOutlined";
import { hashFor, type Page } from "./router.js";

export interface ShellProps {
  page: Page;
  title: string;
  /** The status chips: conditions, recording, program, server, sim. On a phone they get a row of their own and wrap. */
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
 * The app frame: one app bar and the page (D-053, no sidebar). The bar holds, left to right, a
 * home button (back to the dashboards -- the home one, if set -- from any page), the title (the
 * dashboards' tabs on the dashboards page), the status chips, the stop slot, the account chip and
 * the gear that opens Options. Every page is reached from here: a dashboard by its tab, Events /
 * Sessions / Programs / Simulation by their chips, the rest from Options.
 */
export function Shell({ page, title, status, stop, account, startSlot, children }: ShellProps) {
  const theme = useTheme();
  const phone = useMediaQuery(theme.breakpoints.down("sm"));
  const tabs = startSlot ?? null;
  const onDashboards = page === "dashboards" && tabs !== null;
  // The bar's height varies (a phone wraps the chips; the dashboards page adds its tabs), so it is published as
  // `--fb-bar-h` for whatever sits under it: the sticky page bar, the Graph page's full-height body.
  const bar = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = bar.current;
    if (!el) return;
    const publish = () => document.documentElement.style.setProperty("--fb-bar-h", `${el.offsetHeight}px`);
    publish();
    if (typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(publish);
    watch.observe(el);
    return () => watch.disconnect();
  }, []);
  return (
    <Box sx={{ minHeight: "100vh" }}>
      <AppBar ref={bar} position="sticky" color="inherit" elevation={0} sx={{ borderBottom: 1, borderColor: "divider" }}>
        <Toolbar variant="dense" sx={{ gap: 1.5 }}>
          <Tooltip title="Dashboards">
            <IconButton edge="start" aria-label="dashboards" href={hashFor("dashboards")} color={page === "dashboards" ? "primary" : "default"} data-testid="home-button">
              <HomeOutlinedIcon fontSize="small" />
            </IconButton>
          </Tooltip>
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
          {!phone && (
            <Box data-testid="status-chips" sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0, flexShrink: 1, py: "4px", overflowX: "auto", "& > *": { flexShrink: 0 } }}>
              {status}
            </Box>
          )}
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
        {/* On a phone the chips get a row of their own and wrap, so none is ever out of sight behind a hidden scroll. */}
        {phone && (
          <Box data-testid="status-chips" sx={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 1, px: 1, py: "6px", borderTop: 1, borderColor: "divider" }}>
            {status}
          </Box>
        )}
        {onDashboards && phone && (
          <Toolbar variant="dense" disableGutters sx={{ px: 1, borderTop: 1, borderColor: "divider" }}>
            {tabs}
          </Toolbar>
        )}
      </AppBar>

      {/* The page uses the width it has: one gutter on a phone, two on a desktop, capped only where a card row would get absurd. */}
      {/* The bar is sticky, so it takes its own height in the flow however many rows it wraps to: no spacer to keep in step. */}
      <Box component="main" sx={{ minWidth: 0, px: { xs: "16px", md: "24px" }, pt: "16px", pb: "24px", maxWidth: 2200, mx: "auto" }}>
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
