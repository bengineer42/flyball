import { useEffect, useMemo, useRef, useState, type ChangeEvent, type ReactNode } from "react";
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
  LinearProgress,
  Menu,
  MenuItem,
  Paper,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import CheckIcon from "@mui/icons-material/Check";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import DownloadIcon from "@mui/icons-material/Download";
import DriveFileRenameOutlineIcon from "@mui/icons-material/DriveFileRenameOutline";
import SaveAsIcon from "@mui/icons-material/SaveAs";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import RefreshIcon from "@mui/icons-material/Refresh";
import SaveOutlinedIcon from "@mui/icons-material/SaveOutlined";
import StopIcon from "@mui/icons-material/Stop";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import { useQuery, useRig, useRigSchema } from "@flyball/react";
import { RigError, type ProgramCheck, type ProgramFormat, type RigEvent } from "@flyball/client";
import { hashFor } from "../router.js";
import { Confirm } from "../Confirm.js";
import { NEW, stepOf, type Programmer } from "../model.js";
import { clickThrough, clickableSx, SectionHead, StateBlock } from "../cards.js";
import { PAGE_ICONS } from "../icons.js";
import { normalisedOf, stepsSummary } from "../steps.js";
import { asProgram, briefError, stepOfError, type DevicePicks, type ProgramTree } from "../programDoc.js";
import { dumpText, hasComments, parseText, SUPPORTED } from "../programText.js";
import { ProgramBuilder } from "./ProgramBuilder.js";
import { when } from "../time.js";
import { Crumbs } from "./Inputs.js";

const FORMATS: ProgramFormat[] = ["yaml", "toml", "json"];
const TEMPLATE = "steps: []\n";

const formatOf = (filename: string): ProgramFormat | null => {
  const ext = filename.toLowerCase().split(".").pop();
  return ext === "yaml" || ext === "yml" ? "yaml" : ext === "toml" ? "toml" : ext === "json" ? "json" : null;
};
const stem = (filename: string) => filename.replace(/\.[^.]+$/, "");
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** "step 3: controller 'x' is not on the rig", one line per warning, for a tooltip. */
const warningLines = (warnings: Record<string, string>) =>
  Object.entries(warnings)
    .map(([i, m]) => `step ${Number(i) + 1}: ${m}`)
    .join("\n");

/** A program's `notes`: a known `{ source }` note reads as "from &lt;file&gt;" (full path on hover), any
 * other object as `key: value` pairs, and a plain string as itself. */
/** Columns a phone has no room for: the name, its check, its steps and the run/delete buttons are what matters there. */
const wideOnly = { display: { xs: "none", sm: "table-cell" } } as const;

function NotesCell({ notes }: { notes: unknown }) {
  if (notes == null) return <>—</>;
  if (typeof notes === "string") return <>{notes}</>;
  if (typeof notes === "object") {
    const obj = notes as Record<string, unknown>;
    if (typeof obj.source === "string" && Object.keys(obj).length === 1) {
      const path = obj.source;
      const base = path.split("/").pop() || path;
      return <span title={path}>from {base}</span>;
    }
    return (
      <>
        {Object.entries(obj)
          .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
          .join(", ")}
      </>
    );
  }
  return <>{JSON.stringify(notes)}</>;
}

/** ok / warnings / error chip for a stored program's server-side check. */
function CheckChip({ check }: { check: { data: ProgramCheck | undefined; error: Error | undefined } }) {
  if (check.error) return <Chip label="check failed" color="default" variant="outlined" title={check.error.message} />;
  if (!check.data) return <Chip label="…" variant="outlined" />;
  const warnings = Object.keys(check.data.warnings ?? {}).length;
  return (
    <Tooltip title={check.data.ok ? (warnings ? warningLines(check.data.warnings) : "The rig accepts this program") : check.data.error ?? "invalid"}>
      <Chip label={check.data.ok ? (warnings ? `${warnings} warning${warnings === 1 ? "" : "s"}` : "ok") : "error"} color={check.data.ok ? (warnings ? "warning" : "success") : "error"} variant="outlined" />
    </Tooltip>
  );
}

/** A section heading in the Overview's style. */
export function Heading({ children, end }: { children: ReactNode; end?: ReactNode }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.125 }}>
      <Typography variant="h2" component="h2" color="text.secondary">
        {children}
      </Typography>
      {/* A long chip (a steps summary) ellipsises rather than pushing the page wider than a phone. */}
      {end && <Box sx={{ ml: "auto !important", minWidth: 0, "& .MuiChip-root": { maxWidth: "100%" } }}>{end}</Box>}
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
  // `failed` stays set (with the error that stopped it) until the next run clears it, even
  // once `running` goes false -- so a failure is still visible after the fact, not just for
  // the instant it happened.
  const failed = p?.failed ?? false;
  const explanation = !p
    ? "…"
    : p.running
      ? `running: step ${stepOf(p)}${p.command ? ` · ${p.command}` : ""}`
      : failed
        ? (p.error ?? "failed")
        : "idle — nothing is running";
  return (
    <Paper sx={{ p: 2.25, display: "flex", flexDirection: "column", gap: 1.5 }}>
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
        <Chip
          label={p ? (running ? "running" : failed ? "failed" : "idle") : "…"}
          color={running ? "success" : failed ? "error" : "default"}
          sx={{ ml: 1.5, verticalAlign: "middle" }}
        />
      </Heading>
      <Typography color={failed ? "error" : undefined}>{explanation}</Typography>
      {p?.running && p.steps > 0 && <LinearProgress variant="determinate" value={(100 * Math.min(p.step + 1, p.steps)) / p.steps} />}
      <Box sx={{ mt: 0.75 }}>
        <Heading>{name ? "Recent events" : "Recent program events"}</Heading>
        {recent.length > 0 ? (
          <Table size="small" sx={{ "& td": { border: 0, py: 0.375, px: 0.75 } }}>
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
function ProgramRow({ program: p, running, busy, onRun, onDelete }: { program: { name: string; id: number; format: ProgramFormat; body?: string; label?: string | null; created_ns: number; notes?: unknown }; running: boolean; busy: boolean; onRun(): void; onDelete(): void }) {
  const rig = useRig();
  const check = useQuery(() => rig.checkStoredProgram(p.name), [rig, p.name, p.id]);
  const href = hashFor("programs", p.name);
  const normalised = normalisedOf(check.data);
  // The description from the rig's normalised document, else read from the body (a program that fails the check still has one).
  const description = useMemo(() => {
    if (normalised?.description) return normalised.description;
    if (!p.body) return undefined;
    try {
      return asProgram(parseText(p.body, p.format))?.description;
    } catch {
      return undefined;
    }
  }, [normalised?.description, p.body, p.format]);
  const hasDescription = typeof description === "string" && description.length > 0;
  return (
    <>
      <TableRow hover sx={[clickableSx, { "& td": { borderBottom: hasDescription ? 0 : undefined } }]} onClick={clickThrough(href)} data-program={p.name}>
        <TableCell>
          <Link href={href} underline="hover" fontWeight={500}>
            {p.name}
          </Link>
        </TableCell>
        <TableCell sx={wideOnly}>
          <Chip label={p.format} variant="outlined" />
        </TableCell>
        <TableCell>
          <CheckChip check={check} />
        </TableCell>
        <TableCell sx={{ color: "text.secondary" }}>{normalised ? stepsSummary(normalised) : check.data && !check.data.ok ? check.data.error : "…"}</TableCell>
        <TableCell sx={wideOnly}>{p.label ?? "—"}</TableCell>
        <TableCell sx={[wideOnly, { whiteSpace: "nowrap" }]}>{when(p.created_ns)}</TableCell>
        <TableCell sx={[wideOnly, { color: "text.secondary" }]}>
          <NotesCell notes={p.notes} />
        </TableCell>
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
      {hasDescription && (
        <TableRow hover sx={clickableSx} onClick={clickThrough(href)} data-program={`${p.name}-description`}>
          <TableCell colSpan={9} sx={{ pt: 0 }}>
            <Typography
              variant="body2"
              color="text.secondary"
              title={description}
              data-testid="program-description"
              sx={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}
            >
              {description}
            </Typography>
          </TableCell>
        </TableRow>
      )}
    </>
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
  const failed = programmer.data?.failed ?? false;

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
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2.25 }}>
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
        <Tooltip title={failed ? (programmer.data?.error ?? "") : ""}>
          <Chip
            label={running ? `running: ${programmer.data?.command ?? ""} step ${stepOf(programmer.data!)}` : failed ? "failed — see below" : "programmer idle"}
            color={running ? "success" : failed ? "error" : "default"}
            variant="outlined"
          />
        </Tooltip>
      </Stack>
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2.25 }}>
          {error}
        </Alert>
      )}
      {programs.error && <Alert severity="error">{programs.error.message}</Alert>}
      <SectionHead icon={PAGE_ICONS.programs} title="Programs" count={programs.data?.length} />
      {!programs.data && !programs.error && <StateBlock state="loading" message="Loading programs…" />}
      {programs.data && programs.data.length === 0 && (
        <StateBlock state="empty" message="No programs stored yet. Upload one or start from a template." action={{ label: "New program", onClick: () => onOpen(NEW) }} />
      )}
      {programs.data && programs.data.length > 0 && (
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>name</TableCell>
                <TableCell sx={wideOnly}>format</TableCell>
                <TableCell>check</TableCell>
                <TableCell>steps</TableCell>
                <TableCell sx={wideOnly}>label</TableCell>
                <TableCell sx={wideOnly}>saved</TableCell>
                <TableCell sx={wideOnly}>notes</TableCell>
                <TableCell padding="checkbox" />
                <TableCell padding="checkbox" />
              </TableRow>
            </TableHead>
            <TableBody>
              {programs.data.map((p) => (
                <ProgramRow key={p.name} program={p} running={running} busy={busy} onRun={() => setToRun(p.name)} onDelete={() => setToDelete(p.name)} />
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      <Box sx={{ mt: 2.25 }}>
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

/** A dialog asking for a program name; `onSubmit` may reject with the reason shown inline (a 409 for a taken name). */
function NameDialog({ open, title, text, action, initial, busy, onClose, onSubmit }: { open: boolean; title: string; text: string; action: string; initial: string; busy: boolean; onClose(): void; onSubmit(name: string): Promise<string | null> }) {
  const [name, setName] = useState(initial);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setName(initial);
      setProblem(null);
    }
  }, [open, initial]);
  const submit = async () => {
    const n = name.trim();
    if (!n) return setProblem("A name is needed.");
    setProblem(await onSubmit(n));
  };
  return (
    <Dialog open={open} onClose={() => (busy ? undefined : onClose())} fullWidth maxWidth="xs">
      <DialogTitle>{title}</DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2.25 }}>
        <DialogContentText>{text}</DialogContentText>
        <TextField
          autoFocus
          label="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          error={Boolean(problem)}
          helperText={problem ?? undefined}
          inputProps={{ "aria-label": `${action} name` }}
          onKeyDown={(e) => {
            if (e.key === "Enter") void submit();
          }}
          fullWidth
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button variant="contained" onClick={() => void submit()} disabled={busy || !name.trim()}>
          {action}
        </Button>
      </DialogActions>
    </Dialog>
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

/** What the rig said about the tree last checked: ok, or the errors by step and overall. */
interface CheckState {
  ok: boolean;
  /** The whole-program message when the error names no step (or on top of the step ones). */
  error: string | null;
  stepErrors: Record<number, string>;
  /** What steps name that the rig lacks right now; the program is still accepted. */
  stepWarnings: Record<number, string>;
  normalised: unknown;
}

const PARSE_DEBOUNCE_MS = 400;
const CHECK_DEBOUNCE_MS = 500;
const EMPTY: ProgramTree = { steps: [] };

/** The text parsed as a program, or the reason it is not one. */
function parseProgram(text: string, format: ProgramFormat): { tree: ProgramTree; error: null } | { tree: null; error: string } {
  try {
    const value = parseText(text, format);
    const tree = asProgram(value);
    if (!tree) return { tree: null, error: value === null || value === undefined ? "the document is empty" : Array.isArray(value) || typeof value !== "object" ? "a program is a mapping with a 'steps' list" : "'steps' must be a list" };
    return { tree, error: null };
  } catch (e) {
    return { tree: null, error: message(e) };
  }
}

/**
 * One program: the step builder and the text, two views of one document tree,
 * each editable; format, save, download, history, run/interrupt, live status.
 */
export function ProgramDetail({ name: routeName, programmer, events, onSaved, onDeleted }: ProgramDetailProps) {
  const rig = useRig();
  const creating = routeName === NEW;
  const stored = useQuery(async () => (creating ? null : rig.program(routeName)), [rig, routeName, creating]);
  const history = useQuery(async () => (creating ? [] : rig.programHistory(routeName)), [rig, routeName, creating]);
  const programSchema = useQuery(() => rig.programSchema(), [rig]);
  const controllers = useQuery(async () => (await rig.controllers()).map((c) => c.name), [rig]);
  const rigSchema = useRigSchema();
  // Every device for the `command` and `set` steps: the ones with a command of their own, and the ones with a demand.
  const devices = useMemo<DevicePicks | undefined>(() => {
    if (!rigSchema.data) return undefined;
    const all = Object.values(rigSchema.data.devices);
    return {
      names: all.map((d) => d.name),
      commands: Object.fromEntries(all.filter((d) => Object.keys(d.commands).length > 0).map((d) => [d.name, d.commands])),
      demands: Object.fromEntries(
        all
          .map((d) => [d.name, Object.fromEntries(Object.entries(d.signals).filter(([, s]) => s.role === "demand"))] as const)
          .filter(([, signals]) => Object.keys(signals).length > 0),
      ),
    };
  }, [rigSchema.data]);
  const [format, setFormat] = useState<ProgramFormat>("yaml");
  // The tree is the truth; the text is what the user sees and saves. Either side may be edited: the other follows.
  const [tree, setTree] = useState<ProgramTree>(EMPTY);
  const [text, setText] = useState(creating ? TEMPLATE : "");
  const [revision, setRevision] = useState(0); // bumped when the tree was replaced from the text, so the builder re-reads it
  const [parseError, setParseError] = useState<string | null>(null);
  const [saved, setSaved] = useState(creating ? TEMPLATE : "");
  const [label, setLabel] = useState("");
  const [check, setCheck] = useState<CheckState | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [copied, setCopied] = useState(false);
  const [runVersion, setRunVersion] = useState<number | null | "latest">(null);
  const [downloadAnchor, setDownloadAnchor] = useState<HTMLElement | null>(null);
  const [saveAs, setSaveAs] = useState(false);
  const [rename, setRename] = useState(false);
  const [tab, setTab] = useState<"steps" | "text">("steps");
  const wide = useMediaQuery(useTheme().breakpoints.up("md"));
  const dirty = text !== saved;
  const running = programmer.data?.running ?? false;
  const loaded = useRef(false);

  const copyText = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable (insecure context): nothing to do */
    }
  };

  /** Text from outside (the stored row, a version, the template): parsed at once. */
  const loadText = (body: string, fmt: ProgramFormat) => {
    setFormat(fmt);
    setText(body);
    const parsed = parseProgram(body, fmt);
    setParseError(parsed.error);
    if (parsed.tree) setTree(parsed.tree);
    setRevision((n) => n + 1);
  };

  // Load the stored row into the editor once it arrives (and the template for a new one).
  useEffect(() => {
    if (creating) {
      if (!loaded.current) loadText(TEMPLATE, "yaml");
      loaded.current = true;
      return;
    }
    if (stored.data) {
      loadText(stored.data.body, stored.data.format);
      setSaved(stored.data.body);
      setLabel(stored.data.label ?? "");
      loaded.current = true;
    }
  }, [stored.data, creating]); // eslint-disable-line react-hooks/exhaustive-deps

  // Typing in the text: parse after a pause; a parse failure keeps the last good tree and the text as typed.
  const typed = useRef<string | null>(null);
  useEffect(() => {
    if (typed.current === null) return;
    const body = typed.current;
    const handle = window.setTimeout(() => {
      if (typed.current !== body) return;
      typed.current = null;
      const parsed = parseProgram(body, format);
      setParseError(parsed.error);
      if (parsed.tree) {
        setTree(parsed.tree);
        setRevision((n) => n + 1);
      }
    }, PARSE_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [text, format]);
  const onText = (body: string) => {
    typed.current = body;
    setText(body);
  };

  /** An edit in the builder: the tree changes and the text is regenerated in the current format. */
  const onTree = (next: ProgramTree) => {
    typed.current = null;
    setTree(next);
    setParseError(null);
    try {
      setText(dumpText(next, format));
    } catch (e) {
      setError(message(e));
    }
  };

  const switchFormat = (fmt: ProgramFormat) => {
    if (fmt === format || parseError) return;
    try {
      const body = dumpText(tree, fmt);
      typed.current = null;
      setFormat(fmt);
      setText(body);
    } catch (e) {
      setError(message(e));
    }
  };

  // The rig's check of the tree, after a pause; the offending step is found from the message, else by checking steps alone.
  useEffect(() => {
    if (!loaded.current) return;
    let live = true;
    setChecking(true);
    const handle = window.setTimeout(async () => {
      let result: CheckState;
      try {
        const { normalised, warnings } = await rig.checkProgram(tree);
        result = { ok: true, error: null, stepErrors: {}, stepWarnings: Object.fromEntries(Object.entries(warnings ?? {}).map(([i, m]) => [Number(i), m])), normalised };
      } catch (e) {
        const { index, message: why } = stepOfError(e);
        if (index !== null) result = { ok: false, error: null, stepErrors: { [index]: briefError(why) }, stepWarnings: {}, normalised: null };
        else {
          const stepErrors: Record<number, string> = {};
          if (tree.steps.length > 1 && !(e instanceof RigError && e.status !== 422)) {
            const alone = await Promise.all(
              tree.steps.map((step) =>
                rig.checkProgram({ steps: [step] }).then(
                  () => null,
                  (se: unknown) => briefError(stepOfError(se).message),
                ),
              ),
            );
            alone.forEach((m, i) => {
              if (m) stepErrors[i] = m;
            });
          } else if (tree.steps.length === 1) stepErrors[0] = briefError(why);
          result = { ok: false, error: Object.keys(stepErrors).length ? null : briefError(why), stepErrors, stepWarnings: {}, normalised: null };
        }
      }
      if (!live) return;
      setCheck(result);
      setChecking(false);
    }, CHECK_DEBOUNCE_MS);
    return () => {
      live = false;
      window.clearTimeout(handle);
    };
  }, [rig, tree, revision]);

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

  const programName = typeof tree.name === "string" ? tree.name.trim() : "";
  /** Create (new), or add a version to this program: the server never overwrites a version. */
  const save = async () => {
    const n = creating ? programName : routeName;
    if (!n) {
      setError("A name is needed: fill in the name field.");
      return;
    }
    if (parseError) {
      setError(`The text does not parse: ${parseError}`);
      return;
    }
    const ok = await act(() => rig.saveProgram(n, format, text, label.trim() || undefined));
    if (!ok) return;
    setSaved(text);
    if (creating) onSaved(n);
    else {
      stored.refresh();
      history.refresh();
    }
  };
  /** The current text under another name: a new program; this one is untouched. Resolves to the problem to show, or null. */
  const saveAsName = async (n: string): Promise<string | null> => {
    if (n === routeName) return "That is this program's name; Update adds a version to it.";
    setBusy(true);
    try {
      await rig.saveProgram(n, format, text, label.trim() || undefined);
      setSaveAs(false);
      onSaved(n);
      return null;
    } catch (e) {
      return message(e);
    } finally {
      setBusy(false);
    }
  };
  const renameTo = async (n: string): Promise<string | null> => {
    if (n === routeName) return null;
    setBusy(true);
    try {
      await rig.renameProgram(routeName, n);
      setRename(false);
      onSaved(n);
      return null;
    } catch (e) {
      return e instanceof RigError && e.status === 409 ? `A program called ${n} already exists.` : message(e);
    } finally {
      setBusy(false);
    }
  };
  const versions = history.data?.length;

  const run = async (version?: number) => {
    setRunVersion(null);
    const ok = await act(() => rig.runStoredProgram(routeName, version === undefined ? {} : { version }));
    if (ok) programmer.refresh();
  };

  if (!creating && stored.error) return <Alert severity="error">{stored.error.message}</Alert>;
  if (!creating && !stored.data) return <Typography color="text.secondary">loading…</Typography>;

  const commentsLost = hasComments(text, format);
  const normalised = check?.ok ? normalisedOf({ ok: true, error: null, normalised: check.normalised, warnings: {} }) : null;
  const warningCount = check ? Object.keys(check.stepWarnings).length : 0;
  const checkChip = checking ? (
    <Chip label="checking…" variant="outlined" />
  ) : check ? (
    <Tooltip title={check.ok ? (warningCount ? warningLines(check.stepWarnings) : "The rig accepts this program") : check.error ?? "a step is not accepted; see the card"}>
      <Chip
        label={check.ok ? `${normalised ? stepsSummary(normalised) : "ok"}${warningCount ? ` · ${warningCount} warning${warningCount === 1 ? "" : "s"}` : ""}` : `error${Object.keys(check.stepErrors).length ? ` in step ${Object.keys(check.stepErrors).map((i) => Number(i) + 1).join(", ")}` : ""}`}
        color={check.ok ? (warningCount ? "warning" : "success") : "error"}
        variant="outlined"
        data-testid="check-chip"
      />
    </Tooltip>
  ) : undefined;

  const stepsPane = (
    <Box data-testid="steps" sx={{ minWidth: 0 }}>
      <Heading end={checkChip}>Steps</Heading>
      {check && !check.ok && check.error && (
        <Alert severity="error" sx={{ mb: 1.5 }} data-testid="check-error">
          {check.error}
        </Alert>
      )}
      {parseError && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          showing the last text that parsed; fix the text to update
        </Typography>
      )}
      <ProgramBuilder tree={tree} onChange={onTree} programSchema={programSchema.data} controllers={controllers.data} devices={devices} stepErrors={check?.stepErrors ?? {}} stepWarnings={check?.stepWarnings ?? {}} revision={revision} nameEditable={creating} />
    </Box>
  );
  const textPane = (
    <Box sx={{ position: "relative", minWidth: 0 }}>
      <Heading
        end={
          <Stack direction="row" spacing={1} alignItems="center">
            <Tooltip title={parseError ? "Fix the text before switching format" : commentsLost ? "Switching format regenerates the text from the steps: comments are dropped" : "The text is regenerated in the chosen format"}>
              <ToggleButtonGroup exclusive size="small" value={format} aria-label="format" onChange={(_e, fmt: ProgramFormat | null) => fmt && switchFormat(fmt)} disabled={Boolean(parseError)}>
                {FORMATS.map((f) => (
                  <Tooltip key={f} title={SUPPORTED[f] ? "" : `${f} is not available in this build`}>
                    <span>
                      <ToggleButton value={f} sx={{ py: 0.375 }} disabled={!SUPPORTED[f] || Boolean(parseError)}>
                        {f}
                      </ToggleButton>
                    </span>
                  </Tooltip>
                ))}
              </ToggleButtonGroup>
            </Tooltip>
            <Tooltip title={copied ? "copied" : "copy to clipboard"}>
              <IconButton size="small" aria-label="copy program text" onClick={() => void copyText()}>
                {copied ? <CheckIcon fontSize="small" color="success" /> : <ContentCopyIcon fontSize="small" />}
              </IconButton>
            </Tooltip>
          </Stack>
        }
      >
        Text
      </Heading>
      <TextField
        multiline
        fullWidth
        minRows={16}
        maxRows={40}
        value={text}
        onChange={(e) => onText(e.target.value)}
        error={Boolean(parseError)}
        helperText={parseError ? `does not parse — ${parseError}` : commentsLost ? "editing in the steps view regenerates this text; its comments are dropped" : undefined}
        FormHelperTextProps={{ "data-testid": "parse-error" } as never}
        spellCheck={false}
        // `pre` keeps YAML's indentation; the textarea scrolls sideways for a long line rather than hiding its end.
        inputProps={{ "aria-label": "program body", style: { fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", fontSize: "0.85rem", lineHeight: 1.45, whiteSpace: "pre", overflowX: "auto", overflowY: "auto" } }}
        sx={{ "& .MuiInputBase-root": { alignItems: "stretch" }, "& textarea": { overflow: "auto !important" } }}
      />
    </Box>
  );

  return (
    <>
      <Crumbs items={[{ label: "programs", href: hashFor("programs") }, { label: creating ? "new" : routeName }]} />
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2.25 }}>
          {error}
        </Alert>
      )}
      <Paper sx={{ p: 2.25, mb: 2.25, display: "flex", flexDirection: "column", gap: 2.25 }}>
        <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
          <Typography fontWeight={600}>{creating ? programName || "new program" : routeName}</Typography>
          <Chip label={format} variant="outlined" />
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
              <Button startIcon={<DriveFileRenameOutlineIcon />} onClick={() => setRename(true)} disabled={busy}>
                Rename…
              </Button>
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
        {wide ? (
          <Box sx={{ display: "grid", gap: 2.25, gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", alignItems: "start" }}>
            {stepsPane}
            {textPane}
          </Box>
        ) : (
          <>
            <Tabs value={tab} onChange={(_e, v: "steps" | "text") => setTab(v)} sx={{ minHeight: 32 }}>
              <Tab value="steps" label="Steps" sx={{ minHeight: 32, py: 0 }} />
              <Tab value="text" label="Text" sx={{ minHeight: 32, py: 0 }} />
            </Tabs>
            {tab === "steps" ? stepsPane : textPane}
          </>
        )}
        <Stack direction="row" spacing={1.5} alignItems="flex-start" flexWrap="wrap" useFlexGap>
          <TextField label="label" value={label} onChange={(e) => setLabel(e.target.value)} sx={{ minWidth: 200 }} />
          {creating ? (
            <Button variant="contained" startIcon={<SaveOutlinedIcon />} onClick={() => void save()} disabled={busy || Boolean(parseError) || !programName} data-testid="create">
              Create {programName || "…"}
            </Button>
          ) : (
            <>
              <Tooltip title="Adds a version; earlier versions stay under Versions and can be loaded or run.">
                <span>
                  <Button variant="contained" startIcon={<SaveOutlinedIcon />} onClick={() => void save()} disabled={busy || Boolean(parseError) || (!dirty && label === (stored.data?.label ?? ""))} data-testid="update">
                    Update {routeName}
                    {versions !== undefined ? ` (version ${versions + 1})` : ""}
                  </Button>
                </span>
              </Tooltip>
              <Tooltip title={`Creates a new program; ${routeName} is unchanged.`}>
                <span>
                  <Button variant="outlined" startIcon={<SaveAsIcon />} onClick={() => setSaveAs(true)} disabled={busy || Boolean(parseError)} data-testid="save-as">
                    Save as…
                  </Button>
                </span>
              </Tooltip>
            </>
          )}
          {check && (
            <Alert severity={check.ok ? (warningCount ? "warning" : "success") : "error"} sx={{ flexGrow: 1, py: 0 }}>
              {check.ok
                ? warningCount
                  ? `accepted, but step ${Object.keys(check.stepWarnings).map((i) => Number(i) + 1).join(", ")} name${warningCount === 1 ? "s" : ""} something the rig does not have yet`
                  : "the rig accepts this program"
                : check.error ?? `step ${Object.keys(check.stepErrors).map((i) => Number(i) + 1).join(", ")}: ${Object.values(check.stepErrors)[0] ?? "not accepted"}`}
            </Alert>
          )}
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mt: -1 }}>
          {creating ? "Create stores the text as version 1 of a new program." : "Saving never overwrites: every save is a new version of this program."}
        </Typography>
      </Paper>
      <NameDialog open={saveAs} title="Save as a new program" text={`Creates a new program from the current text; ${routeName} is unchanged.`} action="Save as" initial={`${routeName}-copy`} busy={busy} onClose={() => setSaveAs(false)} onSubmit={saveAsName} />
      <NameDialog open={rename} title={`Rename ${routeName}`} text="Moves every version under the new name." action="Rename" initial={routeName} busy={busy} onClose={() => setRename(false)} onSubmit={renameTo} />

      {!creating && (
        <Box sx={{ mb: 2.25 }}>
          <ProgramStatus programmer={programmer} events={events} name={routeName} onInterrupt={() => void act(() => rig.interruptProgram()).then(programmer.refresh)} />
        </Box>
      )}

      {!creating && (
        <TableContainer component={Paper} sx={{ p: 2.25 }}>
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
                    <Button onClick={() => loadText(h.body, h.format)}>load this version</Button>
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
