import { memo, useState, type FormEvent } from "react";
import { Alert, Button, Chip, Stack, TextField, Typography } from "@mui/material";
import StopCircleOutlinedIcon from "@mui/icons-material/StopCircleOutlined";
import FiberManualRecordIcon from "@mui/icons-material/FiberManualRecord";
import { Confirm } from "../Confirm.js";
import { sessionName } from "../model.js";
import { hashFor } from "../router.js";
import { duration, useNow, when } from "../time.js";
import { useRigData } from "../dashboard/context.js";
import type { WidgetKind, WidgetComponentProps } from "./types.js";

const RecordingWidget = memo(function RecordingWidget({ config }: WidgetComponentProps) {
  const { recording } = useRigData();
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
  return (
    <Stack spacing={1} sx={{ flex: "1 1 auto", minHeight: 0, justifyContent: "center" }}>
      {error && (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {open ? (
        <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
          <Chip label="recording" color="success" icon={<FiberManualRecordIcon />} />
          <Typography fontWeight={600} component="a" href={hashFor("sessions", open.id)} sx={{ color: "inherit", textDecoration: "none", "&:hover": { textDecoration: "underline" } }}>
            {sessionName(open)}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            since {when(open.start_ns)} · {duration(((open.end_ns ?? now * 1e6) - open.start_ns) / 1e9)}
          </Typography>
          {controls && (
            <Button variant="outlined" color="error" startIcon={<StopCircleOutlinedIcon />} onClick={() => setConfirmEnd(true)} disabled={busy} sx={{ ml: "auto" }}>
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
        </Stack>
      ) : (
        <Stack component="form" direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap onSubmit={start}>
          <Chip label={recording.loading && !recording.error ? "…" : "not recording"} />
          {controls && <TextField label="name" value={name} onChange={(e) => setName(e.target.value)} inputProps={{ "aria-label": "session name" }} sx={{ flexGrow: 1, minWidth: 120 }} />}
          {controls && (
            <Button type="submit" variant="contained" disabled={busy} startIcon={<FiberManualRecordIcon />}>
              Start
            </Button>
          )}
        </Stack>
      )}
    </Stack>
  );
});

export const recording: WidgetKind = {
  kind: "recording",
  label: "Recording",
  description: "The open session, and buttons to start or end one.",
  category: "control",
  defaultSize: { w: 4, h: 2 },
  minSize: { w: 3, h: 2 },
  cost: "cheap",
  configSchema: () => ({
    type: "object",
    properties: { controls: { type: "boolean", title: "Start/end buttons", default: true, description: "Off, the tile only says whether a session is open." } },
  }),
  defaultConfig: () => ({ controls: true }),
  titleFor: () => "Recording",
  Component: RecordingWidget,
};
