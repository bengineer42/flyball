import { useState, type FormEvent } from "react";
import {
  Alert,
  Box,
  Button,
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
import { SessionPanel, useQuery, useRig, useSession, type SessionExports } from "@flyball/react";
import type { SessionRow } from "@flyball/client";
import { sessionName, useReaderRuns, type Recording } from "../model.js";
import { duration, useNow, when } from "../time.js";
import { hashFor } from "../router.js";
import { Confirm } from "../Confirm.js";
import { PageBar } from "../PageBar.js";


const sessionSeconds = (s: SessionRow, nowMs: number) => ((s.end_ns ?? nowMs * 1e6) - s.start_ns) / 1e9;

/** Start a session, or end the open one. */
function RecordingControl({ recording, onChange }: { recording: Recording; onChange(): void }) {
  const now = useNow();
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
    <Paper sx={{ p: 2, mb: "16px" }}>
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 1 }}>
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
            started {when(open.start_ns)} · {duration(sessionSeconds(open, now))}
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
        {item("Everything (zip)", "every table, the loops' ticks and the metadata", rig.exportUrl(id, { format: "zip" }), `session-${id}.zip`)}
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
      {detail.data && <SessionPanel detail={detail.data} exports={exports} />}
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
  const now = useNow();
  const sessions = useQuery(() => rig.sessions(50), [rig]);
  const [pending, setPending] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  if (selected !== null) {
    return (
      <>
        {error && (
          <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 1.5 }}>
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
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 1.5 }}>
          {error}
        </Alert>
      )}
      {sessions.error && <Alert severity="error">{sessions.error.message}</Alert>}
      {!sessions.data && !sessions.error && <Typography color="text.secondary">loading…</Typography>}
      {sessions.data && (
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
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
                return (
                  <TableRow key={id} hover sx={{ cursor: "pointer" }} onClick={() => onSelect(id)}>
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
                        <Typography variant="body2" color="text.secondary" component="span" sx={{ ml: 1 }} title={Object.keys(otherDetails).join(", ")}>
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
                    <TableCell>{duration(sessionSeconds(s, now))}</TableCell>
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
              {sessions.data.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} sx={{ color: "text.secondary" }}>
                    no sessions recorded
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      {dialog}
    </>
  );
}
