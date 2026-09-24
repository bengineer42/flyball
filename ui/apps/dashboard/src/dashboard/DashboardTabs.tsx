/**
 * The dashboards as tabs in the app bar (D-053): the generated overview first, then this rig's
 * saved dashboards in tab order (`byTabOrder`: the document's `order`, then newest first), then
 * `[+]` for a new, empty one. A tab is a link to its route, so a dashboard keeps its address.
 * An operator drags a saved dashboard's tab to move it; the move is written to the documents
 * (`order`), so every browser shows the same order. Options › Dashboards does the same by buttons.
 * Rendered by `Shell` on the dashboards page; it fetches the list itself so it does not need the
 * page's own (larger) state.
 */
import { useState } from "react";
import { Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, Tab, Tabs, TextField, Tooltip } from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import HomeIcon from "@mui/icons-material/Home";
import { invalidateDashboards, useDashboards, useRig } from "@flyball/react";
import { useAuth } from "../auth.js";
import { hashFor } from "../router.js";
import { emptyDocument, labelFor, labelOf, nameFor } from "./document.js";
import { reorder, saveOrder } from "./order.js";
import { GENERATED_NAME } from "./generate.js";
import { readHome } from "./home.js";

export interface DashboardTabsProps {
  /** The dashboard named in the route; null for the bare route or `?generated`. */
  name: string | null;
  /** `#/dashboards?generated`: the generated overview even when a home dashboard is set. */
  generated: boolean;
  onOpen(name: string | null, generated?: boolean): void;
}

/** The value MUI's `Tabs` holds for the generated overview (a saved dashboard cannot be named this: a name is `nameFor` a label, lower-case letters, digits and `-`). */
const GENERATED = "\u0000generated";

export function DashboardTabs({ name, generated, onOpen }: DashboardTabsProps) {
  const rig = useRig();
  const { canOperate } = useAuth();
  const list = useDashboards();
  const home = readHome();
  const wanted = name ?? (!generated && home ? home : null);
  const saved = list.data ?? [];
  // Until the list has loaded, the route's name is not yet a tab: MUI warns about a value with no tab.
  const value = wanted === null ? GENERATED : saved.some((d) => d.name === wanted) ? wanted : false;
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);
  // What is typed is the label; the key it is saved under follows from it.
  const typed = newName.trim();
  const key = nameFor(typed);
  const taken = saved.some((d) => d.name === key);
  const [dragging, setDragging] = useState<number | null>(null);
  const drop = async (to: number) => {
    const from = dragging;
    setDragging(null);
    if (from === null || from === to) return;
    try {
      await saveOrder(rig, saved, reorder(saved, from, to));
    } finally {
      invalidateDashboards();
    }
  };

  const create = async () => {
    const as = key;
    if (!as || taken) return;
    try {
      await rig.saveDashboard(as, emptyDocument(as, "", labelFor(typed, as)));
      invalidateDashboards();
      setAdding(false);
      setNewName("");
      setError(null);
      onOpen(as);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Box sx={{ display: "flex", alignItems: "center", minWidth: 0, flex: "1 1 auto" }}>
      <Tabs
        value={value}
        variant="scrollable"
        scrollButtons="auto"
        allowScrollButtonsMobile
        aria-label="dashboards"
        data-testid="dashboard-tabs"
        onChange={(_, v: string) => onOpen(v === GENERATED ? null : v, v === GENERATED)}
        sx={{ minHeight: 40, minWidth: 0, "& .MuiTab-root": { minHeight: 40, py: 0, textTransform: "none" } }}
      >
        <Tab value={GENERATED} label={GENERATED_NAME} component="a" href={hashFor("dashboards", null, { generated: "" })} onClick={(e: React.MouseEvent) => e.preventDefault()} data-testid="dashboard-tab-generated" />
        {saved.map((d, i) => (
          <Tab
            key={d.name}
            draggable={canOperate}
            onDragStart={(e: React.DragEvent) => {
              setDragging(i);
              e.dataTransfer.effectAllowed = "move";
            }}
            onDragOver={(e: React.DragEvent) => dragging !== null && e.preventDefault()}
            onDrop={(e: React.DragEvent) => {
              e.preventDefault();
              void drop(i);
            }}
            onDragEnd={() => setDragging(null)}
            value={d.name}
            label={labelOf(d.body, d.name)}
            icon={home === d.name ? <HomeIcon sx={{ fontSize: 16 }} /> : undefined}
            iconPosition="end"
            component="a"
            href={hashFor("dashboards", d.name)}
            onClick={(e: React.MouseEvent) => e.preventDefault()}
            data-testid={`dashboard-tab-${d.name}`}
          />
        ))}
      </Tabs>
      <Tooltip title={canOperate ? "New dashboard" : "Sign in to operate to add a dashboard"}>
        <span>
          <IconButton size="small" aria-label="new dashboard" onClick={() => setAdding(true)} disabled={!canOperate} data-testid="dashboard-add">
            <AddIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
      <Dialog open={adding} onClose={() => setAdding(false)} maxWidth="xs" fullWidth>
        <DialogTitle>New dashboard</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            margin="dense"
            label="Name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void create()}
            error={taken || error !== null}
            helperText={taken ? `A dashboard is already saved as “${key}”.` : error ?? (key ? `Saved as “${key}”, empty; add widgets with Edit.` : "Empty; add widgets with Edit.")}
            inputProps={{ "data-testid": "dashboard-new-name" }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAdding(false)}>Cancel</Button>
          <Button variant="contained" onClick={() => void create()} disabled={!key || taken} data-testid="dashboard-new-create">
            Create
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
