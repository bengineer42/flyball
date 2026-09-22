import { useState, type ReactNode } from "react";
import {
  AppBar,
  Badge,
  Box,
  Collapse,
  Drawer,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Toolbar,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import MenuIcon from "@mui/icons-material/Menu";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import DarkModeOutlinedIcon from "@mui/icons-material/DarkModeOutlined";
import LightModeOutlinedIcon from "@mui/icons-material/LightModeOutlined";
import { PAGES, hashFor, type Page } from "./router.js";
import { PAGE_ICONS } from "./icons.js";
import { useColorMode } from "./theme.js";

export interface ShellProps {
  page: Page;
  onNavigate(page: Page): void;
  title: string;
  status: ReactNode;
  /** Show the Simulation page in the navigation (`/api/sim` says the rig is simulated). */
  simulated?: boolean;
  /**
   * Left-of-centre in the app bar, after the page title: the dashboard
   * switcher ("Overview ▾" + this rig's dashboards + "Manage…", spec §2).
   * The dashboards-engine agent fills this in from `Dashboards.tsx`; empty here on purpose.
   */
  startSlot?: ReactNode;
  /** The rig's devices, for the Devices entry to open into: each a direct link to its page. */
  devices?: ReadonlyArray<{ name: string; label?: string | null }>;
  /** The thing the current page shows (a device's name on its page), so its entry reads as current. */
  current?: string | null;
  /** Unread WARNING+ event count (`useUnreadEvents`), shown as a badge on the Events entry. */
  eventsUnread?: number;
  children: ReactNode;
}

const DRAWER_W = 196;
const MINI_W = 56;

function Nav({ page, mini, simulated, devices = [], current = null, eventsUnread = 0, onNavigate }: { page: Page; mini: boolean; simulated: boolean; devices?: ReadonlyArray<{ name: string; label?: string | null }>; current?: string | null; eventsUnread?: number; onNavigate(p: Page): void }) {
  // The Devices entry opens into one link per device; open while a device page is showing, or when asked.
  const [devicesOpen, setDevicesOpen] = useState<boolean | null>(null);
  const showDevices = !mini && devices.length > 0 && (devicesOpen ?? page === "devices");
  return (
    <List dense disablePadding sx={{ pt: 1 }}>
      {PAGES.filter((p) => p.id !== "simulation" || simulated).map((p) => {
        const Icon = PAGE_ICONS[p.id];
        const expandable = p.id === "devices" && !mini && devices.length > 0;
        const item = (
          <ListItemButton
            key={p.id}
            component="a"
            href={hashFor(p.id)}
            selected={p.id === page}
            aria-current={p.id === page ? "page" : undefined}
            onClick={(e: React.MouseEvent) => {
              e.preventDefault();
              onNavigate(p.id);
            }}
            sx={{
              px: mini ? 0 : 2,
              justifyContent: mini ? "center" : "flex-start",
              borderLeft: 3,
              borderLeftColor: p.id === page ? "primary.main" : "transparent",
            }}
          >
            <ListItemIcon sx={{ minWidth: mini ? 0 : 36, color: p.id === page ? "primary.main" : "inherit" }}>
              {p.id === "events" && eventsUnread > 0 ? (
                <Badge badgeContent={eventsUnread} max={99} color="warning" data-testid="events-nav-badge">
                  <Icon fontSize="small" />
                </Badge>
              ) : (
                <Icon fontSize="small" />
              )}
            </ListItemIcon>
            {!mini && <ListItemText primary={p.label} />}
            {expandable && (
              <IconButton
                size="small"
                edge="end"
                aria-label={showDevices ? "hide the devices" : "show the devices"}
                aria-expanded={showDevices}
                onClick={(e: React.MouseEvent) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setDevicesOpen(!showDevices);
                }}
                sx={{ mr: -1 }}
              >
                {showDevices ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
              </IconButton>
            )}
          </ListItemButton>
        );
        const entry = mini ? (
          <Tooltip key={p.id} title={p.label} placement="right">
            {item}
          </Tooltip>
        ) : (
          item
        );
        if (!expandable) return entry;
        return (
          <li key={p.id} style={{ listStyle: "none" }}>
            {entry}
            <Collapse in={showDevices} unmountOnExit>
              <List dense disablePadding aria-label="devices">
                {devices.map((d) => {
                  const here = page === "devices" && current === d.name;
                  return (
                    <ListItemButton
                      key={d.name}
                      component="a"
                      href={hashFor("devices", d.name)}
                      selected={here}
                      aria-current={here ? "page" : undefined}
                      onClick={(e: React.MouseEvent) => {
                        e.preventDefault();
                        window.location.hash = hashFor("devices", d.name);
                      }}
                      sx={{ pl: 4.25, pr: 1.5, py: 0.25, borderLeft: 3, borderLeftColor: here ? "primary.main" : "transparent" }}
                      title={d.name}
                    >
                      <ListItemText primary={d.label ?? d.name} primaryTypographyProps={{ noWrap: true, fontSize: "0.8rem" }} />
                    </ListItemButton>
                  );
                })}
              </List>
            </Collapse>
          </li>
        );
      })}
    </List>
  );
}

/**
 * App bar with the page title, stream status and the theme toggle; a permanent
 * drawer that shrinks to icons on narrow screens and becomes a temporary
 * drawer on phones.
 */
export function Shell({ page, onNavigate, title, status, simulated = false, startSlot, devices, current, eventsUnread = 0, children }: ShellProps) {
  const theme = useTheme();
  const phone = useMediaQuery(theme.breakpoints.down("sm"));
  const mini = useMediaQuery(theme.breakpoints.between("sm", "md"));
  const [open, setOpen] = useState(false);
  const { mode, toggle } = useColorMode();
  const width = phone ? 0 : mini ? MINI_W : DRAWER_W;

  const brand = (
    <Toolbar variant="dense" sx={{ px: mini ? 0 : 2, justifyContent: mini ? "center" : "flex-start" }}>
      <Typography variant="h1" component="div" sx={{ fontWeight: 700, letterSpacing: "0.02em" }}>
        {mini ? "fb" : "flyball"}
      </Typography>
    </Toolbar>
  );

  return (
    <Box sx={{ display: "flex", minHeight: "100vh" }}>
      <AppBar
        position="fixed"
        color="inherit"
        elevation={0}
        sx={{ borderBottom: 1, borderColor: "divider", width: `calc(100% - ${width}px)`, ml: `${width}px` }}
      >
        <Toolbar variant="dense" sx={{ gap: 1.5 }}>
          {phone && (
            <IconButton edge="start" aria-label="menu" onClick={() => setOpen(true)}>
              <MenuIcon />
            </IconButton>
          )}
          <Typography variant="h1" component="h1" noWrap title={typeof title === "string" ? title : undefined} sx={{ flexShrink: 0, minWidth: 0, maxWidth: { xs: "40%", sm: "none" } }}>
            {title}
          </Typography>
          {startSlot}
          <Box sx={{ flexGrow: 1 }} />
          {/* The chips scroll sideways rather than push the toggle off a phone screen. */}
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0, overflowX: "auto", scrollbarWidth: "none", "&::-webkit-scrollbar": { display: "none" }, "& > *": { flexShrink: 0 } }}>
            {status}
          </Box>
          <Tooltip title={mode === "light" ? "Dark mode" : "Light mode"}>
            <IconButton edge="end" aria-label="toggle theme" onClick={toggle}>
              {mode === "light" ? <DarkModeOutlinedIcon fontSize="small" /> : <LightModeOutlinedIcon fontSize="small" />}
            </IconButton>
          </Tooltip>
        </Toolbar>
      </AppBar>

      {phone ? (
        <Drawer open={open} onClose={() => setOpen(false)} PaperProps={{ sx: { width: DRAWER_W } }}>
          {brand}
          <Nav page={page} mini={false} simulated={simulated} devices={devices} current={current} eventsUnread={eventsUnread} onNavigate={(p) => { setOpen(false); onNavigate(p); }} />
        </Drawer>
      ) : (
        <Drawer
          variant="permanent"
          PaperProps={{ sx: { width, overflowX: "hidden", borderRight: 1, borderColor: "divider" } }}
          sx={{ width, flexShrink: 0 }}
        >
          {brand}
          <Nav page={page} mini={mini} simulated={simulated} devices={devices} current={current} eventsUnread={eventsUnread} onNavigate={onNavigate} />
        </Drawer>
      )}

      {/* The page uses the width it has: one gutter on a phone, two on a desktop, capped only where a card row would get absurd. */}
      <Box component="main" sx={{ flexGrow: 1, minWidth: 0, px: { xs: "16px", md: "24px" }, pb: "24px", maxWidth: 2200 }}>
        <Toolbar variant="dense" sx={{ mb: "16px" }} />
        {children}
      </Box>
    </Box>
  );
}
