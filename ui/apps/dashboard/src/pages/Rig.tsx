import { useState } from "react";
import { Alert, Box, Button, Checkbox, FormControlLabel, IconButton, List, ListItem, ListItemText, Paper, Stack, TextField, Tooltip, Typography } from "@mui/material";
import RestoreIcon from "@mui/icons-material/Restore";
import { useRig, useRigChanges, useRigDocument, useRigVersions } from "@flyball/react";
import { RigError, type RigVersion } from "@flyball/client";
import { dumpYaml } from "../programText.js";
import { Confirm } from "../Confirm.js";
import { SectionHead, StateBlock } from "../cards.js";
import { PAGE_ICONS } from "../icons.js";
import { when } from "../time.js";

const detail = (e: unknown) => (e instanceof RigError ? e.detail : e instanceof Error ? e.message : String(e));

/** A document (or an overlay of one) as read-only YAML, in a monospace block. */
function YamlBlock({ value, empty, highlight }: { value: unknown; empty?: string; highlight?: boolean }) {
  if (empty !== undefined && (value === undefined || (typeof value === "object" && value !== null && Object.keys(value).length === 0))) {
    return (
      <Typography color="text.secondary" sx={{ fontStyle: "italic" }}>
        {empty}
      </Typography>
    );
  }
  return (
    <Box
      component="pre"
      sx={{
        m: 0,
        p: 1.5,
        overflow: "auto",
        maxHeight: 480,
        fontFamily: "monospace",
        fontSize: "0.8125rem",
        lineHeight: 1.5,
        borderRadius: 1,
        bgcolor: highlight ? (t) => (t.palette.mode === "dark" ? "rgba(255,193,7,0.08)" : "rgba(255,193,7,0.12)") : "action.hover",
        border: highlight ? "1px solid" : undefined,
        borderColor: highlight ? "warning.main" : undefined,
      }}
    >
      {dumpYaml(value)}
    </Box>
  );
}

/** One version row: when, why, what files it touched, and a restore button. */
function VersionRow({ version, onRestore, busy }: { version: RigVersion; onRestore(v: RigVersion): void; busy: boolean }) {
  return (
    <ListItem
      divider
      secondaryAction={
        <Tooltip title="Rebuild the running rig to match this version">
          <span>
            <IconButton edge="end" aria-label={`restore version ${version.id}`} onClick={() => onRestore(version)} disabled={busy} data-testid={`restore-${version.id}`}>
              <RestoreIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      }
    >
      <ListItemText
        primary={`#${version.id} · ${version.reason}`}
        secondary={`${when(version.time_ns)}${version.files.length ? ` · ${version.files.join(", ")}` : ""}`}
      />
    </ListItem>
  );
}

/** Save the running rig: the default overlay beside the loaded file, or the whole rig to a chosen path. */
function SaveBox() {
  const rig = useRig();
  const [path, setPath] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const result = await rig.saveRig(path.trim() ? { path: path.trim(), overwrite } : {});
      setSaved(result.path);
    } catch (e) {
      setError(detail(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Paper sx={{ p: 3 }}>
      <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 1.125 }}>
        Save
      </Typography>
      <Stack spacing={1.5}>
        <Typography variant="body2" color="text.secondary">
          With no path, only what changed since the daemon started is written, to an overlay beside the rig file it was loaded from. A path writes the whole rig there instead.
        </Typography>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ xs: "flex-start", sm: "center" }}>
          <TextField
            size="small"
            label="path (blank: the overlay)"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="rig.yaml"
            sx={{ minWidth: 260 }}
            inputProps={{ "data-testid": "save-path" }}
          />
          <FormControlLabel
            control={<Checkbox checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} disabled={!path.trim()} />}
            label="overwrite a loaded file"
          />
          <Button variant="contained" onClick={() => void save()} disabled={busy} data-testid="save-rig">
            Save
          </Button>
        </Stack>
        {saved && (
          <Alert severity="success" onClose={() => setSaved(null)} data-testid="save-result">
            Written to {saved}.
          </Alert>
        )}
        {error && (
          <Alert severity="error" onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
      </Stack>
    </Paper>
  );
}

/**
 * `#/rig`: the running rig as a file would show it, what has changed since
 * the daemon started, its version history with a restore per row, and a box
 * to save it -- the overlay by default, or the whole rig to a path.
 */
export function RigPage() {
  const rig = useRig();
  const document = useRigDocument(5000);
  const changes = useRigChanges(5000);
  const versions = useRigVersions(50);
  const [restoring, setRestoring] = useState<RigVersion | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const restore = async () => {
    if (!restoring) return;
    setBusy(true);
    try {
      await rig.restoreVersion(restoring.id);
      setError(null);
      setRestoring(null);
      document.refresh();
      changes.refresh();
      versions.refresh();
    } catch (e) {
      setError(detail(e));
    } finally {
      setBusy(false);
    }
  };

  const changeCount = changes.data ? Object.keys(changes.data).length : 0;

  return (
    <>
      <SectionHead icon={PAGE_ICONS.rig} title="Rig" />
      <div className="grid">
        <Paper className="c12 xl6" sx={{ p: 3 }}>
          <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 1.125 }}>
            Running document
          </Typography>
          {document.error && <Alert severity="error">{document.error.message}</Alert>}
          {!document.error && (document.data ? <YamlBlock value={document.data} /> : <Typography color="text.secondary">loading…</Typography>)}
        </Paper>
        <Paper className="c12 xl6" sx={{ p: 3 }}>
          <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 1.125 }}>
            Changed since start {changeCount > 0 && `· ${changeCount}`}
          </Typography>
          {changes.error && <Alert severity="error">{changes.error.message}</Alert>}
          {!changes.error && (changes.data !== undefined ? <YamlBlock value={changes.data} empty="Nothing changed." highlight={changeCount > 0} /> : <Typography color="text.secondary">loading…</Typography>)}
        </Paper>
        <Paper className="c12 xl6" sx={{ p: 3 }}>
          <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 1.125 }}>
            Versions {versions.data && `· ${versions.data.length}`}
          </Typography>
          {versions.error && <Alert severity="error">{versions.error.message}</Alert>}
          {!versions.error && versions.data && versions.data.length === 0 && <StateBlock state="empty" message="No version recorded yet." />}
          {!versions.error && versions.data && versions.data.length > 0 && (
            <List dense disablePadding>
              {versions.data.map((v) => (
                <VersionRow key={v.id} version={v} onRestore={setRestoring} busy={busy} />
              ))}
            </List>
          )}
          {error && (
            <Alert severity="error" sx={{ mt: 1.5 }} onClose={() => setError(null)}>
              {error}
            </Alert>
          )}
        </Paper>
        <div className="c12 xl6">
          <SaveBox />
        </div>
      </div>
      <Confirm
        open={restoring !== null}
        title={restoring ? `Restore version #${restoring.id}?` : ""}
        text="Rebuilds the running rig to match this version: what it lacks is added, what it has that this version does not is removed, and a changed device is rebuilt. Recorded as a new version of its own."
        action="Restore"
        busy={busy}
        danger={false}
        onClose={() => setRestoring(null)}
        onConfirm={() => void restore()}
      />
    </>
  );
}
