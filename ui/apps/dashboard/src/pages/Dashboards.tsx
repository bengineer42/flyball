import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import {
  Alert,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import CheckIcon from "@mui/icons-material/Check";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import DownloadIcon from "@mui/icons-material/Download";
import DriveFileRenameOutlineIcon from "@mui/icons-material/DriveFileRenameOutline";
import EditOutlinedIcon from "@mui/icons-material/EditOutlined";
import HomeIcon from "@mui/icons-material/Home";
import HomeOutlinedIcon from "@mui/icons-material/HomeOutlined";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import LockOpenOutlinedIcon from "@mui/icons-material/LockOpenOutlined";
import MoreHorizIcon from "@mui/icons-material/MoreHoriz";
import RedoIcon from "@mui/icons-material/Redo";
import RestoreIcon from "@mui/icons-material/Restore";
import SaveAsIcon from "@mui/icons-material/SaveAs";
import SaveOutlinedIcon from "@mui/icons-material/SaveOutlined";
import UndoIcon from "@mui/icons-material/Undo";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import { download, fileName, invalidateDashboards, useControllers, useDashboards, useHealth, useRig } from "@flyball/react";
import { RigError, type DashboardDocument, type DashboardProblem, type DashboardWidget, type DeviceOut, type JsonSchema, type RigEvent } from "@flyball/client";
import { Confirm } from "../Confirm.js";
import { PageBar } from "../PageBar.js";
import { ChartControls, type ChartSettings } from "../YScaleSelect.js";
import { useRecordingExports, type Programmer, type Recording } from "../model.js";
import { leaveFreely, useLeaveGuard } from "../router.js";
import { useAuth } from "../auth.js";
import { AddWidgetDrawer } from "../dashboard/AddWidgetDrawer.js";
import { ConfigureDialog } from "../dashboard/ConfigureDialog.js";
import { ControllersContext, EventsContext, RigDataContext, makeBindings, type RigData } from "../dashboard/context.js";
import { DEFAULT_GRID, bottomOf, duplicateWidget, emptyDocument, exportJson, labelFor, labelOf, nameFor, newId, normalise, sameDocument } from "../dashboard/document.js";
import { GENERATED_NAME, generateOverview } from "../dashboard/generate.js";
import { DashboardEditGrid, DashboardViewGrid, type Placement } from "../dashboard/Grid.js";
import "../dashboard/dashboard.css";
import { useHistory } from "../dashboard/history.js";
import { readHome, writeHome } from "../dashboard/home.js";
import { useStableControllers, useThrottled } from "../dashboard/throttle.js";
import { validateAgainst } from "../dashboard/validate.js";
import { WidgetFrame, type WidgetAction } from "../dashboard/WidgetFrame.js";
import { widgetType, type WidgetType } from "../widgets/registry.js";

export interface DashboardsProps extends ChartSettings {
  /** The dashboard's name from the route; null for the generated overview (or the home dashboard, when the route is bare). */
  name: string | null;
  /** `#/dashboards?generated`: the generated overview even when a home dashboard is set. */
  generated: boolean;
  devices: DeviceOut[];
  events: RigEvent[];
  recording: Recording;
  programmer: Programmer;
  onOpen(name: string | null, generated?: boolean): void;
}

/** How often the widgets see new samples; the sockets deliver far faster than a person reads or a canvas should redraw. */
const BEAT_MS = 100;

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/**
 * A dialog asking what a dashboard is called. Save as (`current` given, `keyed`): the key it is
 * saved under follows from what is typed (`nameFor`), and an existing one is replaced by a new
 * version. Rename (not `keyed`): only the label changes; the key, and so its link, stay.
 */
function NameDialog({ open, title, action, initial, taken, keyed, current, busy, error, onClose, onSubmit }: { open: boolean; title: string; action: string; initial: string; taken: string[]; keyed: boolean; current: string | null; busy: boolean; error: string | null; onClose(): void; onSubmit(label: string): void }) {
  const [name, setName] = useState(initial);
  useEffect(() => {
    if (open) setName(initial);
  }, [open, initial]);
  const trimmed = name.trim();
  const key = keyed ? nameFor(trimmed) : current ?? "";
  const exists = keyed && taken.includes(key) && key !== current;
  const ready = Boolean(trimmed && key);
  const hint = !keyed
    ? `What its tab shows; its link stays “${current ?? ""}”.`
    : exists
      ? `“${key}” exists; ${action.toLowerCase()} replaces it with a new version.`
      : key
        ? `Saved as “${key}”, its link and file name.`
        : "How this dashboard is listed and linked.";
  return (
    <Dialog open={open} onClose={() => (busy ? undefined : onClose())} fullWidth maxWidth="xs">
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <TextField
          autoFocus
          fullWidth
          label="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && ready) onSubmit(trimmed);
          }}
          helperText={hint}
          inputProps={{ "aria-label": "dashboard name" }}
          sx={{ mt: 1.5 }}
        />
        {error && (
          <Alert severity="error" sx={{ mt: 1.5 }}>
            {error}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button variant="contained" onClick={() => onSubmit(trimmed)} disabled={busy || !ready} color={exists ? "warning" : "primary"}>
          {action}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

/**
 * A dashboard: the document, edited on a grid, saved under a name on the
 * server with a version history. The widgets read the app's live data
 * through contexts fed here at a steady 10 Hz; the generated overview is a
 * function of the rig's schema and is never saved unless someone saves it.
 */
export function Dashboards({ name, generated, devices, events, recording: recordingIn, programmer: programmerIn, onOpen, ...charts }: DashboardsProps) {
  const rig = useRig();
  const list = useDashboards();
  const healthIn = useHealth(5000);
  const exports = useRecordingExports();
  const controllersLive = useControllers(3600);
  const rigName = healthIn.data?.rig ?? "";

  // `useHealth`/`programmer` are `useQuery` results and `recording` wraps one too: each is a
  // freshly-built object on every render of its owner (App.tsx, or `useQuery` itself), whether
  // or not the data inside actually changed. Controllers/events (below) update at up to the
  // poll rate, so this component's own render runs at that rate too; without this, `rigData`'s
  // memo would recompute -- and every widget's context would change -- on every one of them,
  // which is what turned "several widget kinds on the page" into React's own
  // "Maximum update depth exceeded" (the commit falling behind the incoming rate). Kept stable
  // by field, not by the wrapper's identity; `recording`'s methods dispatch through a ref so
  // they never go stale.
  const recordingRef = useRef(recordingIn);
  recordingRef.current = recordingIn;
  const health = useMemo(
    () => ({ data: healthIn.data, error: healthIn.error, loading: healthIn.loading, refresh: healthIn.refresh }),
    [healthIn.data, healthIn.error, healthIn.loading, healthIn.refresh],
  );
  const programmer = useMemo(
    () => ({ data: programmerIn.data, error: programmerIn.error, loading: programmerIn.loading, refresh: programmerIn.refresh }),
    [programmerIn.data, programmerIn.error, programmerIn.loading, programmerIn.refresh],
  );
  const recording = useMemo<Recording>(
    () => ({
      data: recordingIn.data,
      error: recordingIn.error,
      loading: recordingIn.loading,
      refresh: () => recordingRef.current.refresh(),
      start: (details?: unknown) => recordingRef.current.start(details),
      end: () => recordingRef.current.end(),
    }),
    [recordingIn.data, recordingIn.error, recordingIn.loading],
  );

  // The live data, on a beat and with identities kept where nothing moved (see `throttle.ts`).
  // Samples and write states never pass through here: widgets read them from the telemetry store.
  const stableControllers = useStableControllers(controllersLive.controllers, controllersLive.history);
  const beatControllers = useThrottled(stableControllers, BEAT_MS);
  const beatEvents = useThrottled(events, 500);

  const hist = useHistory<DashboardDocument | null>(null);

  // Bindings change when the rig's shape does, not per tick: key them on names, labels and ends.
  const controllerKey = Object.values(beatControllers.controllers)
    .map((c) => `${c.name}|${c.label}|${c.measured_signal}`)
    .join(",");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const bindings = useMemo(() => makeBindings(devices, beatControllers.controllers), [devices, controllerKey]);
  const { windowS, onWindow, yScale, onYScale } = charts;
  const rowHeight = hist.present?.grid.row_height ?? DEFAULT_GRID.row_height;
  const { canOperate } = useAuth();
  const readonly = hist.present?.readonly ?? false;
  const canWrite = canOperate && !readonly;
  const rigData = useMemo<RigData>(
    () => ({ bindings, rigName, health, recording, programmer, exports, charts: { windowS, onWindow, yScale, onYScale }, rowHeight, canWrite }),
    [bindings, rigName, health, recording, programmer, exports, windowS, onWindow, yScale, onYScale, rowHeight, canWrite],
  );

  // Which document the route asks for: a name, or the home dashboard when the route is bare and one is set, else the generated one.
  const home = readHome();
  const wanted = name ?? (!generated && home ? home : null);
  const [homeName, setHomeName] = useState(home);

  const doc = hist.present;
  const [baseline, setBaseline] = useState<DashboardDocument | null>(null);
  /** What the server said the stored document names that this rig lacks (DESIGN-SPEC.md §4.8); the widgets show the same as "missing". */
  const [problems, setProblems] = useState<DashboardProblem[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadedFor, setLoadedFor] = useState<string | null | undefined>(undefined);
  /** Imported documents waiting to be shown under their name (unsaved). */
  const imports = useRef(new Map<string, DashboardDocument>());
  const [editing, setEditing] = useState(false);
  const dirty = doc !== null && !sameDocument(doc, baseline);
  useLeaveGuard(dirty, "This dashboard has unsaved changes. Leave and discard them?");

  // Load what the route names.
  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    setProblems([]);
    if (wanted === null) {
      const fresh = generateOverview(bindings, rigName);
      hist.reset(fresh);
      setBaseline(fresh);
      setLoadedFor(null);
      return;
    }
    const imported = imports.current.get(wanted);
    imports.current.delete(wanted);
    rig
      .dashboard(wanted)
      .then((row) => {
        if (cancelled) return;
        const saved = normalise(row.body);
        hist.reset(imported ?? saved);
        setBaseline(saved);
        setProblems(row.problems);
        setLoadedFor(wanted);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (imported) {
          hist.reset(imported);
          setBaseline(null);
          setLoadedFor(wanted);
        } else {
          setLoadError(e instanceof RigError && e.status === 404 ? `No dashboard named “${wanted}”.` : message(e));
          hist.reset(null);
          setBaseline(null);
          setLoadedFor(wanted);
        }
      });
    return () => {
      cancelled = true;
    };
    // `bindings` is deliberately left out: a rig change re-generates below only while the generated document is untouched.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, rig]);

  // The generated overview follows the rig (loops arrive a moment after the schema) until it is edited.
  useEffect(() => {
    if (wanted !== null || loadedFor !== null || dirty) return;
    const fresh = generateOverview(bindings, rigName);
    if (!sameDocument(fresh, doc)) {
      hist.reset(fresh);
      setBaseline(fresh);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bindings, rigName, wanted, loadedFor, dirty]);

  // Edits.
  const patch = useCallback((f: (d: DashboardDocument) => DashboardDocument) => hist.set((d) => (d ? f(d) : d)), [hist.set]);
  const onLayout = useCallback(
    (placements: Placement[]) =>
      patch((d) => {
        const byId = new Map(placements.map((p) => [p.id, p]));
        let changed = false;
        const widgets = d.widgets.map((w) => {
          const p = byId.get(w.id);
          if (!p || (p.x === w.x && p.y === w.y && p.w === w.w && p.h === w.h)) return w;
          changed = true;
          return { ...w, x: p.x, y: p.y, w: p.w, h: p.h };
        });
        return changed ? { ...d, widgets } : d;
      }),
    [patch],
  );
  const [configuring, setConfiguring] = useState<DashboardWidget | null>(null);
  const [adding, setAdding] = useState(false);
  const onAction = useCallback(
    (id: string, action: WidgetAction) => {
      if (action === "configure") {
        setConfiguring(doc?.widgets.find((w) => w.id === id) ?? null);
        return;
      }
      patch((d) => {
        const widget = d.widgets.find((w) => w.id === id);
        if (!widget) return d;
        if (action === "remove") return { ...d, widgets: d.widgets.filter((w) => w.id !== id) };
        return { ...d, widgets: [...d.widgets, duplicateWidget(widget, d.widgets)] };
      });
    },
    [doc, patch],
  );
  const add = (type: WidgetType) =>
    patch((d) => ({
      ...d,
      widgets: [...d.widgets, { id: newId(type.type), type: type.type, label: null, x: 0, y: bottomOf(d.widgets), w: type.defaultSize.w, h: type.defaultSize.h, config: type.defaultConfig?.(bindings) ?? {} }],
    }));
  const renderWidget = useCallback((w: DashboardWidget) => <WidgetFrame widget={w} editing={editing} onAction={onAction} />, [editing, onAction]);

  // Undo/redo from the keyboard while editing, unless a field has the focus.
  useEffect(() => {
    if (!editing) return;
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || (e.target as HTMLElement | null)?.closest("input, textarea, select, [contenteditable]")) return;
      if (e.key.toLowerCase() === "z" && !e.shiftKey) {
        e.preventDefault();
        hist.undo();
      } else if ((e.key.toLowerCase() === "z" && e.shiftKey) || e.key.toLowerCase() === "y") {
        e.preventDefault();
        hist.redo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing, hist.undo, hist.redo]);

  // Saving and the rest of the menu.
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState<HTMLElement | null>(null);
  const [saveAs, setSaveAs] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const isGenerated = wanted === null;
  const names = (list.data ?? []).map((d) => d.name);
  /** What this dashboard is called where a person reads it: its label, else its key. */
  const shown = labelOf(baseline ?? doc, wanted ?? "");

  const act = async (op: () => Promise<unknown>): Promise<boolean> => {
    setBusy(true);
    try {
      await op();
      setError(null);
      setDialogError(null);
      return true;
    } catch (e) {
      setDialogError(message(e));
      setError(message(e));
      return false;
    } finally {
      setBusy(false);
    }
  };
  // A tab moved since this page loaded the document (drag, or Options › Dashboards) changed `order`
  // on the server only: keep that, not the stale copy, so saving an edit does not move the tab back.
  // A copy under a new name (Save as) starts unordered, after the ordered ones.
  const serverOrder = (as: string) => (list.data ?? []).find((d) => d.name === as)?.body.order;
  const documentFor = (as: string, label?: string | null): DashboardDocument => {
    const base = doc ?? emptyDocument(as, rigName);
    return normalise({ ...base, name: as, label: label === undefined ? base.label ?? null : label, rig: doc?.rig || rigName, order: serverOrder(as) ?? (as === wanted ? base.order ?? null : null) });
  };
  /** Save the working copy under `as`; `label` replaces its label (Save as), undefined keeps it. */
  const save = async (as: string, label?: string | null) => {
    const body = documentFor(as, label);
    const ok = await act(async () => {
      const row = await rig.saveDashboard(as, body);
      const saved = normalise(row.body);
      hist.reset(saved);
      setBaseline(saved);
      setProblems(row.problems);
      invalidateDashboards();
      setNotice(`Saved “${labelOf(saved, as)}”.`);
    });
    if (ok) {
      setSaveAs(false);
      if (as !== wanted) {
        leaveFreely();
        onOpen(as);
      }
    }
  };
  /**
   * Renaming edits the label: a new version of what is saved with the new label, under the same
   * key, so its link, its place and home are unchanged. The working copy takes the label too;
   * anything else unsaved stays unsaved.
   */
  const rename = async (to: string) => {
    if (wanted === null || !baseline) return;
    const label = labelFor(to, wanted);
    const ok = await act(async () => {
      const row = await rig.saveDashboard(wanted, normalise({ ...baseline, label, order: serverOrder(wanted) ?? baseline.order ?? null }));
      const saved = normalise(row.body);
      setBaseline(saved);
      if (dirty) patch((d) => ({ ...d, label }));
      else hist.reset(saved);
      invalidateDashboards();
    });
    if (ok) setRenaming(false);
  };
  const remove = async () => {
    if (wanted === null) return;
    const ok = await act(() => rig.deleteDashboard(wanted));
    if (ok) {
      setDeleting(false);
      invalidateDashboards();
      if (homeName === wanted) {
        writeHome(null);
        setHomeName(null);
      }
      hist.reset(null);
      setBaseline(null);
      leaveFreely();
      onOpen(null, true);
    }
  };
  const revert = () => {
    if (wanted === null) {
      const fresh = generateOverview(bindings, rigName);
      hist.reset(fresh);
      setBaseline(fresh);
    } else if (baseline) hist.reset(baseline);
  };
  const exportFile = () => {
    if (!doc) return;
    download(fileName(doc.name || "dashboard", "json"), exportJson(doc), "application/json");
  };
  const fileInput = useRef<HTMLInputElement>(null);
  const [importErrors, setImportErrors] = useState<string[] | null>(null);
  const importFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as Partial<DashboardDocument>;
      const stem = file.name.replace(/\.json$/i, "");
      const candidate = normalise({ ...emptyDocument(stem, rigName), ...parsed, name: typeof parsed.name === "string" && parsed.name ? parsed.name : stem, rig: rigName || (typeof parsed.rig === "string" ? parsed.rig : "") });
      const shape: JsonSchema = await rig.dashboardSchema();
      const problems = validateAgainst(shape, candidate);
      const unknown = candidate.widgets.filter((w) => !widgetType(w.type)).map((w) => `${w.id}: unknown widget type “${w.type}”`);
      if (problems.length) {
        setImportErrors(problems);
        return;
      }
      if (unknown.length) setNotice(`Imported with ${unknown.length} widget${unknown.length === 1 ? "" : "s"} of a type this app does not have (shown as missing).`);
      imports.current.set(candidate.name, candidate);
      setImportErrors(null);
      if (candidate.name === wanted) {
        // Same name as what is open: show it now, against the saved version.
        const imported = imports.current.get(candidate.name)!;
        imports.current.delete(candidate.name);
        hist.reset(imported);
        setEditing(true);
      } else {
        leaveFreely();
        onOpen(candidate.name);
        setEditing(true);
      }
    } catch (err) {
      setImportErrors([message(err)]);
    }
  };
  /** An edit like any other: the working copy changes at once (the widgets follow), and Save keeps it. */
  const toggleReadonly = () => {
    patch((d) => ({ ...d, readonly: !(d.readonly ?? false) }));
    setMenu(null);
  };
  const toggleHome = () => {
    if (wanted === null) return;
    const next = homeName === wanted ? null : wanted;
    writeHome(next);
    setHomeName(next);
    setMenu(null);
  };

  // Switching dashboards is the app bar's tabs (`DashboardTabs`, via `Shell`'s `startSlot`, D-053).

  const canUndo = hist.canUndo;
  const canRedo = hist.canRedo;
  const bar = (
    <PageBar end={<ChartControls {...charts} unit={bindings.signals[0]?.unit} />}>
      {dirty && <Chip label="unsaved" color="warning" variant="outlined" data-testid="dirty" />}
      {readonly && (
        <Tooltip title="Write controls on this dashboard are disabled for everyone. Turn it off from the ⋯ menu.">
          <Chip icon={<LockOutlinedIcon />} label="read-only" variant="outlined" data-testid="readonly" />
        </Tooltip>
      )}
      {problems.length > 0 && !dirty && (
        <Tooltip title={problems.map((p) => `${p.widget_id}: ${p.reason}`).join("\n")}>
          <Chip label={`${problems.length} widget${problems.length === 1 ? "" : "s"} need${problems.length === 1 ? "s" : ""} attention`} color="warning" variant="outlined" data-testid="problems" />
        </Tooltip>
      )}
      <Button variant={editing ? "contained" : "outlined"} startIcon={editing ? <CheckIcon /> : <EditOutlinedIcon />} onClick={() => setEditing((e) => !e)} data-testid="edit-toggle" disabled={!doc || !canOperate}>
        {editing ? "Done" : "Edit"}
      </Button>
      {editing && (
        <>
          <Button variant="outlined" startIcon={<AddIcon />} onClick={() => setAdding(true)} data-testid="add-widget">
            Add widget
          </Button>
          <Tooltip title="Undo (Ctrl+Z)">
            <span>
              <IconButton aria-label="undo" onClick={hist.undo} disabled={!canUndo}>
                <UndoIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Redo (Ctrl+Shift+Z)">
            <span>
              <IconButton aria-label="redo" onClick={hist.redo} disabled={!canRedo}>
                <RedoIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        </>
      )}
      <Tooltip title={isGenerated ? "Save as a named dashboard" : "Save a new version"}>
        <span>
          <Button variant="contained" startIcon={<SaveOutlinedIcon />} disabled={busy || !doc || !canOperate || (!dirty && !isGenerated)} onClick={() => (isGenerated ? setSaveAs(true) : void save(wanted))} data-testid="save">
            Save
          </Button>
        </span>
      </Tooltip>
      <IconButton aria-label="more" onClick={(e) => setMenu(e.currentTarget)} data-testid="more">
        <MoreHorizIcon fontSize="small" />
      </IconButton>
      <Menu open={Boolean(menu)} anchorEl={menu} onClose={() => setMenu(null)}>
        <MenuItem
          data-testid="menu-save-as"
          onClick={() => {
            setMenu(null);
            setSaveAs(true);
          }}
          disabled={!doc || !canOperate}
        >
          <ListItemIcon>
            <SaveAsIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Save as…</ListItemText>
        </MenuItem>
        <MenuItem
          data-testid="menu-rename"
          onClick={() => {
            setMenu(null);
            setRenaming(true);
          }}
          disabled={isGenerated || !baseline || !canOperate}
        >
          <ListItemIcon>
            <DriveFileRenameOutlineIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Rename…</ListItemText>
        </MenuItem>
        <MenuItem
          data-testid="menu-delete"
          onClick={() => {
            setMenu(null);
            setDeleting(true);
          }}
          disabled={isGenerated || !baseline || !canOperate}
          sx={{ color: "error.main" }}
        >
          <ListItemIcon>
            <DeleteOutlineIcon fontSize="small" color="error" />
          </ListItemIcon>
          <ListItemText>Delete…</ListItemText>
        </MenuItem>
        <MenuItem
          onClick={() => {
            setMenu(null);
            revert();
          }}
          disabled={!dirty}
        >
          <ListItemIcon>
            <RestoreIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Revert to saved</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem
          data-testid="menu-export"
          onClick={() => {
            setMenu(null);
            exportFile();
          }}
          disabled={!doc}
        >
          <ListItemIcon>
            <DownloadIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Export JSON</ListItemText>
        </MenuItem>
        <MenuItem
          data-testid="menu-import"
          onClick={() => {
            setMenu(null);
            fileInput.current?.click();
          }}
          disabled={!canOperate}
        >
          <ListItemIcon>
            <UploadFileIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Import JSON…</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem data-testid="menu-readonly" onClick={toggleReadonly} disabled={!doc || !canOperate}>
          <ListItemIcon>{readonly ? <LockOpenOutlinedIcon fontSize="small" /> : <LockOutlinedIcon fontSize="small" />}</ListItemIcon>
          <ListItemText>{readonly ? "Make writable" : "Make read-only"}</ListItemText>
        </MenuItem>
        <MenuItem data-testid="menu-home" onClick={toggleHome} disabled={isGenerated}>
          <ListItemIcon>{homeName === wanted && wanted !== null ? <HomeIcon fontSize="small" /> : <HomeOutlinedIcon fontSize="small" />}</ListItemIcon>
          <ListItemText>{homeName === wanted && wanted !== null ? "Unset as home" : "Set as home"}</ListItemText>
        </MenuItem>
      </Menu>
      <input ref={fileInput} type="file" accept=".json,application/json" hidden onChange={(e) => void importFile(e)} data-testid="import-file" />
    </PageBar>
  );

  return (
    <RigDataContext.Provider value={rigData}>
          <ControllersContext.Provider value={beatControllers}>
            <EventsContext.Provider value={beatEvents}>
              {bar}
              {error && (
                <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2.25 }}>
                  {error}
                </Alert>
              )}
              {notice && (
                <Alert severity="success" onClose={() => setNotice(null)} sx={{ mb: 2.25 }}>
                  {notice}
                </Alert>
              )}
              {importErrors && (
                <Alert severity="error" onClose={() => setImportErrors(null)} sx={{ mb: 2.25 }}>
                  <strong>Not a dashboard document.</strong>
                  <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                    {importErrors.slice(0, 12).map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                </Alert>
              )}
              {loadError && (
                <Alert severity="warning" sx={{ mb: 2.25 }} action={<Button color="inherit" onClick={() => onOpen(null, true)}>{GENERATED_NAME}</Button>}>
                  {loadError}
                </Alert>
              )}
              {doc && doc.rig && rigName && doc.rig !== rigName && (
                <Alert severity="info" sx={{ mb: 2.25 }}>
                  Made for the rig “{doc.rig}”; this is “{rigName}”. Widgets naming things this rig lacks show as missing.
                </Alert>
              )}
              {doc && doc.description && !editing && (
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2.25 }}>
                  {doc.description}
                </Typography>
              )}
              {doc && doc.widgets.length === 0 && (
                <Stack alignItems="center" spacing={1.5} sx={{ py: 9, color: "text.secondary" }}>
                  <Typography>This dashboard is empty.</Typography>
                  <Button
                    variant="outlined"
                    startIcon={<AddIcon />}
                    onClick={() => {
                      setEditing(true);
                      setAdding(true);
                    }}
                  >
                    Add a widget
                  </Button>
                </Stack>
              )}
              {doc &&
                (editing ? (
                  <DashboardEditGrid widgets={doc.widgets} grid={doc.grid} onLayout={onLayout} renderWidget={renderWidget} />
                ) : (
                  <DashboardViewGrid widgets={doc.widgets} grid={doc.grid} renderWidget={renderWidget} />
                ))}
              {!doc && !loadError && <Typography color="text.secondary">loading…</Typography>}
              <AddWidgetDrawer open={adding} onClose={() => setAdding(false)} onAdd={add} />
              {configuring && (
                <ConfigureDialog
                  key={configuring.id}
                  widget={configuring}
                  onClose={() => setConfiguring(null)}
                  onApply={(updated) => {
                    patch((d) => ({ ...d, widgets: d.widgets.map((w) => (w.id === updated.id ? updated : w)) }));
                    setConfiguring(null);
                  }}
                />
              )}
              <NameDialog
                open={saveAs}
                title="Save dashboard as"
                action="Save"
                initial={isGenerated ? "" : shown}
                taken={names}
                keyed
                current={wanted}
                busy={busy}
                error={dialogError}
                onClose={() => {
                  setSaveAs(false);
                  setDialogError(null);
                }}
                onSubmit={(typed) => {
                  const as = nameFor(typed);
                  void save(as, labelFor(typed, as));
                }}
              />
              <NameDialog
                open={renaming}
                title={`Rename “${shown}”`}
                action="Rename"
                initial={shown}
                taken={names}
                keyed={false}
                current={wanted}
                busy={busy}
                error={dialogError}
                onClose={() => {
                  setRenaming(false);
                  setDialogError(null);
                }}
                onSubmit={(typed) => void rename(typed)}
              />
              <Confirm
                open={deleting}
                title={`Delete “${shown}”?`}
                text="Every saved version of this dashboard is removed. This cannot be undone."
                action="Delete"
                busy={busy}
                onClose={() => setDeleting(false)}
                onConfirm={() => void remove()}
              />
            </EventsContext.Provider>
          </ControllersContext.Provider>
    </RigDataContext.Provider>
  );
}
