import { useMemo, useState } from "react";
import { Box, Chip, Drawer, IconButton, List, ListItemButton, ListItemText, ListSubheader, Stack, TextField, Tooltip, Typography } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { CATEGORIES, WIDGET_TYPES, type WidgetCost, type WidgetType } from "../widgets/registry.js";

const COST: Record<WidgetCost, { label: string; colour: "default" | "info" | "warning"; hint: string }> = {
  cheap: { label: "cheap", colour: "default", hint: "Text and numbers: costs nothing to keep live." },
  chart: { label: "chart", colour: "info", hint: "One canvas, redrawn as samples arrive (paused while off screen)." },
  heavy: { label: "heavy", colour: "warning", hint: "Several charts or a form-heavy panel: a few of these is plenty." },
};

/** The catalogue: every type by category, searchable, with what each costs; click one to add it. Stays open so several can be added. */
export function AddWidgetDrawer({ open, onClose, onAdd }: { open: boolean; onClose(): void; onAdd(type: WidgetType): void }) {
  const [search, setSearch] = useState("");
  const needle = search.trim().toLowerCase();
  const groups = useMemo(
    () =>
      CATEGORIES.map((c) => ({
        ...c,
        types: WIDGET_TYPES.filter((t) => t.category === c.id && (!needle || `${t.label} ${t.description} ${t.type}`.toLowerCase().includes(needle))),
      })).filter((g) => g.types.length),
    [needle],
  );
  return (
    <Drawer anchor="right" open={open} onClose={onClose} PaperProps={{ sx: { width: 340, maxWidth: "100vw" } }} data-testid="add-widget-drawer">
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 3, py: 1.5, borderBottom: 1, borderColor: "divider" }}>
        <Typography variant="h1" component="h2" sx={{ flexGrow: 1 }}>
          Add a widget
        </Typography>
        <IconButton aria-label="close" onClick={onClose}>
          <CloseIcon fontSize="small" />
        </IconButton>
      </Stack>
      <Box sx={{ px: 3, py: 1.5 }}>
        <TextField fullWidth placeholder="search" value={search} onChange={(e) => setSearch(e.target.value)} inputProps={{ "aria-label": "search widgets" }} autoFocus />
      </Box>
      <List dense disablePadding sx={{ overflow: "auto" }}>
        {groups.map((g) => (
          <li key={g.id}>
            <ul style={{ padding: 0 }}>
              <ListSubheader disableSticky sx={{ lineHeight: "28px" }}>
                {g.label}
              </ListSubheader>
              {g.types.map((k) => (
                <ListItemButton key={k.type} onClick={() => onAdd(k)} data-testid={`add-${k.type}`} sx={{ alignItems: "flex-start" }}>
                  <ListItemText
                    primary={
                      <Stack direction="row" alignItems="center" spacing={1.5}>
                        <span style={{ fontWeight: 600 }}>{k.label}</span>
                        <Tooltip title={COST[k.cost].hint}>
                          <Chip label={COST[k.cost].label} color={COST[k.cost].colour} variant="outlined" size="small" sx={{ height: 18, fontSize: "0.7rem" }} />
                        </Tooltip>
                        <Typography variant="caption" color="text.disabled" sx={{ ml: "auto !important" }}>
                          {k.defaultSize.w}×{k.defaultSize.h}
                        </Typography>
                      </Stack>
                    }
                    secondary={k.description}
                    secondaryTypographyProps={{ sx: { whiteSpace: "normal" } }}
                  />
                </ListItemButton>
              ))}
            </ul>
          </li>
        ))}
        {groups.length === 0 && (
          <Typography color="text.secondary" sx={{ px: 3, py: 1.5 }}>
            nothing matches “{search}”
          </Typography>
        )}
      </List>
    </Drawer>
  );
}
