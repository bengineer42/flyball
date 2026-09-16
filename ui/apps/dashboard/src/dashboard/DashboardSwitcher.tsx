/**
 * The dashboard switcher: "Overview ▾" in the app bar, listing this rig's
 * saved dashboards plus the generated one (DESIGN-SPEC.md §2 -- "dashboard
 * identity at the top, not in the sidebar", after Grafana and Home
 * Assistant). Rendered via `Shell`'s `startSlot`, only on the dashboards
 * page; it fetches the list itself so it does not need the page's own
 * (larger) state.
 */
import { Box, Divider, FormControl, ListItemText, MenuItem, Select, useMediaQuery, useTheme } from "@mui/material";
import HomeIcon from "@mui/icons-material/Home";
import { useDashboards } from "@flyball/react";
import { PAGE_ICONS } from "../icons.js";
import { GENERATED_NAME } from "./generate.js";
import { readHome } from "./home.js";

export interface DashboardSwitcherProps {
  /** The dashboard named in the route; null for the bare route or `?generated`. */
  name: string | null;
  /** `#/dashboards?generated`: the generated overview even when a home dashboard is set. */
  generated: boolean;
  onOpen(name: string | null, generated?: boolean): void;
}

export function DashboardSwitcher({ name, generated, onOpen }: DashboardSwitcherProps) {
  const theme = useTheme();
  // Below 600px (Shell's own phone breakpoint) the switcher must not push the app bar's
  // chips (conditions/session/live/theme) out of the toolbar -- an icon and a short,
  // truncated name instead of the full one, capped tight.
  const narrow = useMediaQuery(theme.breakpoints.down("sm"));
  const list = useDashboards();
  const home = readHome();
  const wanted = name ?? (!generated && home ? home : null);
  const isGenerated = wanted === null;
  const label = isGenerated ? GENERATED_NAME : wanted;
  const Icon = PAGE_ICONS.dashboards;
  return (
    <FormControl size="small" sx={{ minWidth: narrow ? 0 : 200, width: narrow ? 40 : undefined, flexShrink: 0 }}>
      <Select
        value={isGenerated ? "" : wanted}
        displayEmpty
        onChange={(e) => {
          const v = e.target.value;
          onOpen(v === "" ? null : v, v === "");
        }}
        inputProps={{ "aria-label": "dashboard" }}
        data-testid="dashboard-select"
        // Icon only below 600px: even a short truncated name left too little room for the app
        // bar's own chips (conditions/session/live/theme) at a phone width; the full name is
        // still there in the tooltip and, opened, every dashboard's full name in the menu.
        renderValue={() =>
          narrow ? (
            <Box sx={{ display: "flex", alignItems: "center" }} title={label ?? undefined}>
              <Icon fontSize="small" />
            </Box>
          ) : (
            label
          )
        }
        sx={narrow ? { "& .MuiSelect-select": { display: "flex", alignItems: "center", py: 0.75, pr: "24px !important", pl: 1 } } : undefined}
      >
        <MenuItem value="">
          <ListItemText primary={GENERATED_NAME} secondary="from the rig's schema" />
        </MenuItem>
        {(list.data ?? []).length > 0 && <Divider />}
        {(list.data ?? []).map((d) => (
          <MenuItem key={d.name} value={d.name}>
            <ListItemText primary={d.name} secondary={`saved ${new Date(d.created_ns / 1e6).toLocaleString()}`} />
            {home === d.name && <HomeIcon fontSize="small" sx={{ ml: 1.5, color: "text.disabled" }} />}
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  );
}
