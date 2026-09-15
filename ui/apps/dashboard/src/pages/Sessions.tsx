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
  IconButton,
  Link,
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
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import StopCircleOutlinedIcon from "@mui/icons-material/StopCircleOutlined";
import { SessionPanel, useQuery, useRecording, useRig, useSession, ValueView } from "@flyball/react";
import type { SessionRow } from "@flyball/client";
import { duration, useNow, when } from "../time.js";
import { hashFor } from "../router.js";

export type Recording = ReturnType<typeof useRecording>;

/** A session's display name from its free-form details, else its id. */
export const sessionName = (s: SessionRow) => {
  const d = s.details as Record<string, unknown> | null | undefined;
  return d && typeof d.name === "string" && d.name ? d.name : `session #${s.id}`;
};

const sessionSeconds = (s: SessionRow, nowMs: number) => ((s.end_ns ?? nowMs * 1e6) - s.start_ns) / 1e9;

/** A yes/no dialog. */
function Confirm({ open, title, text, action, busy, onClose, onConfirm }: { open: boolean; title: string; text: string; action: string; busy: boolean; onClose(): void; onConfirm(): void }) {
  return (
    <Dialog open={open} onClose={() => (busy ? undefined : onClose())}>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        <DialogContentText>{text}</DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button color="error" variant="contained" onClick={onConfirm} disabled={busy}>
          {action}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

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
    <Paper sx={{ p: 1.5, mb: 1.5 }}>
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

/** One session top to bottom, from the library's `SessionPanel`; back link and delete above it. */
function SessionDrillIn({ id, onBack, onDelete }: { id: number; onBack(): void; onDelete(id: number): void }) {
  const detail = useSession(id);
  const open = detail.data ? !detail.data.session.end_ns : true;
  return (
    <>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.5 }}>
        <Button startIcon={<ArrowBackIcon />} onClick={onBack} href={hashFor("sessions")}>
          sessions
        </Button>
        <Typography variant="body2" color="text.secondary">
          #{id}
        </Typography>
        <Box sx={{ ml: "auto !important" }}>
          <Tooltip title={open ? "Still recording" : "Delete session"}>
            <span>
              <IconButton aria-label={`delete session ${id}`} disabled={open} onClick={() => onDelete(id)}>
                <DeleteOutlineIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        </Box>
      </Stack>
      {detail.error && <Alert severity="error">{detail.error.message}</Alert>}
      {!detail.data && !detail.error && <Typography color="text.secondary">loading…</Typography>}
      {detail.data && <SessionPanel detail={detail.data} />}
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
                <TableCell>started</TableCell>
                <TableCell>ended</TableCell>
                <TableCell>duration</TableCell>
                <TableCell>details</TableCell>
                <TableCell padding="checkbox" />
              </TableRow>
            </TableHead>
            <TableBody>
              {sessions.data.map((s) => {
                const { id, start_ns, end_ns, details, ...rest } = s;
                const { name, notes, ...otherDetails } = (details && typeof details === "object" ? details : {}) as Record<string, unknown>;
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
                    </TableCell>
                    <TableCell>{when(start_ns)}</TableCell>
                    <TableCell>{end_ns ? when(end_ns) : <Chip label="open" color="success" variant="outlined" />}</TableCell>
                    <TableCell>{duration(sessionSeconds(s, now))}</TableCell>
                    <TableCell sx={{ color: "text.secondary" }}>
                      <ValueView value={{ ...otherDetails, ...rest }} />
                    </TableCell>
                    <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
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
