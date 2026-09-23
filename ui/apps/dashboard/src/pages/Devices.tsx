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
import { DevicePanel, DeviceSignals, SchemaForm, useCommands, useControllers, useDeviceRuns, useDeviceSchema, useRig, useRigDocument, useRigFileSchema, type QueryState } from "@flyball/react";
import { describeController, describeDevice, deviceOf, humanise, RigError, type DeviceOut, type InputOut, type JsonSchema, type NewDevice, type RigDocument } from "@flyball/client";
import { DeviceSummaryCard, SectionHead, StateBlock } from "../cards.js";
import { PAGE_ICONS } from "../icons.js";
import { hashFor, hrefFor } from "../router.js";
import { useRecordingExports } from "../model.js";
import { Confirm } from "../Confirm.js";
import { Crumbs } from "./Inputs.js";
import { PageBar } from "../PageBar.js";
import { deviceDrivers, linkKinds, withLinkSelect, TAG_HIDDEN } from "../rigForms.js";
import type { ChartSettings } from "../YScaleSelect.js";

const detail = (e: unknown) => (e instanceof RigError ? e.detail : e instanceof Error ? e.message : String(e));

/** One `{role, address}` row, freely typed: what a device declares as its inputs is not known before it is built. */
interface BoundRow {
  role: string;
  address: string;
}

function BoundRows({ rows, onChange }: { rows: BoundRow[]; onChange(rows: BoundRow[]): void }) {
  const set = (i: number, patch: Partial<BoundRow>) => onChange(rows.map((r, j) => (i === j ? { ...r, ...patch } : r)));
  return (
    <Stack spacing={1}>
      <Typography variant="body2" color="text.secondary">
        Bound inputs (optional): the role this device follows, and the signal address it reads.
      </Typography>
      {rows.map((row, i) => (
        <Stack key={i} direction="row" spacing={1} alignItems="center">
          <TextField size="small" label="role" value={row.role} onChange={(e) => set(i, { role: e.target.value })} sx={{ flex: 1 }} />
          <TextField size="small" label="address" value={row.address} onChange={(e) => set(i, { address: e.target.value })} placeholder="device.signal" sx={{ flex: 2 }} />
          <IconButton aria-label={`remove bound row ${i + 1}`} size="small" onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            <DeleteOutlineIcon fontSize="small" />
          </IconButton>
        </Stack>
      ))}
      <Button size="small" onClick={() => onChange([...rows, { role: "", address: "" }])} sx={{ alignSelf: "flex-start" }}>
        + bound input
      </Button>
    </Stack>
  );
}

/** Name, a driver from the rig schema, that driver's config as a form (`link` offered as a select of the rig's links), label, poll period and bound inputs. */
export function AddDeviceDialog({ open, schema, linkNames, onClose, onCreated }: { open: boolean; schema: JsonSchema | undefined; linkNames: string[]; onClose(): void; onCreated(device: DeviceOut): void }) {
  const rig = useRig();
  const [name, setName] = useState("");
  const [driver, setDriver] = useState("");
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [label, setLabel] = useState("");
  const [pollS, setPollS] = useState("");
  const [bound, setBound] = useState<BoundRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const drivers = useMemo(() => (schema ? deviceDrivers(schema) : []), [schema]);
  const chosen = drivers.find((d) => d.tag === driver);
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
    setBound([]);
    setError(null);
  };

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const body: NewDevice = { name: name.trim(), driver, config };
      if (label.trim()) body.label = label.trim();
      if (pollS.trim() && Number.isFinite(Number(pollS))) body.poll_s = Number(pollS);
      const boundEntries = bound.filter((r) => r.role.trim() && r.address.trim());
      if (boundEntries.length) body.bound = Object.fromEntries(boundEntries.map((r) => [r.role.trim(), r.address.trim()]));
      const device = await rig.addDevice(body);
      reset();
      onCreated(device);
    } catch (e) {
      setError(detail(e));
    } finally {
      setBusy(false);
    }
  };

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
                <MenuItem key={d.tag} value={d.tag} title={d.configSchema.description}>
                  {describeDevice(d.tag)}
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
          <BoundRows rows={bound} onChange={setBound} />
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
        <Button variant="contained" onClick={() => void create()} disabled={!canCreate} data-testid="create-device">
          Add
        </Button>
      </DialogActions>
    </Dialog>
  );
}

/** Name, a link kind from the rig schema, and that kind's config as a form. */
function AddLinkDialog({ open, schema, onClose, onCreated }: { open: boolean; schema: JsonSchema | undefined; onClose(): void; onCreated(link: { name: string; tag: string }): void }) {
  const rig = useRig();
  const [name, setName] = useState("");
  const [tag, setTag] = useState("");
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const kinds = useMemo(() => (schema ? linkKinds(schema) : []), [schema]);
  const chosen = kinds.find((k) => k.tag === tag);
  const formSchema = useMemo(() => (schema && chosen ? { ...chosen.configSchema, $defs: schema.$defs } : undefined), [schema, chosen]);

  const reset = () => {
    setName("");
    setTag("");
    setConfig({});
    setError(null);
  };

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const link = await rig.addLink({ ...config, name: name.trim(), tag });
      reset();
      onCreated({ name: link.name, tag: String(link.tag) });
    } catch (e) {
      setError(detail(e));
    } finally {
      setBusy(false);
    }
  };

  const canCreate = name.trim() !== "" && tag !== "" && !busy;

  return (
    <Dialog open={open} onClose={() => (busy ? undefined : (reset(), onClose()))} fullWidth maxWidth="sm">
      <DialogTitle>Add a link</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 0.5 }}>
          <TextField label="name" value={name} onChange={(e) => setName(e.target.value)} autoFocus fullWidth inputProps={{ "data-testid": "link-name" }} />
          <FormControl fullWidth>
            <InputLabel id="link-tag-label">kind</InputLabel>
            <Select labelId="link-tag-label" label="kind" value={tag} onChange={(e) => { setTag(e.target.value); setConfig({}); }} data-testid="link-tag">
              {kinds.map((k) => (
                <MenuItem key={k.tag} value={k.tag} title={k.configSchema.description}>
                  {k.tag}
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
              <SchemaForm schema={formSchema} value={config} onChange={setConfig} uiSchema={TAG_HIDDEN} form={MuiForm} idPrefix="new-link" />
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
        <Button variant="contained" onClick={() => void create()} disabled={!canCreate} data-testid="create-link">
          Add
        </Button>
      </DialogActions>
    </Dialog>
  );
}

/** The rig's links, name and tag, each with a remove button (409 while a device is built on it). */
export function LinksSection({ document, schema, showAdd = true }: { document: QueryState<RigDocument>; schema: QueryState<JsonSchema>; showAdd?: boolean }) {
  const rig = useRig();
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const links = Object.entries(document.data?.links ?? {});

  const remove = async () => {
    if (!removing) return;
    setBusy(true);
    try {
      await rig.removeLink(removing);
      setError(null);
      setRemoving(null);
      document.refresh();
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
          <Button size="small" startIcon={<AddIcon />} sx={{ ml: "auto" }} onClick={() => setAdding(true)} data-testid="add-link">
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
              label={`${name} · ${String(entry.tag)}`}
              variant="outlined"
              onDelete={() => setRemoving(name)}
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
        onCreated={() => {
          setAdding(false);
          document.refresh();
        }}
      />
      <Confirm
        open={removing !== null}
        title={`Remove link ${removing}?`}
        text="Refused while a device is still built on it."
        action="Remove"
        busy={busy}
        onClose={() => setRemoving(null)}
        onConfirm={() => void remove()}
      />
    </Paper>
  );
}

/** Every device the rig has, as cards: `#/devices` without a name. The simulation device (if any) stays on its own page. */
export function Devices({ devices }: { devices: DeviceOut[] }) {
  const runs = useDeviceRuns();
  const rig = useRig();
  const schema = useRigFileSchema();
  const document = useRigDocument();
  const [removing, setRemoving] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // `devices` is fetched once by the app, so a device this page just removed is
  // reflected here immediately rather than waiting for the app to refetch it.
  const [removed, setRemoved] = useState<Set<string>>(new Set());

  const shown = devices.filter((d) => d.kind !== "simulation" && !removed.has(d.name));

  const remove = async () => {
    if (!removing) return;
    setBusy(true);
    try {
      await rig.removeDevice(removing);
      setError(null);
      setRemoved((r) => new Set(r).add(removing));
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
                  <IconButton aria-label={`remove device ${d.name}`} size="small" onClick={() => setRemoving(d.name)} data-testid={`remove-${d.name}`}>
                    <DeleteOutlineIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              }
            />
          ))}
        </div>
      )}
      <Confirm
        open={removing !== null}
        title={`Remove device ${removing}?`}
        text="Takes it off the rig with everything that hung off it."
        action="Remove"
        busy={busy}
        onClose={() => setRemoving(null)}
        onConfirm={() => void remove()}
      />
    </>
  );
}

/** What the device follows, by role: "dry ← hum_sensors.dry.humidity", one per bound (or unbound) input. */
function InputsLine({ inputs }: { inputs: Record<string, InputOut> }) {
  const entries = Object.entries(inputs);
  if (entries.length === 0) return null;
  return (
    <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
      {entries.map(([role, input], i) => (
        <Fragment key={role}>
          {i > 0 && "  ·  "}
          {input.label || humanise(input.name)} ←{" "}
          {input.bound ? (
            <Link href={hrefFor({ kind: "signal", name: input.bound })} underline="hover" title={input.bound}>
              {input.bound}
            </Link>
          ) : (
            <span className="fb-muted">not bound</span>
          )}
        </Fragment>
      ))}
    </Typography>
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
  const device = devices.find((d) => d.name === name);
  const schema = useDeviceSchema(name);
  const commands = useCommands(name);
  const stored = useRecordingExports();
  const { controllers } = useControllers();
  const tags = useMemo(() => Object.entries(schema.data?.commands ?? {}).filter(([, c]) => !c.simulation).map(([tag]) => tag), [schema.data]);
  if (!device) return <Alert severity="warning">No device named {name}.</Alert>;
  const drives = Object.values(controllers).filter((c) => deviceOf(c.output_signal) === name);
  const regulates = Object.values(controllers).filter((c) => deviceOf(c.measured_signal) === name && deviceOf(c.output_signal) !== name);
  return (
    <>
      <PageBar>
        <Crumbs items={[{ label: "overview", href: hashFor("overview") }, { label: "devices", href: hashFor("devices") }, { label: device.label ?? name }]} />
      </PageBar>
      {schema.error && <Alert severity="error">{schema.error.message}</Alert>}
      <div className="grid">
        <div className="c12 xl6">
          {schema.data ? (
            <DevicePanel
              device={device}
              schema={schema.data}
              commands={tags}
              form={MuiForm}
              onRun={(tag, args) => commands.run(tag, args).catch(() => undefined)}
              onRestart={() => rig.restartDevice(name)}
              busy={commands.busy}
              results={commands.results}
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
