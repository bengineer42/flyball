import { useEffect, useRef, useState, type ChangeEvent, type ReactNode } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  IconButton,
  Link,
  LinearProgress,
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
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import CheckIcon from "@mui/icons-material/Check";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import DownloadIcon from "@mui/icons-material/Download";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import RefreshIcon from "@mui/icons-material/Refresh";
import SaveOutlinedIcon from "@mui/icons-material/SaveOutlined";
import StopIcon from "@mui/icons-material/Stop";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import { useQuery, useRig } from "@flyball/react";
import type { ProgramCheck, ProgramFormat, RigEvent } from "@flyball/client";
import { hashFor } from "../router.js";
import { Confirm } from "../Confirm.js";
import { NEW, stepOf, type Programmer } from "../model.js";
import { clickThrough, clickableSx } from "../cards.js";
import { normalisedOf, stepsSummary } from "../steps.js";
import { ProgramSteps } from "./ProgramSteps.js";
import { when } from "../time.js";
import { Crumbs } from "./Sources.js";

const FORMATS: ProgramFormat[] = ["yaml", "toml", "json"];
const TEMPLATE = 'name: ...\nsteps:\n  - wait: {message: "...", seconds: 5}\n';

const formatOf = (filename: string): ProgramFormat | null => {
  const ext = filename.toLowerCase().split(".").pop();
  return ext === "yaml" || ext === "yml" ? "yaml" : ext === "toml" ? "toml" : ext === "json" ? "json" : null;
};
const stem = (filename: string) => filename.replace(/\.[^.]+$/, "");
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** ok / error chip for a stored program's server-side check. */
function CheckChip({ check }: { check: { data: ProgramCheck | undefined; error: Error | undefined } }) {
  if (check.error) return <Chip label="check failed" color="default" variant="outlined" title={check.error.message} />;
  if (!check.data) return <Chip label="…" variant="outlined" />;
  return (
    <Tooltip title={check.data.ok ? "The rig accepts this program" : check.data.error ?? "invalid"}>
      <Chip label={check.data.ok ? "ok" : "error"} color={check.data.ok ? "success" : "error"} variant="outlined" />
    </Tooltip>
  );
}

/** A section heading in the Overview's style. */
export function Heading({ children, end }: { children: ReactNode; end?: ReactNode }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.75 }}>
      <Typography variant="h2" component="h2" color="text.secondary">
        {children}
      </Typography>
      {end && <Box sx={{ ml: "auto !important" }}>{end}</Box>}
    </Stack>
  );
}

/** What the programmer is doing, and the recent program events for `name` (all programs when omitted). */
export function ProgramStatus({ programmer, events, name, onInterrupt }: { programmer: Programmer; events: RigEvent[]; /** One program's page: its events only, and "Status" rather than "Programmer". */ name?: string; onInterrupt?(): void }) {
  const p = programmer.data;
  const recent = events
    .filter((e) => e.scope === "program" && (name === undefined || e.subject === name || e.subject.startsWith(`${name}[`)))
    .slice(-6)
    .reverse();
  const running = p?.running ?? false;
  const explanation = !p ? "…" : p.running ? `running: step ${stepOf(p)}${p.command ? ` · ${p.command}` : ""}` : "idle — nothing is running";
  return (
    <Paper sx={{ p: 1.5, display: "flex", flexDirection: "column", gap: 1 }}>
      <Heading
        end={
          running && onInterrupt ? (
            <Button variant="outlined" color="error" startIcon={<StopIcon />} onClick={onInterrupt}>
              Interrupt
            </Button>
          ) : undefined
        }
      >
        {name ? "Status" : "Programmer"}
        <Chip label={p ? (running ? "running" : "idle") : "…"} color={running ? "success" : "default"} sx={{ ml: 1, verticalAlign: "middle" }} />
      </Heading>
      <Typography>{explanation}</Typography>
      {p?.running && p.steps > 0 && <LinearProgress variant="determinate" value={(100 * Math.min(p.step + 1, p.steps)) / p.steps} />}
      <Box sx={{ mt: 0.5 }}>
        <Heading>{name ? "Recent events" : "Recent program events"}</Heading>
        {recent.length > 0 ? (
          <Table size="small" sx={{ "& td": { border: 0, py: 0.25, px: 0.5 } }}>
            <TableBody>
              {recent.map((e, i) => (
                <TableRow key={`${e.time_ns}-${i}`}>
                  <TableCell sx={{ color: "text.secondary", whiteSpace: "nowrap", width: "1%" }}>{new Date(e.time_ns / 1e6).toLocaleTimeString()}</TableCell>
                  <TableCell sx={{ width: "1%" }}>
                    <Chip label={e.kind} variant="outlined" color={e.level === "ERROR" ? "error" : e.level === "WARNING" ? "warning" : "default"} />
                  </TableCell>
                  <TableCell sx={{ color: "text.secondary", whiteSpace: "nowrap", width: "1%", fontFamily: "monospace" }}>{e.subject}</TableCell>
                  <TableCell>{e.message}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Typography variant="body2" color="text.secondary">
            {name ? `no events from ${name} yet` : "no program events yet"}
          </Typography>
        )}
      </Box>
    </Paper>
  );
}

/** One row of the library: the whole row opens the program; check and step summary are fetched lazily. */
function ProgramRow({ program: p, running, busy, onRun, onDelete }: { program: { name: string; id: number; format: string; label?: string | null; created_ns: number; notes?: unknown }; running: boolean; busy: boolean; onRun(): void; onDelete(): void }) {
  const rig = useRig();
  const check = useQuery(() => rig.checkStoredProgram(p.name), [rig, p.name, p.id]);
  const href = hashFor("programs", p.name);
  const normalised = normalisedOf(check.data);
  return (
    <TableRow hover sx={clickableSx} onClick={clickThrough(href)} data-program={p.name}>
      <TableCell>
        <Link href={href} underline="hover" fontWeight={500}>
          {p.name}
        </Link>
      </TableCell>
      <TableCell>
        <Chip label={p.format} variant="outlined" />
      </TableCell>
      <TableCell>
        <CheckChip check={check} />
      </TableCell>
      <TableCell sx={{ color: "text.secondary" }}>{normalised ? stepsSummary(normalised) : check.data && !check.data.ok ? check.data.error : "…"}</TableCell>
      <TableCell>{p.label ?? "—"}</TableCell>
      <TableCell sx={{ whiteSpace: "nowrap" }}>{when(p.created_ns)}</TableCell>
      <TableCell sx={{ color: "text.secondary" }}>{p.notes == null ? "—" : typeof p.notes === "string" ? p.notes : JSON.stringify(p.notes)}</TableCell>
      <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
        <Tooltip title={running ? "A program is running" : "Run"}>
          <span>
            <IconButton aria-label={`run ${p.name}`} disabled={running || busy} onClick={onRun}>
              <PlayArrowIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </TableCell>
      <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
        <Tooltip title="Delete">
          <span>
            <IconButton aria-label={`delete ${p.name}`} disabled={busy} onClick={onDelete}>
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </TableCell>
    </TableRow>
  );
}

export interface ProgramsProps {
  programmer: Programmer;
  events: RigEvent[];
  onOpen(name: string): void;
}

/** The program library: every stored program, with check, run, delete, upload and new. */
export function Programs({ programmer, events, onOpen }: ProgramsProps) {
  const rig = useRig();
  // Files in the daemon's programs directory are imported on arrival here and on "Rescan"; the list is fetched after.
  const [rescans, setRescans] = useState(0);
  const [imported, setImported] = useState<string[] | null>(null);
  const programs = useQuery(async () => {
    const fresh = await rig.importPrograms().catch(() => []); // no directory, or an older daemon: nothing to import
    setImported(fresh.map((p) => p.name));
    return rig.programs();
  }, [rig, rescans]);
  const [error, setError] = useState<string | null>(null);
  const [toRun, setToRun] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const running = programmer.data?.running ?? false;

  const act = async (op: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await op();
      setError(null);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const upload = async (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    const format = formatOf(f.name);
    if (!format) {
      setError(`${f.name}: not .yaml, .yml, .toml or .json`);
      return;
    }
    const body = await f.text();
    await act(() => rig.saveProgram(stem(f.name), format, body));
    programs.refresh();
  };

  return (
    <>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
        <input ref={file} type="file" accept=".yaml,.yml,.toml,.json" hidden onChange={(e) => void upload(e)} aria-label="upload program file" />
        <Button variant="outlined" startIcon={<UploadFileIcon />} onClick={() => file.current?.click()} disabled={busy}>
          Upload
        </Button>
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => onOpen(NEW)}>
          New
        </Button>
        <Tooltip title="Import any new files from the daemon's programs directory">
          <span>
            <Tooltip title="Re-read the program files on the rig's disk; changed files become new versions">
              <span>
                <Button variant="outlined" startIcon={<RefreshIcon />} onClick={() => setRescans((n) => n + 1)} disabled={programs.loading} data-testid="rescan">
                  Reload files
                </Button>
              </span>
            </Tooltip>
          </span>
        </Tooltip>
        {imported && imported.length > 0 && <Chip label={`imported ${imported.join(", ")}`} color="info" variant="outlined" onDelete={() => setImported(null)} />}
        <Box sx={{ flexGrow: 1 }} />
        <Chip label={running ? `running: ${programmer.data?.command ?? ""} step ${stepOf(programmer.data!)}` : "programmer idle"} color={running ? "success" : "default"} variant="outlined" />
      </Stack>
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 1.5 }}>
          {error}
        </Alert>
      )}
      {programs.error && <Alert severity="error">{programs.error.message}</Alert>}
      {!programs.data && !programs.error && <Typography color="text.secondary">loading…</Typography>}
      {programs.data && (
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>name</TableCell>
                <TableCell>format</TableCell>
                <TableCell>check</TableCell>
                <TableCell>steps</TableCell>
                <TableCell>label</TableCell>
                <TableCell>saved</TableCell>
                <TableCell>notes</TableCell>
                <TableCell padding="checkbox" />
                <TableCell padding="checkbox" />
              </TableRow>
            </TableHead>
            <TableBody>
              {programs.data.map((p) => (
                <ProgramRow key={p.name} program={p} running={running} busy={busy} onRun={() => setToRun(p.name)} onDelete={() => setToDelete(p.name)} />
              ))}
              {programs.data.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9} sx={{ color: "text.secondary" }}>
                    no programs stored
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      <Box sx={{ mt: 1.5 }}>
        <ProgramStatus programmer={programmer} events={events} onInterrupt={() => void act(() => rig.interruptProgram()).then(programmer.refresh)} />
      </Box>
      <Confirm
        open={toRun !== null}
        title={`Run ${toRun}?`}
        text="The programmer takes over the rig until the program finishes or is interrupted."
        action="Run"
        danger={false}
        busy={busy}
        onClose={() => setToRun(null)}
        onConfirm={() => {
          const n = toRun!;
          void act(() => rig.runStoredProgram(n)).then(() => {
            setToRun(null);
            programmer.refresh();
          });
        }}
      />
      <Confirm
        open={toDelete !== null}
        title={`Delete ${toDelete}?`}
        text="Every stored version of this program is removed."
        action="Delete"
        busy={busy}
        onClose={() => setToDelete(null)}
        onConfirm={() => {
          const n = toDelete!;
          void act(() => rig.deleteProgram(n)).then(() => {
            setToDelete(null);
            programs.refresh();
          });
        }}
      />
    </>
  );
}

export interface ProgramDetailProps {
  /** `NEW` for create mode. */
  name: string;
  programmer: Programmer;
  events: RigEvent[];
  onSaved(name: string): void;
  onDeleted(): void;
}

/** One program: editor, format, save, download, history, run/interrupt, live status. */
export function ProgramDetail({ name: routeName, programmer, events, onSaved, onDeleted }: ProgramDetailProps) {
  const rig = useRig();
  const creating = routeName === NEW;
  const stored = useQuery(async () => (creating ? null : rig.program(routeName)), [rig, routeName, creating]);
  const history = useQuery(async () => (creating ? [] : rig.programHistory(routeName)), [rig, routeName, creating]);
  // The schemas the steps view reads: the commands' arguments, and the file dialect for their descriptions.
  const commands = useQuery(() => rig.programCommandsSchema(), [rig]);
  const programSchema = useQuery(() => rig.programSchema(), [rig]);
  const [name, setName] = useState(creating ? "" : routeName);
  const [format, setFormat] = useState<ProgramFormat>("yaml");
  const [body, setBody] = useState(creating ? TEMPLATE : "");
  const [saved, setSaved] = useState(creating ? TEMPLATE : "");
  const [label, setLabel] = useState("");
  const [check, setCheck] = useState<ProgramCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [copied, setCopied] = useState(false);
  const copyText = async () => {
    try {
      await navigator.clipboard.writeText(body);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable (insecure context): nothing to do */
    }
  };
  const [convertTo, setConvertTo] = useState<ProgramFormat | null>(null);
  const [runVersion, setRunVersion] = useState<number | null | "latest">(null);
  const [downloadAnchor, setDownloadAnchor] = useState<HTMLElement | null>(null);
  const dirty = body !== saved;
  const running = programmer.data?.running ?? false;
  const normalised = normalisedOf(check ?? undefined);

  // Load the stored row into the editor once it arrives.
  useEffect(() => {
    if (stored.data) {
      setFormat(stored.data.format);
      setBody(stored.data.body);
      setSaved(stored.data.body);
      setLabel(stored.data.label ?? "");
    }
  }, [stored.data]);
  // The server-side check needs the stored row: show it for the saved text only.
  useEffect(() => {
    if (creating || !stored.data) return;
    let live = true;
    rig.checkStoredProgram(routeName).then((c) => live && setCheck(c), () => live && setCheck(null));
    return () => {
      live = false;
    };
  }, [rig, routeName, creating, stored.data]);

  const act = async (op: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await op();
      setError(null);
      return true;
    } catch (e) {
      setError(message(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    const n = name.trim();
    if (!n) {
      setError("A name is needed.");
      return;
    }
    const ok = await act(() => rig.saveProgram(n, format, body, label.trim() || undefined));
    if (!ok) return;
    setSaved(body);
    if (creating || n !== routeName) onSaved(n);
    else {
      stored.refresh();
      history.refresh();
    }
  };

  const convert = async (fmt: ProgramFormat) => {
    setConvertTo(null);
    if (creating || !stored.data) {
      setFormat(fmt); // nothing stored to convert from; the text is taken as-is in the new format
      return;
    }
    const ok = await act(async () => {
      const r = await fetch(rig.programDownloadUrl(routeName, fmt));
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const text = await r.text();
      setFormat(fmt);
      setBody(text);
      setSaved(text); // converted, not edited
    });
    void ok;
  };

  const run = async (version?: number) => {
    setRunVersion(null);
    const ok = await act(() => rig.runStoredProgram(routeName, version === undefined ? {} : { version }));
    if (ok) programmer.refresh();
  };

  if (!creating && stored.error) return <Alert severity="error">{stored.error.message}</Alert>;
  if (!creating && !stored.data) return <Typography color="text.secondary">loading…</Typography>;

  return (
    <>
      <Crumbs items={[{ label: "programs", href: hashFor("programs") }, { label: creating ? "new" : routeName }]} />
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 1.5 }}>
          {error}
        </Alert>
      )}
      <Paper sx={{ p: 1.5, mb: 1.5, display: "flex", flexDirection: "column", gap: 1.5 }}>
        <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
          {creating ? (
            <TextField label="name" value={name} onChange={(e) => setName(e.target.value)} inputProps={{ "aria-label": "program name" }} />
          ) : (
            <Typography fontWeight={600}>{routeName}</Typography>
          )}
          <ToggleButtonGroup
            exclusive
            size="small"
            value={format}
            aria-label="format"
            onChange={(_e, fmt: ProgramFormat | null) => {
              if (!fmt || fmt === format) return;
              if (dirty) setConvertTo(fmt);
              else void convert(fmt);
            }}
          >
            {FORMATS.map((f) => (
              <ToggleButton key={f} value={f} sx={{ py: 0.25 }}>
                {f}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
          {dirty && <Chip label="unsaved" color="warning" variant="outlined" />}
          <Box sx={{ flexGrow: 1 }} />
          {!creating && (
            <>
              <Button startIcon={<DownloadIcon />} onClick={(e) => setDownloadAnchor(e.currentTarget)} aria-label="download">
                Download
              </Button>
              <Menu open={Boolean(downloadAnchor)} anchorEl={downloadAnchor} onClose={() => setDownloadAnchor(null)}>
                {FORMATS.map((f) => (
                  <MenuItem key={f} component="a" href={rig.programDownloadUrl(routeName, f)} download={`${routeName}.${f}`} onClick={() => setDownloadAnchor(null)}>
                    as {f}
                  </MenuItem>
                ))}
              </Menu>
              <Button variant="contained" startIcon={<PlayArrowIcon />} disabled={running || busy} onClick={() => setRunVersion("latest")}>
                Run
              </Button>
              {running && (
                <Button variant="outlined" color="error" startIcon={<StopIcon />} onClick={() => void act(() => rig.interruptProgram()).then(programmer.refresh)}>
                  Interrupt
                </Button>
              )}
              <Tooltip title="Delete program">
                <span>
                  <IconButton aria-label={`delete ${routeName}`} onClick={() => setConfirmDelete(true)} disabled={busy}>
                    <DeleteOutlineIcon fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
            </>
          )}
        </Stack>
        <Box sx={{ display: "grid", gap: 1.5, gridTemplateColumns: { xs: "1fr", md: creating ? "1fr" : "minmax(0, 1fr) minmax(0, 1fr)" }, alignItems: "start" }}>
          {!creating && (
            <Box data-testid="steps" sx={{ minWidth: 0 }}>
              <Heading end={normalised ? <Chip label={stepsSummary(normalised)} variant="outlined" /> : undefined}>Steps</Heading>
              {check && !check.ok && (
                <Alert severity="error" sx={{ mb: 1 }}>
                  {check.error ?? "the rig rejects this program"}
                </Alert>
              )}
              {normalised ? (
                <ProgramSteps program={normalised} commands={commands.data} programSchema={programSchema.data} />
              ) : check && !check.ok ? (
                <Typography variant="body2" color="text.secondary">
                  The steps cannot be shown until the program passes the check; the text above is what is stored.
                </Typography>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  {check ? "no steps" : "checking…"}
                </Typography>
              )}
              {dirty && (
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                  showing the saved version; save to update
                </Typography>
              )}
            </Box>
          )}
          <Box sx={{ position: "relative" }}>
            <Heading
              end={
                <Tooltip title={copied ? "copied" : "copy to clipboard"}>
                  <IconButton size="small" aria-label="copy program text" onClick={() => void copyText()}>
                    {copied ? <CheckIcon fontSize="small" color="success" /> : <ContentCopyIcon fontSize="small" />}
                  </IconButton>
                </Tooltip>
              }
            >
              Text
            </Heading>
            <TextField
              multiline
              fullWidth
              minRows={16}
              maxRows={40}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              spellCheck={false}
              inputProps={{ "aria-label": "program body", style: { fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", fontSize: "0.85rem", lineHeight: 1.45, whiteSpace: "pre", overflowX: "auto" } }}
            />
          </Box>
        </Box>
        <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
          <TextField label="label" value={label} onChange={(e) => setLabel(e.target.value)} sx={{ minWidth: 200 }} />
          <Button variant="contained" startIcon={<SaveOutlinedIcon />} onClick={() => void save()} disabled={busy || (!creating && !dirty && label === (stored.data?.label ?? ""))}>
            Save
          </Button>
          {check && !dirty && (
            <Alert severity={check.ok ? "success" : "error"} sx={{ flexGrow: 1, py: 0 }}>
              {check.ok ? "the rig accepts this program" : check.error}
            </Alert>
          )}
          {dirty && (
            <Typography variant="body2" color="text.secondary">
              save to check against the rig
            </Typography>
          )}
        </Stack>
      </Paper>

      {!creating && (
        <Box sx={{ mb: 1.5 }}>
          <ProgramStatus programmer={programmer} events={events} name={routeName} onInterrupt={() => void act(() => rig.interruptProgram()).then(programmer.refresh)} />
        </Box>
      )}

      {!creating && (
        <TableContainer component={Paper} sx={{ p: 1.5 }}>
          <Heading>Versions</Heading>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>version</TableCell>
                <TableCell>label</TableCell>
                <TableCell>saved</TableCell>
                <TableCell>format</TableCell>
                <TableCell>sha256</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {(history.data ?? []).map((h) => (
                <TableRow key={h.id} hover selected={h.id === stored.data?.id}>
                  <TableCell>{h.id}</TableCell>
                  <TableCell>{h.label ?? "—"}</TableCell>
                  <TableCell>{when(h.created_ns)}</TableCell>
                  <TableCell>{h.format}</TableCell>
                  <TableCell sx={{ fontFamily: "monospace" }}>{h.sha256.slice(0, 12)}</TableCell>
                  <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                    <Button
                      onClick={() => {
                        setFormat(h.format);
                        setBody(h.body);
                      }}
                    >
                      load this version
                    </Button>
                    <Button disabled={running || busy} onClick={() => setRunVersion(h.id)}>
                      run this version
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {history.data && history.data.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} sx={{ color: "text.secondary" }}>
                    no history
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <Confirm
        open={convertTo !== null}
        title={`Convert to ${convertTo}?`}
        text="The editor has unsaved changes; converting replaces them with the stored version in the new format."
        action="Discard and convert"
        onClose={() => setConvertTo(null)}
        onConfirm={() => void convert(convertTo!)}
      />
      <Confirm
        open={runVersion !== null}
        title={runVersion === "latest" ? `Run ${routeName}?` : `Run ${routeName} version ${runVersion}?`}
        text={dirty ? "Unsaved edits are not run; the stored program is." : "The programmer takes over the rig until the program finishes or is interrupted."}
        action="Run"
        danger={false}
        busy={busy}
        onClose={() => setRunVersion(null)}
        onConfirm={() => void run(runVersion === "latest" ? undefined : (runVersion as number))}
      />
      <Confirm
        open={confirmDelete}
        title={`Delete ${routeName}?`}
        text="Every stored version of this program is removed."
        action="Delete"
        busy={busy}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => void act(() => rig.deleteProgram(routeName)).then((ok) => ok && onDeleted())}
      />
    </>
  );
}
