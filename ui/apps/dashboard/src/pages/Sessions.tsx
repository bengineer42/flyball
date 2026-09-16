import { useEffect, useState, type FormEvent } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
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
import { SessionPanel, useNowS, useQuery, useRig, useSession, type SessionExports } from "@flyball/react";
import type { SessionRow } from "@flyball/client";
import { sessionName, useReaderRuns, type Recording } from "../model.js";
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
function RecordingControl({ recording, onChange }: { recording: Recording; onChange(): void }) {
  const nowS = useNowS();
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
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
    void run(() => recording.start(details)).then(() => {
      setName("");
      setNotes("");
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
 * whole thing as a zip, the channels wide (raw or resampled), long, JSON, and
 * the events. Every route answers with an attachment, so these are plain
 * links; the open session exports like any other.
 */
function DownloadMenu({ id }: { id: number }) {
  const rig = useRig();
  const runs = useReaderRuns();
  // The finest grid worth offering: the fastest reader currently running, else a second.
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
        {item("All channels — CSV wide", "a column per channel, a row per sample instant", rig.exportUrl(id, { format: "csv", layout: "wide" }), `session-${id}-wide.csv`)}
        <MenuItem
          onClick={() => {
            close();
            setStep(String(finest));
            setAsking(true);
          }}
        >
          <ListItemText primary="CSV wide resampled…" secondary="the same, on a regular grid" />
        </MenuItem>
        {item("CSV long", "a row per raw value: time, source, measurand, unit", rig.exportUrl(id, { format: "csv", layout: "long" }), `session-${id}-long.csv`)}
        {item("JSON", "the wide table as a list of objects", rig.exportUrl(id, { format: "json", layout: "wide" }), `session-${id}-wide.json`)}
        <Divider />
        {item("Events CSV", "flags, notes and what the rig logged", rig.eventsExportUrl(id, "csv"), `session-${id}-events.csv`)}
      </Menu>
      <Dialog open={asking} onClose={() => setAsking(false)}>
        <DialogTitle>Resample session #{id}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            One row per step, each channel holding its last value. {periods.length ? `The fastest reader samples every ${finest} s.` : "No reader period is visible, so a second is offered."}
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
    series: (source, measurand, format) => rig.seriesExportUrl(id, source, measurand, format),
    ticks: (loop, format) => rig.ticksExportUrl(id, loop, format),
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
  const [pending, setPending] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Multi-select: a set of checked ids, and the anchor row shift-click extends a range from.
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [anchorId, setAnchorId] = useState<number | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);

  const rows = sessions.data ?? [];
  // Only one session can be open at a time; it can't be selected or deleted.
  const openId = rows.find((r) => r.end_ns == null)?.id ?? null;
  const selectableIds = rows.filter((r) => r.id !== openId).map((r) => r.id);

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
      <RecordingControl recording={recording} onChange={sessions.refresh} />
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
      <SectionHead icon={PAGE_ICONS.sessions} title="Sessions" count={sessions.data?.length} />
      {!sessions.data && !sessions.error && <StateBlock state="loading" message="Loading sessions…" />}
      {sessions.data && sessions.data.length === 0 && <StateBlock state="empty" message="No sessions recorded yet. Start recording to capture one." />}
      {sessions.data && sessions.data.length > 0 && (
        <TableContainer
          component={Paper}
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
              {sessions.data.map((s) => {
                // `config` is the whole rig file as recorded: the drill-in shows it, a row never does.
                const { id, start_ns, end_ns, details, config, hardware, version } = s as SessionRow & { config?: unknown; hardware?: unknown; version?: unknown };
                const { name, notes, ...otherDetails } = (details && typeof details === "object" ? details : {}) as Record<string, unknown>;
                const rigName = config && typeof config === "object" && typeof (config as { name?: unknown }).name === "string" ? (config as { name: string }).name : null;
                const extra = Object.keys(otherDetails).length;
                const isOpen = id === openId;
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
                    <TableCell sx={{ whiteSpace: "nowrap" }}>{end_ns ? when(end_ns) : <Chip label="open" color="success" variant="outlined" />}</TableCell>
                    <TableCell>{fmtDuration(sessionSeconds(s, nowS))}</TableCell>
                    <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
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
