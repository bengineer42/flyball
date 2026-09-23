import { memo, useState, type FormEvent } from "react";
import { Alert, Button, Chip, TextField, Typography } from "@mui/material";
import StopCircleOutlinedIcon from "@mui/icons-material/StopCircleOutlined";
import FiberManualRecordIcon from "@mui/icons-material/FiberManualRecord";
import { Confirm } from "../Confirm.js";
import { sessionName } from "../model.js";
import { hashFor } from "../router.js";
import { duration, useNow, when } from "../time.js";
import { useCanWrite, useRigData } from "../dashboard/context.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

const RecordingWidget = memo(function RecordingWidget({ config }: WidgetComponentProps) {
  const { recording } = useRigData();
  const canWrite = useCanWrite();
  const now = useNow();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = recording.data;
  const run = async (op: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await op();
      setError(null);
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
    void run(() => recording.start(details)).then(() => setName(""));
  };
  const controls = config.controls !== false;
  // One row, always (dashboard.css `.dash-recording`): a 6×3 body is 42px, which holds a small field and button and nothing more.
  // An error replaces the row rather than stacking under it.
  if (error)
    return (
      <div className="dash-recording">
        <Alert severity="error" onClose={() => setError(null)} sx={{ py: 0, flex: "1 1 auto", minWidth: 0, alignItems: "center" }}>
          {error}
        </Alert>
      </div>
    );
  return open ? (
    <div className="dash-recording">
      <Chip size="small" label="recording" color="success" icon={<FiberManualRecordIcon />} />
      <Typography fontWeight={600} noWrap component="a" href={hashFor("sessions", open.id)} sx={{ color: "inherit", textDecoration: "none", "&:hover": { textDecoration: "underline" } }}>
        {sessionName(open)}
      </Typography>
      <Typography variant="body2" color="text.secondary" noWrap sx={{ minWidth: 0, flex: "1 1 auto" }}>
        since {when(open.start_ns)} · {duration(((open.end_ns ?? now * 1e6) - open.start_ns) / 1e9)}
      </Typography>
      {controls && (
        <Button size="small" variant="outlined" color="error" startIcon={<StopCircleOutlinedIcon />} onClick={() => setConfirmEnd(true)} disabled={busy || !canWrite} sx={{ flex: "none" }}>
          End
        </Button>
      )}
      <Confirm
        open={confirmEnd}
        title={`End ${sessionName(open)}?`}
        text="Recording stops and the session is closed. Its data is kept."
        action="End recording"
        busy={busy}
        onClose={() => setConfirmEnd(false)}
        onConfirm={() => void run(() => recording.end()).then(() => setConfirmEnd(false))}
      />
    </div>
  ) : (
    <form className="dash-recording" onSubmit={start}>
      <Chip size="small" label={recording.loading && !recording.error ? "…" : "not recording"} />
      {controls && <TextField size="small" label="name" disabled={!canWrite} value={name} onChange={(e) => setName(e.target.value)} inputProps={{ "aria-label": "session name" }} sx={{ flex: "1 1 auto", minWidth: 0 }} />}
      {controls && (
        <Button size="small" type="submit" variant="contained" disabled={busy || !canWrite} startIcon={<FiberManualRecordIcon />} sx={{ flex: "none" }}>
          Start
        </Button>
      )}
    </form>
  );
});

export const recording: WidgetKind = {
  kind: "recording",
  label: "Recording",
  description: "The open session, and buttons to start or end one.",
  category: "control",
  // 6×3: one 40px row (chip · name · Start) in a 42px body; below 3 rows the controls cannot fit (DESIGN-SPEC.md §10).
  defaultSize: { w: 6, h: 3 },
  minSize: { w: 4, h: 3 },
  cost: "cheap",
  configSchema: () => ({
    type: "object",
    properties: { controls: { type: "boolean", title: "Start/end buttons", default: true, description: "Off, the tile only says whether a session is open." } },
  }),
  defaultConfig: () => ({ controls: true }),
  titleFor: () => "Recording",
  Component: RecordingWidget,
};
