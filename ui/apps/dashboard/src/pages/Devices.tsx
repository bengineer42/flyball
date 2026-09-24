import { Fragment, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  IconButton,
  InputLabel,
  Link,
  MenuItem,
  Paper,
  Select,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { Form as MuiForm } from "@rjsf/mui";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import { useAuth } from "../auth.js";
import { DevicePanel, DeviceSignals, SchemaForm, useCommands, useControllers, useDeviceRuns, useDeviceSchema, useQuery, useRig, useRigDocument, useRigFileSchema, type QueryState } from "@flyball/react";
import { describeController, describeDevice, describeQuality, deviceOf, RigError, withUnit, type DeviceOut, type InputOut, type JsonSchema, type NewDevice, type RigDocument, type RigEditOut, type ValueSourceOut } from "@flyball/client";
import { DeviceSummaryCard, SectionHead, StateBlock } from "../cards.js";
import { PAGE_ICONS } from "../icons.js";
import { hashFor, hrefFor } from "../router.js";
import { useRecordingExports } from "../model.js";
import { Confirm } from "../Confirm.js";
import { confirmLevel } from "../confirmLevels.js";
import { RESTART_TEXT, useRigEdit } from "../rigEdit.js";
import { Crumbs } from "./Readings.js";
import { PageBar } from "../PageBar.js";
import { deviceDrivers, linkKinds, withLinkSelect, TYPE_HIDDEN } from "../rigForms.js";
import type { ChartSettings } from "../YScaleSelect.js";

const detail = (e: unknown) => (e instanceof RigError ? e.detail : e instanceof Error ? e.message : String(e));

/** One `{input, address}` row, freely typed: what a device declares as its inputs is not known before it is built. `address` may be a number instead: a constant binding. */
interface InputRow {
  input: string;
  address: string;
}

/** A row's value as the rig file takes it: a number binds the input to that constant, anything else is an address. */
export const inputValue = (text: string): string | number => {
  const t = text.trim();
  const n = Number(t);
  return t !== "" && Number.isFinite(n) ? n : t;
};

function InputRows({ rows, onChange }: { rows: InputRow[]; onChange(rows: InputRow[]): void }) {
  const set = (i: number, patch: Partial<InputRow>) => onChange(rows.map((r, j) => (i === j ? { ...r, ...patch } : r)));
  return (
    <Stack spacing={1}>
      <Typography variant="body2" color="text.secondary">
        Inputs (optional): the input this device follows, by its name, and the signal address it reads, or a number to hold it at.
      </Typography>
      {rows.map((row, i) => (
        <Stack key={i} direction="row" spacing={1} alignItems="center">
          <TextField size="small" label="input" value={row.input} onChange={(e) => set(i, { input: e.target.value })} sx={{ flex: 1 }} />
          <TextField size="small" label="address or number" value={row.address} onChange={(e) => set(i, { address: e.target.value })} placeholder="device.signal or 36.5" sx={{ flex: 2 }} />
          <IconButton aria-label={`remove input row ${i + 1}`} size="small" onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            <DeleteOutlineIcon fontSize="small" />
          </IconButton>
        </Stack>
      ))}
      <Button size="small" onClick={() => onChange([...rows, { input: "", address: "" }])} sx={{ alignSelf: "flex-start" }}>
        + input
      </Button>
    </Stack>
  );
}

/** Name, a driver from the rig schema, that driver's config as a form (`link` offered as a select of the rig's links), label, poll period, write retry age and inputs. */
export function AddDeviceDialog({ open, schema, linkNames, onClose, onCreated }: { open: boolean; schema: JsonSchema | undefined; linkNames: string[]; onClose(): void; onCreated(edit: RigEditOut): void }) {
  const rig = useRig();
  const [name, setName] = useState("");
  const [driver, setDriver] = useState("");
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [label, setLabel] = useState("");
  const [pollS, setPollS] = useState("");
  const [retryS, setRetryS] = useState("");
  const [inputs, setInputs] = useState<InputRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const edits = useRigEdit();

  const drivers = useMemo(() => (schema ? deviceDrivers(schema) : []), [schema]);
  const chosen = drivers.find((d) => d.type === driver);
  const formSchema = useMemo(
    () => (schema && chosen ? { ...withLinkSelect(chosen.configSchema, schema, linkNames), $defs: schema.$defs } : undefined),
    [schema, chosen, linkNames],
  );

  const reset = () => {
    setName("");
    setDriver("");
    setConfig({});
    setLabel("");
    setPollS("");
    setRetryS("");
    setInputs([]);
    setError(null);
  };

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const body: NewDevice = { ...config, name: name.trim(), driver };
      if (label.trim()) body.label = label.trim();
      if (pollS.trim() && Number.isFinite(Number(pollS))) body.poll_s = Number(pollS);
      const inputEntries = inputs.filter((r) => r.input.trim() && r.address.trim());
      if (inputEntries.length) body.inputs = Object.fromEntries(inputEntries.map((r) => [r.input.trim(), inputValue(r.address)]));
      if (retryS.trim() && Number.isFinite(Number(retryS))) body.retry_max_age_s = Number(retryS);
      const edit = await edits.apply((options) => rig.addDevice(body, options));
      if (edit) {
        reset();
        onCreated(edit);
      }
    } catch (e) {
      setError(detail(e));
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };
  const ask = () => (confirmLevel("rig.edit.add") === 0 ? void create() : setConfirming(true));

  const canCreate = name.trim() !== "" && driver !== "" && !busy;

  return (
    <Dialog open={open} onClose={() => (busy ? undefined : (reset(), onClose()))} fullWidth maxWidth="sm">
      <DialogTitle>Add a device</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 0.5 }}>
          <TextField label="name" value={name} onChange={(e) => setName(e.target.value)} autoFocus fullWidth inputProps={{ "data-testid": "device-name" }} />
          <FormControl fullWidth>
            <InputLabel id="device-driver-label">driver</InputLabel>
            <Select labelId="device-driver-label" label="driver" value={driver} onChange={(e) => { setDriver(e.target.value); setConfig({}); }} data-testid="device-driver">
              {drivers.map((d) => (
                <MenuItem key={d.type} value={d.type} title={d.configSchema.description}>
                  {describeDevice(d.type)}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          {!schema && <Typography color="text.secondary">loading what this rig can build…</Typography>}
          {chosen?.configSchema.description && (
            <Typography variant="body2" color="text.secondary">
              {chosen.configSchema.description}
            </Typography>
          )}
          {formSchema && (
            <Box data-testid="device-config-form">
              <SchemaForm schema={formSchema} value={config} onChange={setConfig} form={MuiForm} idPrefix="new-device" />
            </Box>
          )}
          <Stack direction="row" spacing={1.5}>
            <TextField label="label (optional)" value={label} onChange={(e) => setLabel(e.target.value)} fullWidth />
            <TextField label="poll period (s, optional)" value={pollS} onChange={(e) => setPollS(e.target.value)} inputProps={{ inputMode: "decimal" }} sx={{ minWidth: 180 }} />
          </Stack>
          <TextField
            label="resend a failed write for (s, optional)"
            value={retryS}
            onChange={(e) => setRetryS(e.target.value)}
            helperText="how long a value kept from a failed write may wait to be sent again; blank: 60 s"
            inputProps={{ inputMode: "decimal", "data-testid": "device-retry-max-age" }}
          />
          <InputRows rows={inputs} onChange={setInputs} />
        </Stack>
        {error && (
          <Alert severity="error" onClose={() => setError(null)} sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={() => (busy ? undefined : (reset(), onClose()))} disabled={busy}>
          Cancel
        </Button>
        <Button variant="contained" onClick={ask} disabled={!canCreate} data-testid="create-device">
          Add
        </Button>
      </DialogActions>
      <Confirm
        open={confirming}
        title={`Add device ${name.trim()}?`}
        text={RESTART_TEXT}
        action="Add and restart"
        danger={false}
        level={confirmLevel("rig.edit.add")}
        phrase={name.trim()}
        busy={busy}
        onClose={() => setConfirming(false)}
        onConfirm={() => void create()}
      />
      {edits.dialog}
    </Dialog>
  );
}

/** Name, a link kind from the rig schema, and that kind's config as a form. */
function AddLinkDialog({ open, schema, onClose, onCreated }: { open: boolean; schema: JsonSchema | undefined; onClose(): void; onCreated(edit: RigEditOut): void }) {
  const rig = useRig();
  const [name, setName] = useState("");
  const [type, setType] = useState("");
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const edits = useRigEdit();

  const kinds = useMemo(() => (schema ? linkKinds(schema) : []), [schema]);
  const chosen = kinds.find((k) => k.type === type);
  const formSchema = useMemo(() => (schema && chosen ? { ...chosen.configSchema, $defs: schema.$defs } : undefined), [schema, chosen]);

  const reset = () => {
    setName("");
    setType("");
    setConfig({});
    setError(null);
  };

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = { ...config, name: name.trim(), type };
      const edit = await edits.apply((options) => rig.addLink(body, options));
      if (edit) {
        reset();
        onCreated(edit);
      }
    } catch (e) {
      setError(detail(e));
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };
  const ask = () => (confirmLevel("rig.edit.add") === 0 ? void create() : setConfirming(true));

  const canCreate = name.trim() !== "" && type !== "" && !busy;

  return (
    <Dialog open={open} onClose={() => (busy ? undefined : (reset(), onClose()))} fullWidth maxWidth="sm">
      <DialogTitle>Add a link</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 0.5 }}>
          <TextField label="name" value={name} onChange={(e) => setName(e.target.value)} autoFocus fullWidth inputProps={{ "data-testid": "link-name" }} />
          <FormControl fullWidth>
            <InputLabel id="link-type-label">kind</InputLabel>
            <Select labelId="link-type-label" label="kind" value={type} onChange={(e) => { setType(e.target.value); setConfig({}); }} data-testid="link-type">
              {kinds.map((k) => (
                <MenuItem key={k.type} value={k.type} title={k.configSchema.description}>
                  {k.type}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          {!schema && <Typography color="text.secondary">loading what this rig can build…</Typography>}
          {chosen?.configSchema.description && (
            <Typography variant="body2" color="text.secondary">
              {chosen.configSchema.description}
            </Typography>
          )}
          {formSchema && (
            <Box data-testid="link-config-form">
              <SchemaForm schema={formSchema} value={config} onChange={setConfig} uiSchema={TYPE_HIDDEN} form={MuiForm} idPrefix="new-link" />
            </Box>
          )}
        </Stack>
        {error && (
          <Alert severity="error" onClose={() => setError(null)} sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={() => (busy ? undefined : (reset(), onClose()))} disabled={busy}>
          Cancel
        </Button>
        <Button variant="contained" onClick={ask} disabled={!canCreate} data-testid="create-link">
          Add
        </Button>
      </DialogActions>
      <Confirm
        open={confirming}
        title={`Add link ${name.trim()}?`}
        text={RESTART_TEXT}
        action="Add and restart"
        danger={false}
        level={confirmLevel("rig.edit.add")}
        phrase={name.trim()}
        busy={busy}
        onClose={() => setConfirming(false)}
        onConfirm={() => void create()}
      />
      {edits.dialog}
    </Dialog>
  );
}

/** The rig's links, name and type, each with a remove button (409 while a device is built on it). */
export function LinksSection({ document, schema, showAdd = true }: { document: QueryState<RigDocument>; schema: QueryState<JsonSchema>; showAdd?: boolean }) {
  const { canOperate } = useAuth();
  const rig = useRig();
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const edits = useRigEdit();
  const links = Object.entries(document.data?.links ?? {});

  const remove = async () => {
    if (!removing) return;
    setBusy(true);
    try {
      const target = removing;
      await edits.apply((options) => rig.removeLink(target, options));
      setError(null);
      setRemoving(null);
    } catch (e) {
      setError(detail(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Paper sx={{ p: 3, mb: "16px" }}>
      <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="h2" component="h2" color="text.secondary">
          Links
        </Typography>
        {showAdd && (
          <Button size="small" startIcon={<AddIcon />} sx={{ ml: "auto" }} disabled={!canOperate} onClick={() => setAdding(true)} data-testid="add-link">
            Add link
          </Button>
        )}
      </Stack>
      {document.error && <Alert severity="error">{document.error.message}</Alert>}
      {!document.error && links.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          No link declared yet.
        </Typography>
      )}
      {links.length > 0 && (
        <Stack direction="row" flexWrap="wrap" useFlexGap spacing={1}>
          {links.map(([name, entry]) => (
            <Chip
              key={name}
              label={`${name} · ${String(entry.type)}`}
              variant="outlined"
              onDelete={canOperate ? () => setRemoving(name) : undefined}
              data-testid={`link-${name}`}
            />
          ))}
        </Stack>
      )}
      {error && (
        <Alert severity="error" sx={{ mt: 1.5 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <AddLinkDialog
        open={adding}
        schema={schema.data}
        onClose={() => setAdding(false)}
        onCreated={() => setAdding(false)}
      />
      <Confirm
        open={removing !== null}
        title={`Remove link ${removing}?`}
        text={`Refused while a device is still built on it. ${RESTART_TEXT}`}
        action="Remove and restart"
        level={confirmLevel("rig.edit.remove")}
        phrase={removing ?? undefined}
        busy={busy}
        onClose={() => setRemoving(null)}
        onConfirm={() => void remove()}
      />
      {edits.dialog}
    </Paper>
  );
}

/** Every device the rig has, as cards: `#/devices` without a name. The simulation device (if any) stays on its own page. */
export function Devices({ devices }: { devices: DeviceOut[] }) {
  const { canOperate } = useAuth();
  const runs = useDeviceRuns();
  const rig = useRig();
  const schema = useRigFileSchema();
  const document = useRigDocument();
  const [removing, setRemoving] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A removal restarts the rig, and the app reads it afresh once it is back: no local bookkeeping.
  const edits = useRigEdit();

  const shown = devices.filter((d) => d.kind !== "simulation");

  const remove = async () => {
    if (!removing) return;
    setBusy(true);
    try {
      const target = removing;
      await edits.apply((options) => rig.removeDevice(target, options));
      setError(null);
      setRemoving(null);
    } catch (e) {
      setError(detail(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <LinksSection document={document} schema={schema} showAdd={false} />
      <SectionHead icon={PAGE_ICONS.devices} title="Devices" count={shown.length} />
      {error && (
        <Alert severity="error" sx={{ mb: 1.5 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {shown.length === 0 ? (
        <StateBlock state="empty" message="No device declared. Add one in Config to see it here." />
      ) : (
        <div className="grid">
          {shown.map((d) => (
            <DeviceSummaryCard
              key={d.name}
              className="c3"
              device={d}
              run={runs[d.name]}
              actions={
                <Tooltip title="Remove this device from the rig">
                  <span>
                    <IconButton aria-label={`remove device ${d.name}`} size="small" disabled={!canOperate} onClick={() => setRemoving(d.name)} data-testid={`remove-${d.name}`}>
                      <DeleteOutlineIcon fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
              }
            />
          ))}
        </div>
      )}
      <Confirm
        open={removing !== null}
        title={`Remove device ${removing}?`}
        text={`Takes it off the rig file with everything that hung off it. ${RESTART_TEXT}`}
        action="Remove and restart"
        level={confirmLevel("rig.edit.remove")}
        phrase={removing ?? undefined}
        busy={busy}
        onClose={() => setRemoving(null)}
        onConfirm={() => void remove()}
      />
      {edits.dialog}
    </>
  );
}

/** A reading's age as the inputs line says it: `4 s`, `2 min`. */
const ageText = (s: number) => (s < 60 ? `${s < 10 ? s.toFixed(1) : Math.round(s)} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`);

/**
 * What the device follows, by role: "dry ← hum_sensors.dry.humidity", or
 * "dry = 36.5" for a constant, one per bound (or unbound) input; the
 * source's quality beside it when it is not ok (with its reason), and the
 * age of its newest reading.
 */
export function InputsLine({ inputs }: { inputs: Record<string, InputOut> }) {
  const entries = Object.entries(inputs);
  if (entries.length === 0) return null;
  return (
    <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }} data-testid="inputs-line">
      {entries.map(([role, input], i) => {
        const quality = describeQuality(input.quality, input.reason);
        return (
          <Fragment key={role}>
            {i > 0 && "  ·  "}
            <span data-testid={`input-${role}`}>
              {input.label}{" "}
              {input.constant !== undefined && input.constant !== null ? (
                <span title="a constant: the rig file binds this input to a number">= {withUnit(String(input.constant), input.unit)}</span>
              ) : input.bound ? (
                <>
                  ←{" "}
                  <Link href={hrefFor({ kind: "signal", name: input.bound })} underline="hover" title={input.bound}>
                    {input.bound}
                  </Link>
                </>
              ) : (
                <>
                  ← <span className="fb-muted">not bound</span>
                </>
              )}
              {quality && (
                <>
                  {" "}
                  <Typography component="span" variant="body2" color={input.quality === "pending" ? "text.secondary" : "warning.main"}>
                    ({quality})
                  </Typography>
                </>
              )}
              {input.age_s !== undefined && input.age_s !== null && input.bound && (
                <span title="since the rig received its source's newest reading with a value"> · {ageText(input.age_s)} ago</span>
              )}
            </span>
          </Fragment>
        );
      })}
    </Typography>
  );
}

/** Where a `values` device's value came from, as the page says it. */
export function describeValueSource(source: ValueSourceOut): string {
  if (source.origin === "rig_file") return "rig file";
  const who = source.actor?.principal ?? "someone";
  const when = source.written_utc_ns !== null ? ` at ${new Date(source.written_utc_ns / 1e6).toLocaleString()}` : "";
  return source.origin === "restored" ? `restored, written by ${who}${when}` : `written by ${who}${when}`;
}

/** A `driver: values` device's values: each one's source (the rig file, or who wrote it and when). */
function SourcesSection({ device }: { device: DeviceOut }) {
  const entries = Object.entries(device.sources ?? {});
  if (entries.length === 0) return null;
  return (
    <Paper className="c12" sx={{ p: 3 }} data-testid="value-sources">
      <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 0.75 }}>
        Where each value came from
      </Typography>
      {entries.map(([path, source]) => (
        <Typography key={path} variant="body2">
          <Link href={hrefFor({ kind: "signal", name: `${device.name}.${path}` })} underline="hover">
            {path}
          </Link>{" "}
          <Typography component="span" variant="body2" color="text.secondary" title={source.origin === "rig_file" ? undefined : `the rig file's initial: ${source.initial}`}>
            · {describeValueSource(source)}
          </Typography>
        </Typography>
      ))}
    </Paper>
  );
}

/** Who follows this device's signals: each signal, and the inputs bound to it (or to a namespace above it). */
function ConsumersSection({ device }: { device: DeviceOut }) {
  const entries = Object.entries(device.consumers ?? {});
  if (entries.length === 0) return null;
  return (
    <Paper className="c12" sx={{ p: 3 }} data-testid="consumers">
      <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 0.75 }}>
        Followed by
      </Typography>
      {entries.map(([path, users]) => (
        <Typography key={path} variant="body2">
          <Link href={hrefFor({ kind: "signal", name: `${device.name}.${path}` })} underline="hover">
            {path}
          </Link>{" "}
          <Typography component="span" variant="body2" color="text.secondary">
            →{" "}
            {users.map((u, i) => (
              <Fragment key={u}>
                {i > 0 && ", "}
                <Link href={hrefFor({ kind: "device", name: deviceOf(u) })} underline="hover" title={u}>
                  {u}
                </Link>
              </Fragment>
            ))}
          </Typography>
        </Typography>
      ))}
    </Paper>
  );
}

/**
 * One device (`#/devices/<name>`): the panel with its state, conditions,
 * run and commands (the simulation-only ones stay on the Simulation page),
 * then the signal tree with live values and write states, then the
 * controllers that drive or regulate its signals.
 */
export function DevicePage({ devices, name, windowS }: { devices: DeviceOut[]; name: string } & ChartSettings) {
  const rig = useRig();
  // The app's list is fetched once; this device is fetched again every few seconds for what only
  // `GET /api/devices/{name}` says: its inputs' quality and age, its values' sources, its consumers.
  const fresh = useQuery((signal) => rig.device(name, signal), [rig, name], { refreshMs: 5000 });
  const listed = devices.find((d) => d.name === name);
  const device = listed && fresh.data?.name === name ? { ...listed, inputs: fresh.data.inputs, sources: fresh.data.sources, consumers: fresh.data.consumers } : listed;
  const schema = useDeviceSchema(name);
  const commands = useCommands(name);
  const stored = useRecordingExports();
  const { controllers } = useControllers();
  const commandNames = useMemo(() => Object.entries(schema.data?.commands ?? {}).filter(([, c]) => !c.simulation).map(([command]) => command), [schema.data]);
  const { canOperate } = useAuth();
  if (!device) return <Alert severity="warning">No device named {name}.</Alert>;
  const drives = Object.values(controllers).filter((c) => deviceOf(c.output_signal) === name);
  const regulates = Object.values(controllers).filter((c) => deviceOf(c.measured_signal) === name && deviceOf(c.output_signal) !== name);
  return (
    <>
      <PageBar>
        <Crumbs items={[{ label: "readings", href: hashFor("readings") }, { label: device.label }]} />
      </PageBar>
      {schema.error && <Alert severity="error">{schema.error.message}</Alert>}
      <div className="grid">
        <div className="c12 xl6">
          {schema.data ? (
            <DevicePanel
              device={device}
              schema={schema.data}
              commands={commandNames}
              form={MuiForm}
              onRun={(command, args) => commands.run(command, args).catch(() => undefined)}
              onRestart={() => rig.restartDevice(name)}
              busy={commands.busy}
              results={commands.results}
              canOperate={canOperate}
            >
              <InputsLine inputs={device.inputs} />
            </DevicePanel>
          ) : (
            <Typography color="text.secondary">loading…</Typography>
          )}
        </div>
        <div className="c12 xl6">
          <DeviceSignals device={device} windowS={windowS} exportHref={(s) => stored.series(s.address)} />
        </div>
        <SourcesSection device={device} />
        <ConsumersSection device={device} />
        <Paper className="c12" sx={{ p: 3 }}>
          <Typography variant="h2" component="h2" color="text.secondary" sx={{ mb: 0.75 }}>
            Controllers
          </Typography>
          {drives.length + regulates.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              no controller drives or regulates a signal of this device
            </Typography>
          ) : (
            [...drives, ...regulates].map((c) => (
              <Typography key={c.name}>
                <Link href={hrefFor({ kind: "controller", name: c.name })} underline="hover">
                  {describeController(c)}
                </Link>{" "}
                <Typography component="span" variant="body2" color="text.secondary">
                  {c.label && `${c.name} · `}
                  {deviceOf(c.output_signal) === name ? `drives ${c.output_signal} from ${c.measured_signal}` : `regulates ${c.measured_signal} through ${c.output_signal}`} · {c.mode}
                </Typography>
              </Typography>
            ))
          )}
        </Paper>
      </div>
    </>
  );
}
