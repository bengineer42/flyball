import { useState } from "react";
import { Alert, Box, Button, Checkbox, Chip, FormControlLabel, IconButton, List, ListItem, ListItemText, Paper, Stack, TextField, Tooltip, Typography } from "@mui/material";
import RestoreIcon from "@mui/icons-material/Restore";
import PowerSettingsNewIcon from "@mui/icons-material/PowerSettingsNew";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import CheckIcon from "@mui/icons-material/Check";
import { useQuery, useRig, useRigChanges, useRigDocument, useRigVersions } from "@flyball/react";
import { RigError, pageBase, type RunnerInfo, type RigVersion } from "@flyball/client";
import { dumpYaml } from "../programText.js";
import { Confirm } from "../Confirm.js";
import { SectionHead, StateBlock } from "../cards.js";
import { PAGE_ICONS } from "../icons.js";
import { when } from "../time.js";
import { useAuth } from "../auth.js";

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

/** One version row: when, why, what files it touched, and a restore button; the head (where the rig is) is marked and has nothing to restore. */
function VersionRow({ version, onRestore, busy }: { version: RigVersion; onRestore(v: RigVersion): void; busy: boolean }) {
  const head = version.head === true;
  return (
    <ListItem
      divider
      secondaryAction={
        <Tooltip title={head ? "The running rig is at this version" : "Rebuild the running rig to match this version"}>
          <span>
            <IconButton edge="end" aria-label={`restore version ${version.id}`} onClick={() => onRestore(version)} disabled={busy || head} data-testid={`restore-${version.id}`}>
              <RestoreIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      }
    >
      <ListItemText
        primary={
          <span>
            #{version.id} · {version.reason}
            {head && <Chip label="current" size="small" color="primary" variant="outlined" sx={{ ml: 1, height: 18 }} />}
          </span>
        }
        secondary={`${when(version.time_ns)}${version.parent != null ? ` · from #${version.parent}` : ""}${version.files.length ? ` · ${version.files.join(", ")}` : ""}`}
      />
    </ListItem>
  );
}

/** Save the running rig: the default overlay beside the loaded file, or the whole rig to a chosen path. */
function SaveBox({ allowPath }: { allowPath: boolean }) {
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
          {allowPath
            ? "With no path, only what changed since the runner started is written, to an overlay beside the rig file it was loaded from. A path writes the whole rig there instead."
            : "What changed since the runner started is written to an overlay beside the rig file it was loaded from. (Writing the whole rig to a path of your choosing needs the runner started with allow_save.)"}
        </Typography>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} alignItems={{ xs: "flex-start", sm: "center" }}>
          {allowPath && (
            <TextField
              size="small"
              label="path (blank: the overlay)"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="rig.yaml"
              sx={{ minWidth: 260 }}
              inputProps={{ "data-testid": "save-path" }}
            />
          )}
          {allowPath && (
            <FormControlLabel
              control={<Checkbox checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} disabled={!path.trim()} />}
              label="overwrite a loaded file"
            />
          )}
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

/** A line of text with a copy-to-clipboard button, in a monospace box. */
function CopyLine({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable (insecure context): nothing to do */
    }
  };
  return (
    <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1 }}>
      <Box
        component="pre"
        // A one-line command wraps at any width (a phone reads it whole and the copy button has it exact); a JSON block keeps its indentation and scrolls.
        sx={{ m: 0, p: 1, flex: 1, minWidth: 0, overflow: "auto", whiteSpace: value.includes("\n") ? "pre" : "pre-wrap", overflowWrap: "anywhere", fontFamily: "monospace", fontSize: "0.8125rem", lineHeight: 1.5, borderRadius: 1, bgcolor: "action.hover" }}
        className="fb-scroll-shadow-x"
      >
        {value}
      </Box>
      <Tooltip title={copied ? "copied" : "copy to clipboard"}>
        <IconButton size="small" aria-label="copy" onClick={() => void copy()} sx={{ mt: 0.25 }}>
          {copied ? <CheckIcon fontSize="small" color="success" /> : <ContentCopyIcon fontSize="small" />}
        </IconButton>
      </Tooltip>
    </Box>
  );
}

/** The three tiers `mcp.md` documents, narrowest first, with the server name the book's own examples use
 * where it gives one (`rig` for author, `rig-operate` for operate). */
const MCP_MODES: Array<{ mode: "read" | "author" | "operate"; label: string; server: string; note: string }> = [
  { mode: "read", label: "Read", server: "rig-read", note: "Every GET, plus check_program/check_rig: asking the rig questions." },
  { mode: "author", label: "Author", server: "rig", note: "Adds saving programs, dashboards and tunings to the store." },
  { mode: "operate", label: "Operate", server: "rig-operate", note: "Adds one tool per device command, demands, controllers, running programs, recording and a simulation's knobs: driving the rig." },
];

/**
 * `GET /mcp/{read,author,operate}`: this runner's MCP server, one tier per mode (book's mcp.md). Shows each
 * tier's absolute URL, the `claude mcp add` line for it, and a client config block. The browser never holds
 * the runner's token (a login is a cookie), so the block carries a placeholder for it when the runner has one.
 */
function ConnectModelCard() {
  const { info } = useAuth();
  const base = pageBase();
  const urls = MCP_MODES.map((m) => ({ ...m, url: `${base}/mcp/${m.mode}` }));
  const needsToken = Boolean(info && (info.token || info.password));
  const config = {
    mcpServers: Object.fromEntries(
      urls.map((m) => [m.server, { type: "http", url: m.url, ...(needsToken ? { headers: { Authorization: "Bearer <token>" } } : {}) }]),
    ),
  };
  return (
    <Paper className="c12" sx={{ p: 3 }}>
      <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 1.125 }}>
        Connect a model
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        This runner serves MCP over HTTP at three tiers, narrowest first: a client in read mode is never told
        a tool that moves anything exists.
      </Typography>
      <Stack spacing={2.5}>
        {urls.map((m) => (
          <Box key={m.mode}>
            <Typography variant="subtitle2">{m.label}</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 0.75 }}>
              {m.note}
            </Typography>
            <Stack spacing={0.75}>
              <CopyLine value={m.url} />
              <CopyLine value={`claude mcp add --transport http ${m.server} ${m.url}`} />
            </Stack>
          </Box>
        ))}
        <Box>
          <Typography variant="subtitle2">Client config</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 0.75 }}>
            {info?.token
              ? "Every server here needs the runner's bearer token (--token), since any of them can drive the rig: put it where <token> is."
              : info?.password
                ? "This runner has a password but no token, and a model cannot type one: start it with --token as well and put that where <token> is."
                : "This runner has no password and no token: it is open to anyone who can reach it, so the config below carries no headers."}
          </Typography>
          <CopyLine value={JSON.stringify(config, null, 2)} />
        </Box>
      </Stack>
    </Paper>
  );
}

/** Where this runner serves from and, when it allows it, the buttons to stop or restart it. */
function RunnerControls({ runner, busy, onAsk }: { runner: RunnerInfo; busy: boolean; onAsk(what: "shutdown" | "restart"): void }) {
  return (
    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
      <Typography variant="body2" color="text.secondary" title={runner.files.join("\n")}>
        {runner.host}:{runner.port}
        {runner.root_path ? runner.root_path : ""} · {runner.files.length} file{runner.files.length === 1 ? "" : "s"}
      </Typography>
      {runner.allow_shutdown && (
        <>
          <Button size="small" variant="outlined" startIcon={<RestartAltIcon />} disabled={busy} onClick={() => onAsk("restart")} data-testid="runner-restart">
            Restart
          </Button>
          <Button size="small" variant="outlined" color="error" startIcon={<PowerSettingsNewIcon />} disabled={busy} onClick={() => onAsk("shutdown")} data-testid="runner-shutdown">
            Shut down
          </Button>
        </>
      )}
    </Stack>
  );
}

/**
 * `#/rig`: the running rig as a file would show it, what has changed since
 * the runner started, its version history with a restore per row, and a box
 * to save it -- the overlay by default, or the whole rig to a path.
 */
export function RigPage() {
  const rig = useRig();
  const document = useRigDocument(5000);
  const changes = useRigChanges(5000);
  const versions = useRigVersions(50);
  const runner = useQuery(() => rig.runner().catch(() => undefined), [rig]);
  const [restoring, setRestoring] = useState<RigVersion | null>(null);
  const [power, setPower] = useState<"shutdown" | "restart" | null>(null);
  const powerAct = async () => {
    if (!power) return;
    setBusy(true);
    try {
      await (power === "shutdown" ? rig.shutdownRunner() : rig.restartRunner());
      setError(null);
      setPower(null);
    } catch (e) {
      setError(detail(e));
    } finally {
      setBusy(false);
    }
  };
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
      <SectionHead icon={PAGE_ICONS.rig} title="Rig" end={runner.data ? <RunnerControls runner={runner.data} busy={busy} onAsk={setPower} /> : undefined} />
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
          <SaveBox allowPath={runner.data?.allow_save ?? true} />
        </div>
        <ConnectModelCard />
      </div>
      <Confirm
        open={power !== null}
        title={power === "shutdown" ? "Shut the runner down?" : "Restart the runner?"}
        text={power === "shutdown" ? "The rig stops: polling, controllers and recording end, and this page loses its connection until a runner is started again." : "The runner stops and starts itself again with the same command: the rig is rebuilt from its files, controllers start in manual, and this page reconnects in a few seconds."}
        action={power === "shutdown" ? "Shut down" : "Restart"}
        busy={busy}
        onClose={() => setPower(null)}
        onConfirm={() => void powerAct()}
      />
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
