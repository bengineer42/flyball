import { useEffect, useState, type FormEvent } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  FormControl,
  InputLabel,
  Select,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  IconButton,
  Link,
  ListItemText,
  Menu,
  MenuItem,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import ArrowDropDownIcon from "@mui/icons-material/ArrowDropDown";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import DownloadIcon from "@mui/icons-material/Download";
import StopCircleOutlinedIcon from "@mui/icons-material/StopCircleOutlined";
import PushPinIcon from "@mui/icons-material/PushPin";
import PushPinOutlinedIcon from "@mui/icons-material/PushPinOutlined";
import HistoryIcon from "@mui/icons-material/History";
import { SessionPanel, useDeviceRuns, useNowS, useQuery, useRig, useSession, useSimulation, type SessionExports } from "@flyball/react";
import { isScratch, type DaemonInfo, type SessionRow } from "@flyball/client";
import { sessionName, type Recording } from "../model.js";
import { duration, when } from "../time.js";
import { hashFor } from "../router.js";
import { Confirm } from "../Confirm.js";
import { PageBar } from "../PageBar.js";
import { SectionHead, StateBlock } from "../cards.js";
import { PAGE_ICONS } from "../icons.js";


// `nowS` is the rig's own clock (`useNowS()`), never the wall clock: a simulated rig can run its
// clock at any speed, so `Date.now()` against `start_ns` can read hours ahead or behind.
const sessionSeconds = (s: SessionRow, nowS: number) => ((s.end_ns ?? nowS * 1e9) - s.start_ns) / 1e9;
/** `duration()`, but never negative: `nowS` catching up to a session that only just opened would otherwise read "-13h -18m". */
const fmtDuration = (s: number) => (s < 0 ? "just started" : duration(s));

/** "#1, #2, #3, …" for a confirm dialog: the first few, then an ellipsis if there are more. */
function previewIds(ids: number[], max = 5): string {
  const shown = ids.slice(0, max).map((id) => `#${id}`).join(", ");
  return ids.length > max ? `${shown}, …` : shown;
}

/** Start a session, or end the open one. */
/** The choices for "include the last …" and "keep the last …", in minutes, cut to what the scratch record holds. */
const LAST_MINUTES = [5, 15, 30, 60, 180, 720, 1440];
const lastChoices = (scratch: SessionRow | undefined, nowS: number): number[] => {
  if (!scratch) return [];
  const heldMin = Math.max(0, (nowS - scratch.start_ns / 1e9) / 60);
  const fits = LAST_MINUTES.filter((m) => m <= heldMin);
  return heldMin >= 1 && (fits.length === 0 || heldMin > fits[fits.length - 1]! * 1.5) ? [...fits, Math.floor(heldMin)] : fits;
};
const minutesLabel = (m: number) => (m >= 60 && m % 60 === 0 ? `${m / 60} h` : m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`);

/** A configured span in the words a person would use: `30 d`, `24 h`, `90 min`; the exact form only when it is not whole. */
const span = (seconds: number): string =>
  seconds % 86400 === 0 ? `${seconds / 86400} d` : seconds % 3600 === 0 ? `${seconds / 3600} h` : seconds % 60 === 0 ? `${seconds / 60} min` : duration(seconds);

/** "kept 1 h · rotates daily · 30 d retention · 20 GB cap" from what the daemon says of its store; nothing when it says nothing. */
function retentionLine(daemon: DaemonInfo | undefined): string | null {
  if (!daemon) return null;
  const parts: string[] = [];
  if (daemon.keep_ns) parts.push(`unrecorded data kept ${span(daemon.keep_ns / 1e9)}`);
  if (daemon.retain_ns) parts.push(`sessions ${span(daemon.retain_ns / 1e9)} unless pinned`);
  if (daemon.rotate_ns) parts.push(`rotates every ${span(daemon.rotate_ns / 1e9)}`);
  if (daemon.max_bytes) parts.push(`${bytes(daemon.max_bytes)} cap`);
  if (daemon.store) parts.push(`in ${daemon.store}`);
  return parts.join(" · ") || null;
}

/** When the daemon's retention will age a closed session out, for its "ended" cell's hint; nothing for a pinned or open one. */
function keptUntil(s: SessionRow, daemon: DaemonInfo | undefined): string | undefined {
  const retain = daemon?.retain_ns;
  if (!retain || s.pinned || s.end_ns == null) return undefined;
  return `kept until ${when(s.end_ns + retain)} (${span(retain / 1e9)} retention); pin to keep it`;
}

const bytes = (n: number): string => (n >= 1e9 ? `${(n / 1e9).toFixed(n >= 1e10 ? 0 : 1)} GB` : n >= 1e6 ? `${Math.round(n / 1e6)} MB` : `${Math.round(n / 1e3)} kB`);

/** The rolling scratch record's row: what it holds, and Keep… to make a session of some of it. */
function ScratchRow({ scratch, nowS, daemon, onKeep, onSelect }: { scratch: SessionRow; nowS: number; daemon: DaemonInfo | undefined; onKeep(): void; onSelect(): void }) {
  const heldS = Math.max(0, nowS - scratch.start_ns / 1e9);
  const keep = daemon?.keep_ns;
  return (
    <TableRow hover sx={{ cursor: "pointer", "& td": { bgcolor: "action.hover" } }} onClick={onSelect} data-testid="scratch-row">
      <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
        <Tooltip title="What the daemon holds while nothing is recorded: the newest data, trimmed as it ages. Not a session until kept.">
          <Box sx={{ width: 44, height: 44, display: "flex", alignItems: "center", justifyContent: "center", color: "text.secondary" }}>
            <HistoryIcon fontSize="small" />
          </Box>
        </Tooltip>
      </TableCell>
      <TableCell>{scratch.id}</TableCell>
      <TableCell colSpan={2}>
        <Typography fontWeight={500}>last {duration(heldS)} held</Typography>
        <Typography variant="body2" color="text.secondary">
          not a recording{keep ? ` — trimmed to ${span(keep / 1e9)}` : ""}
          {scratch.bytes ? ` · ${bytes(scratch.bytes)}` : ""}
        </Typography>
      </TableCell>
      <TableCell sx={{ whiteSpace: "nowrap" }}>{when(scratch.start_ns)}</TableCell>
      <TableCell sx={{ whiteSpace: "nowrap" }}>
        <Chip label="rolling" variant="outlined" />
      </TableCell>
      <TableCell>{fmtDuration(heldS)}</TableCell>
      <TableCell padding="checkbox" colSpan={2} onClick={(e) => e.stopPropagation()}>
        <Button size="small" variant="outlined" onClick={onKeep} disabled={heldS < 60} data-testid="keep-button" sx={{ whiteSpace: "nowrap" }}>
          Keep…
        </Button>
      </TableCell>
    </TableRow>
  );
}

/** Keep the last N minutes of the scratch record as a session of its own. */
function KeepDialog({ scratch, nowS, onClose, onKept }: { scratch: SessionRow; nowS: number; onClose(): void; onKept(): void }) {
  const rig = useRig();
  const choices = lastChoices(scratch, nowS);
  const [minutes, setMinutes] = useState(choices.includes(15) ? 15 : (choices[0] ?? 0));
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keep = async () => {
    setBusy(true);
    try {
      const details: Record<string, string> = {};
      if (name.trim()) details.name = name.trim();
      if (notes.trim()) details.notes = notes.trim();
      const clock = await rig.clock(); // the rig's own now: a simulated clock is not the wall's
      const end_ns = clock.now_ns;
      const start_ns = Math.max(scratch.start_ns, end_ns - Math.round(minutes * 60 * 1e9));
      await rig.keepRange(scratch.id, { start_ns, end_ns, details });
      onKept();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open onClose={busy ? undefined : onClose} fullWidth maxWidth="xs">
      <DialogTitle>Keep as a session</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>The last part of what the daemon holds becomes a session of its own, kept like any recording.</DialogContentText>
        {error && (
          <Alert severity="error" sx={{ mb: 1.5 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        <Stack spacing={1.5}>
          <FormControl size="small" fullWidth>
            <InputLabel id="keep-last-label">the last</InputLabel>
            <Select labelId="keep-last-label" label="the last" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} inputProps={{ "aria-label": "keep the last minutes" }}>
              {choices.map((m) => (
                <MenuItem key={m} value={m}>
                  {minutesLabel(m)}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <TextField label="name" size="small" value={name} onChange={(e) => setName(e.target.value)} inputProps={{ "aria-label": "kept session name" }} autoFocus />
          <TextField label="notes" size="small" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button variant="contained" onClick={() => void keep()} disabled={busy || minutes <= 0} data-testid="keep-confirm">
          Keep
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function RecordingControl({ recording, scratch, onChange }: { recording: Recording; scratch?: SessionRow; onChange(): void }) {
  const nowS = useNowS();
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [includeMin, setIncludeMin] = useState(0);
  const choices = lastChoices(scratch, nowS);
  const [busy, setBusy] = useState(false);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = recording.data;

  const run = async (op: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await op();
      setError(null);
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const start = (e: FormEvent) => {
    e.preventDefault();
    const details: Record<string, string> = {};
    if (name.trim()) details.name = name.trim();
    if (notes.trim()) details.notes = notes.trim();
    const include = includeMin > 0 && choices.includes(includeMin) ? { include_ns: Math.round(includeMin * 60 * 1e9) } : {};
    void run(() => recording.start(details, include)).then(() => {
      setName("");
      setNotes("");
      setIncludeMin(0);
    });
  };

  return (
    <Paper sx={{ p: 3, mb: "16px" }}>
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 1.5 }}>
          {error}
        </Alert>
      )}
      {open ? (
        <Stack direction="row" alignItems="center" spacing={1.5} flexWrap="wrap" useFlexGap>
          <Chip label="recording" color="success" />
          <Typography fontWeight={600}>
            <Link href={hashFor("sessions", open.id)} underline="hover" color="inherit">
              {sessionName(open)}
            </Link>
          </Typography>
          <Typography variant="body2" color="text.secondary">
            started {when(open.start_ns)} · {fmtDuration(sessionSeconds(open, nowS))}
          </Typography>
          <Button variant="outlined" color="error" startIcon={<StopCircleOutlinedIcon />} onClick={() => setConfirmEnd(true)} disabled={busy} sx={{ ml: "auto" }}>
            End recording
          </Button>
          <Confirm
            open={confirmEnd}
            title={`End ${sessionName(open)}?`}
            text="Recording stops and the session is closed. Its data is kept."
            action="End recording"
            busy={busy}
            onClose={() => setConfirmEnd(false)}
            onConfirm={() => void run(() => recording.end()).then(() => setConfirmEnd(false))}
          />
        </Stack>
      ) : (
        <Stack component="form" direction="row" alignItems="center" spacing={1.5} flexWrap="wrap" useFlexGap onSubmit={start}>
          <Chip label={recording.loading && !recording.error ? "…" : "not recording"} />
          <TextField label="name" value={name} onChange={(e) => setName(e.target.value)} inputProps={{ "aria-label": "session name" }} />
          <TextField label="notes" value={notes} onChange={(e) => setNotes(e.target.value)} sx={{ flexGrow: 1, minWidth: 160 }} />
          {choices.length > 0 && (
            <FormControl size="small" sx={{ minWidth: 150 }}>
              <InputLabel id="include-last-label">include the last</InputLabel>
              <Select labelId="include-last-label" label="include the last" value={includeMin} onChange={(e) => setIncludeMin(Number(e.target.value))} inputProps={{ "aria-label": "include the last minutes held" }}>
                <MenuItem value={0}>nothing</MenuItem>
                {choices.map((m) => (
                  <MenuItem key={m} value={m}>
                    {minutesLabel(m)}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          )}
          <Button type="submit" variant="contained" disabled={busy}>
            Start recording
          </Button>
        </Stack>
      )}
    </Paper>
  );
}

/** Follow a download URL without leaving the page: the response is an attachment, so nothing navigates. */
function follow(href: string, name: string) {
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/**
 * Everything the store will send for one session, as a menu of links: the
 * whole thing as a zip, the signals wide (raw or resampled), long, JSON, and
 * the events. Every route answers with an attachment, so these are plain
 * links; the open session exports like any other.
 */
function DownloadMenu({ id }: { id: number }) {
  const rig = useRig();
  const runs = useDeviceRuns();
  // The finest grid worth offering: the fastest device currently polled, else a second.
  const periods = Object.values(runs)
    .map((r) => r.period_s)
    .filter((p): p is number => typeof p === "number" && p > 0);
  const finest = periods.length ? Math.min(...periods) : 1;
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [asking, setAsking] = useState(false);
  const [step, setStep] = useState("");
  const close = () => setAnchor(null);
  const item = (label: string, hint: string, href: string, name: string) => (
    <MenuItem component="a" href={href} download={name} onClick={close}>
      <ListItemText primary={label} secondary={hint} />
    </MenuItem>
  );
  const seconds = Number(step);
  const valid = step.trim() !== "" && Number.isFinite(seconds) && seconds > 0;
  return (
    <>
      <Button
        variant="outlined"
        startIcon={<DownloadIcon />}
        endIcon={<ArrowDropDownIcon />}
        aria-haspopup="menu"
        aria-expanded={Boolean(anchor)}
        onClick={(e) => setAnchor(e.currentTarget)}
      >
        Download
      </Button>
      <Menu open={Boolean(anchor)} anchorEl={anchor} onClose={close}>
        {item("Everything (zip)", "every table, the controllers' ticks and the metadata", rig.exportUrl(id, { format: "zip" }), `session-${id}.zip`)}
        <Divider />
        {item("All signals — CSV wide", "a column per signal, a row per sample instant", rig.exportUrl(id, { format: "csv", layout: "wide" }), `session-${id}-wide.csv`)}
        <MenuItem
          onClick={() => {
            close();
            setStep(String(finest));
            setAsking(true);
          }}
        >
          <ListItemText primary="CSV wide resampled…" secondary="the same, on a regular grid" />
        </MenuItem>
        {item("CSV long", "a row per raw value: time, signal, unit", rig.exportUrl(id, { format: "csv", layout: "long" }), `session-${id}-long.csv`)}
        {item("JSON", "the wide table as a list of objects", rig.exportUrl(id, { format: "json", layout: "wide" }), `session-${id}-wide.json`)}
        <Divider />
        {item("Events CSV", "flags, notes and what the rig logged", rig.eventsExportUrl(id, "csv"), `session-${id}-events.csv`)}
      </Menu>
      <Dialog open={asking} onClose={() => setAsking(false)}>
        <DialogTitle>Resample session #{id}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            One row per step, each signal holding its last value. {periods.length ? `The fastest device polls every ${finest} s.` : "No poll period is visible, so a second is offered."}
          </DialogContentText>
          <TextField
            autoFocus
            margin="dense"
            type="number"
            label="step"
            value={step}
            onChange={(e) => setStep(e.target.value)}
            inputProps={{ min: 0, step: "any", "aria-label": "step in seconds" }}
            InputProps={{ endAdornment: <Typography color="text.secondary">s</Typography> }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAsking(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!valid}
            onClick={() => {
              follow(rig.exportUrl(id, { format: "csv", layout: "wide", step_s: seconds }), `session-${id}-wide-${seconds}s.csv`);
              setAsking(false);
            }}
          >
            Download
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

/** One session top to bottom, from the library's `SessionPanel`; back link and delete above it. */
function SessionDrillIn({ id, onBack, onDelete }: { id: number; onBack(): void; onDelete(id: number): void }) {
  const rig = useRig();
  const detail = useSession(id);
  const nowS = useNowS();
  const open = detail.data ? !detail.data.session.end_ns : true;
  // The panel is pure: it takes the URLs, the client knows how to build them.
  const exports: SessionExports = {
    series: (address, format) => rig.seriesExportUrl(id, address, format),
    writes: (address, format) => rig.writesExportUrl(id, address, format),
    ticks: (controller, format) => rig.ticksExportUrl(id, controller, format),
    events: (format) => rig.eventsExportUrl(id, format),
  };
  return (
    <>
      <PageBar
        end={
          <Stack direction="row" spacing={1} alignItems="center">
            <DownloadMenu id={id} />
            <Tooltip title={open ? "Still recording" : "Delete session"}>
              <span>
                <IconButton aria-label={`delete session ${id}`} disabled={open} onClick={() => onDelete(id)}>
                  <DeleteOutlineIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
        }
      >
        <Button startIcon={<ArrowBackIcon />} onClick={onBack} href={hashFor("sessions")}>
          sessions
        </Button>
        <Typography variant="body2" color="text.secondary">
          #{id}
        </Typography>
      </PageBar>
      {detail.error && <Alert severity="error">{detail.error.message}</Alert>}
      {!detail.data && !detail.error && <Typography color="text.secondary">loading…</Typography>}
      {detail.data && <SessionPanel detail={detail.data} exports={exports} nowS={nowS} />}
    </>
  );
}

export interface SessionsProps {
  recording: Recording;
  /** The session shown in full, from the hash; `null` for the list. */
  selected: number | null;
  onSelect(id: number | null): void;
}

/** Recording control, recorded sessions newest first, and one session in full when selected. */
export function Sessions({ recording, selected, onSelect }: SessionsProps) {
  const rig = useRig();
  const nowS = useNowS();
  const sessions = useQuery(() => rig.sessions(50), [rig]);
  const simulation = useSimulation();
  const [pending, setPending] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Multi-select: a set of checked ids, and the anchor row shift-click extends a range from.
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [anchorId, setAnchorId] = useState<number | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);

  // The list may stamp an open session with its last sample as `end_ns`; the recording endpoint is the word on which is open.
  const rows = (sessions.data ?? []).map((r) => (recording.data && r.id === recording.data.id && recording.data.end_ns == null ? { ...r, end_ns: null } : r));
  // The daemon's rolling scratch record (the last `keep` while nothing is recorded): shown apart, kept from, never selected.
  const scratch = rows.find((r) => isScratch(r) && r.end_ns == null);
  const daemon = useQuery(() => rig.daemon().catch(() => undefined), [rig], { refreshMs: 30000 });
  const [keeping, setKeeping] = useState<SessionRow | null>(null);
  const [pinBusy, setPinBusy] = useState<number | null>(null);
  const pin = async (s: SessionRow) => {
    setPinBusy(s.id);
    try {
      await rig.pinSession(s.id, !s.pinned);
      sessions.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPinBusy(null);
    }
  };
  // Only one session can be open at a time; it can't be selected or deleted.
  const openId = rows.find((r) => r.end_ns == null && !isScratch(r))?.id ?? null;
  const selectableIds = rows.filter((r) => r.id !== openId && !isScratch(r)).map((r) => r.id);

  // Drop ids from the selection once they're no longer in the list (deleted, or fallen off the page).
  const sessionsData = sessions.data;
  useEffect(() => {
    if (!sessionsData) return;
    setChecked((prev) => {
      const valid = new Set(sessionsData.map((r) => r.id));
      const next = new Set([...prev].filter((id) => valid.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [sessionsData]);

  /** Apply a checkbox click's modifiers: shift extends the range from the anchor, ctrl/⌘ toggles in place, plain click toggles and moves the anchor. */
  const toggleWithModifiers = (e: { shiftKey: boolean; ctrlKey: boolean; metaKey: boolean }, id: number) => {
    if (e.shiftKey && anchorId !== null) {
      const ids = rows.map((r) => r.id);
      const from = ids.indexOf(anchorId);
      const to = ids.indexOf(id);
      if (from !== -1 && to !== -1) {
        const [lo, hi] = from < to ? [from, to] : [to, from];
        const next = new Set<number>();
        for (let i = lo; i <= hi; i++) {
          const rid = ids[i];
          if (rid !== undefined && rid !== openId) next.add(rid);
        }
        setChecked(next);
      }
      return;
    }
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    if (!(e.ctrlKey || e.metaKey)) setAnchorId(id);
  };

  const confirmBulkDelete = async () => {
    const ids = rows.filter((r) => checked.has(r.id)).map((r) => r.id);
    setBulkDeleting(true);
    try {
      const failed = await rig.deleteSessions(ids);
      setError(
        failed.length
          ? `${failed.length} of ${ids.length} session${ids.length === 1 ? "" : "s"} failed to delete: ${failed
              .map((f) => `#${f.id} (${f.error})`)
              .join("; ")}`
          : null,
      );
      if (selected !== null && ids.includes(selected) && !failed.some((f) => f.id === selected)) onSelect(null);
      setChecked(new Set(failed.map((f) => f.id)));
      setBulkOpen(false);
      sessions.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBulkDeleting(false);
    }
  };

  const confirmDelete = async () => {
    if (pending === null) return;
    setDeleting(true);
    try {
      await rig.deleteSession(pending);
      setError(null);
      if (selected === pending) onSelect(null);
      setPending(null);
      sessions.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDeleting(false);
    }
  };

  const dialog = (
    <Confirm
      open={pending !== null}
      title={`Delete session #${pending}?`}
      text="Everything it recorded is removed. This cannot be undone."
      action="Delete"
      busy={deleting}
      onClose={() => setPending(null)}
      onConfirm={() => void confirmDelete()}
    />
  );

  const checkedIds = rows.filter((r) => checked.has(r.id)).map((r) => r.id);
  const bulkDialog = (
    <Confirm
      open={bulkOpen}
      title={`Delete ${checkedIds.length} session${checkedIds.length === 1 ? "" : "s"}?`}
      text={`${previewIds(checkedIds)}. Everything they recorded is removed. This cannot be undone.`}
      action="Delete"
      busy={bulkDeleting}
      onClose={() => setBulkOpen(false)}
      onConfirm={() => void confirmBulkDelete()}
    />
  );

  if (selected !== null) {
    return (
      <>
        {error && (
          <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2.25 }}>
            {error}
          </Alert>
        )}
        <SessionDrillIn id={selected} onBack={() => onSelect(null)} onDelete={setPending} />
        {dialog}
      </>
    );
  }

  return (
    <>
      <RecordingControl recording={recording} scratch={scratch} onChange={sessions.refresh} />
      {keeping && <KeepDialog scratch={keeping} nowS={nowS} onClose={() => setKeeping(null)} onKept={() => { setKeeping(null); sessions.refresh(); }} />}
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2.25 }}>
          {error}
        </Alert>
      )}
      {checked.size > 0 && (
        <PageBar>
          <Typography variant="body2" fontWeight={600}>
            {checked.size} selected
          </Typography>
          <Button size="small" color="error" startIcon={<DeleteOutlineIcon />} onClick={() => setBulkOpen(true)}>
            Delete
          </Button>
          <Button size="small" onClick={() => setChecked(new Set())}>
            Clear
          </Button>
        </PageBar>
      )}
      {sessions.error && <Alert severity="error">{sessions.error.message}</Alert>}
      <SectionHead
        icon={PAGE_ICONS.sessions}
        title="Sessions"
        count={sessions.data?.length}
        end={
          <Typography variant="body2" color="text.secondary" component="span">
            {[
              simulation.speed !== undefined && simulation.speed !== 1 ? `times are the rig's clock, ×${simulation.speed}` : null,
              retentionLine(daemon.data),
            ]
              .filter(Boolean)
              .join(" · ")}
          </Typography>
        }
      />
      {!sessions.data && !sessions.error && <StateBlock state="loading" message="Loading sessions…" />}
      {sessions.data && sessions.data.length === 0 && <StateBlock state="empty" message="No sessions recorded yet. Start recording to capture one." />}
      {sessions.data && sessions.data.length > 0 && (
        <TableContainer
          component={Paper}
          className="fb-scroll-shadow-x"
          onKeyDown={(e) => {
            if (e.key === "Escape" && checked.size > 0) {
              e.stopPropagation();
              setChecked(new Set());
            }
          }}
        >
          <Table>
            <TableHead>
              <TableRow>
                <TableCell padding="checkbox">
                  <Checkbox
                    sx={{ width: 44, height: 44 }}
                    checked={selectableIds.length > 0 && selectableIds.every((id) => checked.has(id))}
                    indeterminate={selectableIds.some((id) => checked.has(id)) && !selectableIds.every((id) => checked.has(id))}
                    disabled={selectableIds.length === 0}
                    onChange={() => {
                      const allSelected = selectableIds.length > 0 && selectableIds.every((id) => checked.has(id));
                      setChecked(allSelected ? new Set() : new Set(selectableIds));
                    }}
                    inputProps={{ "aria-label": "select all sessions" }}
                  />
                </TableCell>
                <TableCell>id</TableCell>
                <TableCell>name</TableCell>
                <TableCell>rig</TableCell>
                <TableCell>started</TableCell>
                <TableCell>ended</TableCell>
                <TableCell>duration</TableCell>
                <TableCell padding="checkbox" />
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((s) => {
                // `config` is the whole rig file as recorded: the drill-in shows it, a row never does.
                const { id, start_ns, end_ns, details, config, hardware, version } = s as SessionRow & { config?: unknown; hardware?: unknown; version?: unknown };
                const { name, notes, ...otherDetails } = (details && typeof details === "object" ? details : {}) as Record<string, unknown>;
                const rigName = config && typeof config === "object" && typeof (config as { name?: unknown }).name === "string" ? (config as { name: string }).name : null;
                const extra = Object.keys(otherDetails).length;
                const isOpen = id === openId;
                if (isScratch(s)) return <ScratchRow key={id} scratch={s} nowS={nowS} daemon={daemon.data} onKeep={() => setKeeping(s)} onSelect={() => onSelect(id)} />;
                return (
                  <TableRow key={id} hover sx={{ cursor: "pointer" }} onClick={() => onSelect(id)}>
                    <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
                      {isOpen ? (
                        <Tooltip title="recording">
                          <span>
                            <Checkbox disabled sx={{ width: 44, height: 44 }} inputProps={{ "aria-label": `session ${id} is recording` }} />
                          </span>
                        </Tooltip>
                      ) : (
                        <Checkbox
                          sx={{ width: 44, height: 44 }}
                          checked={checked.has(id)}
                          onClick={(e) => {
                            e.preventDefault();
                            toggleWithModifiers(e, id);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "Escape") setChecked(new Set());
                          }}
                          inputProps={{ "aria-label": `select session ${id}` }}
                        />
                      )}
                    </TableCell>
                    <TableCell>{id}</TableCell>
                    <TableCell>
                      <Link href={hashFor("sessions", id)} underline="hover" color="inherit" fontWeight={500} onClick={(e) => e.preventDefault()}>
                        {typeof name === "string" && name ? name : "—"}
                      </Link>
                      {typeof notes === "string" && notes && (
                        <Typography variant="body2" color="text.secondary">
                          {notes}
                        </Typography>
                      )}
                      {extra > 0 && (
                        <Typography variant="body2" color="text.secondary" component="span" sx={{ ml: 1.5 }} title={Object.keys(otherDetails).join(", ")}>
                          {extra} note{extra === 1 ? "" : "s"}
                        </Typography>
                      )}
                      {s.continues != null && (
                        <Chip label={`continues #${s.continues}`} size="small" variant="outlined" sx={{ ml: 1 }} title="The daemon rotated at a boundary: this session carries on from that one" />
                      )}
                    </TableCell>
                    <TableCell>
                      <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap" useFlexGap>
                        {rigName && (
                          <Typography variant="body2" color="text.secondary">
                            {rigName}
                          </Typography>
                        )}
                        {typeof hardware === "string" && hardware && <Chip label={hardware} variant="outlined" />}
                        {version != null && version !== "" && <Chip label={`v${String(version)}`} variant="outlined" />}
                        {!rigName && !hardware && version == null && (
                          <Typography variant="body2" color="text.disabled">
                            —
                          </Typography>
                        )}
                      </Stack>
                    </TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>{when(start_ns)}</TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap" }} title={keptUntil(s, daemon.data)}>
                      {end_ns ? when(end_ns) : <Chip label="open" color="success" variant="outlined" />}
                    </TableCell>
                    <TableCell>{fmtDuration(sessionSeconds(s, nowS))}</TableCell>
                    <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
                      {daemon.data?.retain_ns != null && daemon.data.retain_ns > 0 && (
                        <Tooltip title={s.pinned ? "Pinned: never aged out. Unpin?" : `Pin: keep past the ${span(daemon.data.retain_ns / 1e9)} retention`}>
                          <span>
                            <IconButton aria-label={`${s.pinned ? "unpin" : "pin"} session ${id}`} disabled={pinBusy === id} onClick={() => void pin(s)}>
                              {s.pinned ? <PushPinIcon fontSize="small" color="primary" /> : <PushPinOutlinedIcon fontSize="small" />}
                            </IconButton>
                          </span>
                        </Tooltip>
                      )}
                      <Tooltip title="Download everything (zip)">
                        <IconButton
                          component="a"
                          aria-label={`download session ${id}`}
                          href={rig.exportUrl(id, { format: "zip" })}
                          download={`session-${id}.zip`}
                        >
                          <DownloadIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title={end_ns ? "Delete session" : "Still recording"}>
                        <span>
                          <IconButton aria-label={`delete session ${id}`} disabled={!end_ns} onClick={() => setPending(id)}>
                            <DeleteOutlineIcon fontSize="small" />
                          </IconButton>
                        </span>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      {dialog}
      {bulkDialog}
    </>
  );
}
