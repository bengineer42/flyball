import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  IconButton,
  List,
  ListItemButton,
  ListItemText,
  ListSubheader,
  MenuItem,
  Select,
  Stack,
  Step,
  StepContent,
  StepLabel,
  Stepper,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import StopIcon from "@mui/icons-material/Stop";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import { Form as MuiForm } from "@rjsf/mui";
import { ControllerPanel, SchemaForm, WritePanel, useControllers, useQuery, useRig, type ControllerTrace } from "@flyball/react";
import { signalTitle, signalsOf, type ControllerOut, type ControllerSchema, type DeviceOut, type FeedforwardConfig, type JsonSchema, type LawConfig, type ReferenceSpec, type SignalChoice, type StartSpec, type SignalOut } from "@flyball/client";
import { Confirm } from "../Confirm.js";
import { useRecordingExports } from "../model.js";
import { TuningPicker } from "../TuningPicker.js";
import { ChartControls, type ChartSettings } from "../YScaleSelect.js";
import { generatorChoices, generatorFormSchema, generatorUiSchema, prune } from "../generatorForm.js";
import { PageBar } from "../PageBar.js";
import { SectionHead, StateBlock } from "../cards.js";
import { PAGE_ICONS } from "../icons.js";
import { Crumbs } from "./Inputs.js";
import { hashFor } from "../router.js";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

type LawChoice = "stored" | "configure" | "none";

/** What the dialog sends: `law` is a tuning's name, a config, or null for no law; `feedforward` a config. */
interface Draft {
  /** The writable signal to drive: the controller's name. */
  target: SignalChoice | null;
  /** The publishing signal to regulate, by address. */
  source: string | null;
  lawChoice: LawChoice;
  tuning: string;
  config: LawConfig | null;
  /** Null until a target and source are chosen (the default depends on their units). */
  feedforward: FeedforwardConfig | null;
  isDefault: boolean;
}

const EMPTY_DRAFT: Draft = { target: null, source: null, lawChoice: "none", tuning: "", config: null, feedforward: null, isDefault: false };

/**
 * What the rig would pick when no feedforward is given: `setpoint` when the
 * target takes the source's unit (demand = setpoint), else `none` (the law
 * does all the work, in the target's unit).
 */
const defaultFeedforward = (target: SignalChoice | null, source: SignalChoice | null): FeedforwardConfig | null =>
  target && source ? { tag: target.unit === source.unit ? "setpoint" : "none" } : null;

/**
 * A tagged union's branches titled by their tag (`PI`, not `PIConfig`), so
 * the form's kind picker reads as the tags the rig speaks; the `tag` field
 * itself is then hidden by `TAG_HIDDEN`.
 */
function titledByTag(schema: JsonSchema): JsonSchema {
  const defs = Object.fromEntries(
    Object.entries(schema.$defs ?? {}).map(([name, def]) => {
      const tag = (def.properties?.tag as JsonSchema | undefined)?.const;
      return [name, typeof tag === "string" ? { ...def, title: tag } : def];
    }),
  );
  return { ...schema, $defs: defs };
}
const TAG_HIDDEN = { tag: { "ui:widget": "hidden" } };

/** The feedforward schema with the `setpoint` branch dropped: across differing units the rig refuses it (409). */
function withoutSetpoint(schema: JsonSchema): JsonSchema {
  const mapping = { ...(schema.discriminator?.mapping ?? {}) };
  const ref = mapping["setpoint"];
  delete mapping["setpoint"];
  return {
    ...schema,
    oneOf: (schema.oneOf ?? []).filter((b) => b.$ref !== ref && (b.properties?.tag as JsonSchema | undefined)?.const !== "setpoint"),
    ...(schema.discriminator ? { discriminator: { ...schema.discriminator, mapping } } : {}),
  };
}

/**
 * Make a controller in four steps: the writable signal to drive (the
 * target), a publishing signal to regulate (the source), the law, and the
 * feedforward that maps the setpoint into the target's unit (defaulted from
 * the units, as the rig would). Sources another controller already
 * regulates and targets already driven are disabled. Opened either from the
 * page bar (any target) or from an undriven signal's own card, which
 * preselects it (`initialTarget`) and jumps straight to the source step.
 */
const AddControllerDialog = memo(function AddControllerDialog({
  open,
  schema,
  devices,
  initialTarget,
  onClose,
  onCreated,
}: {
  open: boolean;
  schema: ControllerSchema | undefined;
  /** For the devices' display names in the pickers. */
  devices: DeviceOut[];
  /** Preselect this target (an undriven signal's own "Add controller" button) and open on the source step. */
  initialTarget?: SignalChoice | null;
  onClose(): void;
  onCreated(controller: ControllerOut): void;
}) {
  const deviceLabel = (name: string) => devices.find((d) => d.name === name)?.label ?? name;
  const choiceLabel = (c: SignalChoice) => c.label || c.address.slice(c.device.length + 1);
  const rig = useRig();
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  // Bumped to remount a SchemaForm with new initial values (it keeps its own form state after the first render).
  const [formKey, setFormKey] = useState(0);
  const [active, setActive] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const patch = (p: Partial<Draft>) => setDraft((d) => ({ ...d, ...p }));
  // Opening on a preselected target (from its own card) starts the stepper on the source step.
  const openedFor = useRef<string | null>(null);
  useEffect(() => {
    if (open && initialTarget && openedFor.current !== initialTarget.address) {
      openedFor.current = initialTarget.address;
      setDraft({ ...EMPTY_DRAFT, target: initialTarget });
      setActive(1);
    } else if (!open) {
      openedFor.current = null;
    }
  }, [open, initialTarget]);

  // Publishing signals by device; any unit may be regulated, the feedforward maps it into the target's.
  const byDevice = useMemo(() => {
    const groups: Record<string, SignalChoice[]> = {};
    for (const c of schema?.sources ?? []) (groups[c.device] ??= []).push(c);
    return groups;
  }, [schema]);

  const source = useMemo(() => schema?.sources.find((c) => c.address === draft.source) ?? null, [schema, draft.source]);
  const demandUnit = draft.target?.unit ?? null;
  const unitsAgree = source !== null && demandUnit === source.unit;
  // The feedforward the rig would pick on its own; set once both ends are known, and again whenever they change.
  const feedforward = draft.feedforward ?? defaultFeedforward(draft.target, source);
  const feedforwardSchema = useMemo(
    () => (schema?.feedforwards ? titledByTag(unitsAgree ? schema.feedforwards : withoutSetpoint(schema.feedforwards)) : undefined),
    [schema, unitsAgree],
  );

  const lawReady = draft.lawChoice === "none" || (draft.lawChoice === "stored" ? draft.tuning !== "" : draft.config !== null);
  const canCreate = Boolean(draft.target && draft.source && lawReady && feedforward);

  const create = async () => {
    if (!draft.target || !draft.source) return;
    setBusy(true);
    try {
      const law = draft.lawChoice === "none" ? null : draft.lawChoice === "stored" ? draft.tuning : draft.config;
      const controller = await rig.createController({ target: draft.target.address, source: draft.source, law, feedforward, default: draft.isDefault });
      setError(null);
      onCreated(controller);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const tunings = schema?.tunings ?? [];
  const lawSchema = useMemo(() => (schema?.laws ? titledByTag(schema.laws) : undefined), [schema]);

  return (
    <Dialog open={open} onClose={() => (busy ? undefined : onClose())} fullWidth maxWidth="sm">
      <DialogTitle>Add a controller</DialogTitle>
      <DialogContent>
        {!schema && <Typography color="text.secondary">loading what the rig can regulate…</Typography>}
        {schema && (
          <Stepper activeStep={active} orientation="vertical" nonLinear>
            <Step completed={draft.target !== null}>
              <StepLabel onClick={() => setActive(0)} sx={{ cursor: "pointer" }}>
                Target{draft.target && active !== 0 ? `: ${draft.target.address}` : ""}
              </StepLabel>
              <StepContent>
                {schema.targets.length === 0 && <Typography color="text.secondary">This rig has no writable signal to drive.</Typography>}
                <List dense disablePadding>
                  {schema.targets.map((t) => {
                    const taken = schema.driven[t.address];
                    return (
                      <ListItemButton
                        key={t.address}
                        selected={draft.target?.address === t.address}
                        disabled={Boolean(taken)}
                        onClick={() => {
                          patch({ target: t, feedforward: null });
                          setActive(1);
                        }}
                        data-target={t.address}
                      >
                        <ListItemText
                          primary={`${choiceLabel(t)} · ${t.unit}`}
                          secondary={`${t.address} · ${deviceLabel(t.device)}${taken ? ` · already driven by ${taken}` : t.limits ? ` · limits ${t.limits[0]} – ${t.limits[1]} ${t.unit}` : ""}`}
                        />
                      </ListItemButton>
                    );
                  })}
                </List>
              </StepContent>
            </Step>
            <Step completed={draft.source !== null}>
              <StepLabel onClick={() => draft.target && setActive(1)} sx={{ cursor: draft.target ? "pointer" : "default" }}>
                Source{draft.source && active !== 1 ? `: ${draft.source}` : ""}
              </StepLabel>
              <StepContent>
                {Object.keys(byDevice).length === 0 && <Typography color="text.secondary">{draft.target ? "No signal on this rig publishes." : "Pick a target first."}</Typography>}
                <List dense disablePadding>
                  {Object.entries(byDevice).map(([device, choices]) => (
                    <li key={device}>
                      <ul style={{ padding: 0 }}>
                        <ListSubheader disableSticky sx={{ lineHeight: "28px" }}>
                          {deviceLabel(device)}
                          {deviceLabel(device) !== device && (
                            <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1.5 }}>
                              {device}
                            </Typography>
                          )}
                        </ListSubheader>
                        {choices.map((c) => {
                          const by = schema.regulated[c.address];
                          return (
                            <ListItemButton
                              key={c.address}
                              selected={draft.source === c.address}
                              disabled={Boolean(by)}
                              onClick={() => {
                                patch({ source: c.address, feedforward: null });
                                setActive(2);
                              }}
                              data-source={c.address}
                            >
                              <ListItemText primary={`${choiceLabel(c)} · ${c.unit}`} secondary={`${c.address}${c.dimension ? ` · ${c.dimension}` : ""}${by ? ` · regulated by ${by}` : ""}`} />
                            </ListItemButton>
                          );
                        })}
                      </ul>
                    </li>
                  ))}
                </List>
              </StepContent>
            </Step>
            <Step completed={lawReady && draft.lawChoice !== "none"}>
              <StepLabel onClick={() => draft.source && setActive(2)} sx={{ cursor: draft.source ? "pointer" : "default" }}>
                Law
                {active !== 2 && (draft.lawChoice === "none" ? ": none (manual only)" : draft.lawChoice === "stored" ? `: tuning ${draft.tuning || "…"}` : draft.config ? `: ${String(draft.config.tag)}` : "")}
              </StepLabel>
              <StepContent>
                <Stack spacing={1.5}>
                  <ToggleButtonGroup
                    exclusive
                    size="small"
                    value={draft.lawChoice}
                    onChange={(_e, v: LawChoice | null) => v && patch({ lawChoice: v })}
                    aria-label="law"
                  >
                    {tunings.length > 0 && <ToggleButton value="stored">stored tuning</ToggleButton>}
                    <ToggleButton value="configure">configure</ToggleButton>
                    <ToggleButton value="none">none</ToggleButton>
                  </ToggleButtonGroup>
                  {draft.lawChoice === "stored" && (
                    <TuningPicker
                      tunings={tunings}
                      value={draft.tuning}
                      onChange={(tuning) => patch({ tuning })}
                      onConfigure={(config) => {
                        patch({ lawChoice: "configure", config });
                        setFormKey((k) => k + 1);
                      }}
                    />
                  )}
                  {draft.lawChoice === "configure" && lawSchema && (
                    <Box data-testid="law-form">
                      {draft.config && <Chip label={`law set: ${String(draft.config.tag)}`} color="success" variant="outlined" sx={{ mb: 1.5 }} />}
                      <SchemaForm
                        key={formKey}
                        schema={lawSchema}
                        value={draft.config ?? undefined}
                        uiSchema={TAG_HIDDEN}
                        form={MuiForm}
                        submitLabel="Use this law"
                        onSubmit={(data) => patch({ config: data as LawConfig })}
                      />
                    </Box>
                  )}
                  {draft.lawChoice === "none" && (
                    <Typography variant="body2" color="text.secondary">
                      No law: the controller starts in manual and cannot regulate until a tuning is given.
                    </Typography>
                  )}
                </Stack>
              </StepContent>
            </Step>
            <Step completed={feedforward !== null}>
              <StepLabel onClick={() => draft.source && setActive(3)} sx={{ cursor: draft.source ? "pointer" : "default" }}>
                Feedforward{feedforward && active !== 3 ? `: ${feedforward.tag}` : ""}
              </StepLabel>
              <StepContent>
                <Stack spacing={1.5}>
                  {!(draft.target && source) && <Typography color="text.secondary">Pick a target and a source first.</Typography>}
                  {draft.target && source && (
                    <Typography variant="body2" color="text.secondary" data-testid="feedforward-help">
                      {unitsAgree
                        ? `${draft.target.address} takes demands in ${demandUnit}, the same as ${source.address} — "setpoint" passes the setpoint straight through and the law corrects in ${demandUnit}.`
                        : `${draft.target.address} takes ${demandUnit}; ${source.address} is ${source.unit} — the feedforward maps one to the other, the law corrects in ${demandUnit}. "setpoint" is not offered: the units differ. "none" leaves all of it to the law.`}
                    </Typography>
                  )}
                  {feedforward && (
                    <Chip label={`feedforward set: ${feedforward.tag}${draft.feedforward === null ? " (the rig's default)" : ""}`} color="success" variant="outlined" sx={{ alignSelf: "flex-start" }} data-testid="feedforward-set" />
                  )}
                  {draft.target && source && feedforwardSchema && (
                    <Box data-testid="feedforward-form">
                      <SchemaForm
                        key={`${formKey}-${draft.target.address}-${draft.source}`}
                        schema={feedforwardSchema}
                        value={feedforward ?? undefined}
                        uiSchema={TAG_HIDDEN}
                        form={MuiForm}
                        submitLabel="Use this feedforward"
                        onSubmit={(data) => patch({ feedforward: data as FeedforwardConfig })}
                      />
                    </Box>
                  )}
                  <FormControlLabel
                    control={<Checkbox checked={draft.isDefault} onChange={(e) => patch({ isDefault: e.target.checked })} />}
                    label="default controller (the one programs address when they name none)"
                  />
                </Stack>
              </StepContent>
            </Step>
          </Stepper>
        )}
        {error && (
          <Alert severity="error" onClose={() => setError(null)} sx={{ mt: 2.25 }}>
            {error}
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button variant="contained" onClick={() => void create()} disabled={!canCreate || busy} data-testid="create-controller">
          Create
        </Button>
      </DialogActions>
    </Dialog>
  );
});

/** Where a generator starts from: the controller's setpoint, its reading, or a value typed here. */
type From = "setpoint" | "process" | "value";

/**
 * The target entry and its verb button, rendered inline in the faceplate's
 * Target row (DESIGN-SPEC §3.4). A kind picker offers a plain value or any
 * set-point generator the rig registers (`GET /api/controllers/schema` →
 * `generators`, never a list kept here). A plain value: one box, and
 * "Regulate" hands control to the law at it (a bumpless start) while stopped,
 * "Move target" changes it and leaves the law running while regulating. A
 * generator: its config as a form built from its own JSON schema
 * (`generatorFormSchema`), a "from" choice for where it starts (the setpoint,
 * the reading, or a value -- the request's `start`), and one "Start <kind>"
 * button that regulates while stopped and moves the reference while
 * regulating. Takes primitives and a stable callback so it does not re-render
 * on every tick (its text field is a MUI form control, which sets state in an
 * effect whenever it renders in development). Split from the stop/remove
 * control below so Tab reaches this field and its button before Stop, which
 * the faceplate places in the header regardless of where it sits in the DOM.
 */
const SetpointControl = memo(function SetpointControl({ name, unit, mode, tag, generators, onEvent }: { name: string; unit: string; mode: ControllerOut["mode"]; tag: string | null; generators: JsonSchema | undefined; onEvent(name: string, kind: "changed" | "removed"): void }) {
  const rig = useRig();
  const [setpoint, setSetpoint] = useState("");
  const [kind, setKind] = useState("value");
  const [from, setFrom] = useState<From | null>(null);
  const [fromValue, setFromValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasLaw = tag !== null && tag !== "open_loop";
  const regulating = mode === "regulating";
  const choices = useMemo(() => generatorChoices(generators), [generators]);
  const chosen = choices.find((c) => c.tag === kind);
  const formSchema = useMemo(() => (generators && chosen ? generatorFormSchema(generators, chosen.tag, unit) : undefined), [generators, chosen, unit]);
  const formUi = useMemo(() => (formSchema ? generatorUiSchema(formSchema) : undefined), [formSchema]);

  const act = async (op: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await op();
      setError(null);
      onEvent(name, "changed");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  const value = setpoint.trim() === "" ? null : Number(setpoint);
  const valid = value !== null && Number.isFinite(value);
  const send = (at: ReferenceSpec, start?: StartSpec | null) => act(() => (regulating ? rig.setReference(name, at, start) : rig.regulate(name, start == null ? { at } : { at, start })));

  // Where a generator starts: the server's own rule (the setpoint while regulating, else the
  // reading) unless the operator says otherwise, which is what is shown as chosen.
  const fromNow: From = from ?? (regulating ? "setpoint" : "process");
  const typedStart = fromValue.trim() === "" ? null : Number(fromValue);
  const start: StartSpec | null = fromNow === "value" ? (typedStart !== null && Number.isFinite(typedStart) ? typedStart : null) : fromNow;
  const startValid = fromNow !== "value" || start !== null;

  const startLabel = hasLaw ? "Aim here and start the law (bumpless)" : tag === null ? "No law: give the controller a tuning first" : "Open loop: no law to regulate with";
  const box = { "& .MuiInputBase-input": { py: 0.75 } } as const;
  const canStart = regulating || hasLaw;

  return (
    <Stack component="span" direction="row" spacing={0.75} alignItems="center" useFlexGap sx={{ display: "inline-flex", flexWrap: "wrap", minWidth: 0 }}>
      {choices.length > 0 && (
        <FormControl size="small" sx={{ flexShrink: 0 }}>
          <Select value={chosen ? chosen.tag : "value"} onChange={(e) => setKind(e.target.value)} inputProps={{ "aria-label": `how to set the target of ${name}` }} data-testid={`kind-${name}`} sx={{ fontSize: "0.85rem", "& .MuiSelect-select": { py: 0.75 } }}>
            <MenuItem value="value">value</MenuItem>
            {choices.map((c) => (
              <MenuItem key={c.tag} value={c.tag}>
                {c.label.toLowerCase()}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      )}
      {!chosen && (
        <>
          <TextField
            type="number"
            label="target"
            value={setpoint}
            onChange={(e) => setSetpoint(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && valid && !busy && canStart) void send(value!);
            }}
            inputProps={{ "aria-label": `target ${name}`, step: "any", "data-testid": `regulate-at-${name}`, style: { width: "4.5em" } }}
            InputProps={{ endAdornment: <span style={{ fontSize: "0.8rem", opacity: 0.7 }}>{unit}</span> }}
            sx={{ flexShrink: 0, ...box }}
          />
          {regulating ? (
            <Tooltip title="Change the target; the law keeps running as it is">
              <span>
                <Button variant="contained" size="small" disabled={!valid || busy} onClick={() => void send(value!)} data-testid={`set-reference-${name}`} sx={{ flexShrink: 0, whiteSpace: "nowrap" }}>
                  Move target
                </Button>
              </span>
            </Tooltip>
          ) : (
            <Tooltip title={startLabel}>
              <span>
                <Button variant="contained" size="small" startIcon={<PlayArrowIcon />} disabled={!hasLaw || !valid || busy} onClick={() => void send(value!)} data-testid={`regulate-${name}`} sx={{ flexShrink: 0, whiteSpace: "nowrap" }}>
                  Regulate
                </Button>
              </span>
            </Tooltip>
          )}
        </>
      )}
      {chosen && formSchema && (
        <>
          <FormControl size="small" sx={{ flexShrink: 0 }}>
            <Select value={fromNow} onChange={(e) => setFrom(e.target.value as From)} inputProps={{ "aria-label": `where the ${chosen.label.toLowerCase()} on ${name} starts` }} data-testid={`from-${name}`} sx={{ fontSize: "0.85rem", "& .MuiSelect-select": { py: 0.75 } }}>
              <MenuItem value="setpoint">from setpoint</MenuItem>
              <MenuItem value="process">from reading</MenuItem>
              <MenuItem value="value">from a value</MenuItem>
            </Select>
          </FormControl>
          {fromNow === "value" && (
            <TextField
              type="number"
              label="start"
              value={fromValue}
              onChange={(e) => setFromValue(e.target.value)}
              inputProps={{ "aria-label": `start value for ${name}`, step: "any", "data-testid": `start-${name}`, style: { width: "4.5em" } }}
              InputProps={{ endAdornment: <span style={{ fontSize: "0.8rem", opacity: 0.7 }}>{unit}</span> }}
              sx={{ flexShrink: 0, ...box }}
            />
          )}
          {/* The generator's own fields, from its schema; the whole row's width, under the pickers. */}
          <Tooltip title={!canStart ? startLabel : regulating ? `${chosen.verb}; the law keeps running as it is` : `${chosen.verb} and hand control to the law (bumpless)`}>
            <Box className="fb-generator" data-testid={`generator-${name}`} sx={{ width: "100%", minWidth: 0 }}>
              <SchemaForm
                key={`${name}-${chosen.tag}`}
                schema={formSchema}
                uiSchema={formUi}
                form={MuiForm}
                idPrefix={`gen-${name.replace(/\W/g, "_")}`}
                submitLabel={chosen.verb}
                disabled={busy || !canStart || !startValid}
                onSubmit={(data) => {
                  const spec = prune(data) ?? {};
                  void send({ ...spec, tag: chosen.tag }, start);
                }}
              />
            </Box>
          </Tooltip>
        </>
      )}
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ py: 0, width: "100%" }}>
          {error}
        </Alert>
      )}
    </Stack>
  );
});

/**
 * Stop and remove, rendered in the faceplate's header (DESIGN-SPEC §3.4)
 * even though the panel places them after the Target row in the DOM, so Tab
 * reaches target -> Move -> Stop in that order.
 */
const StopControl = memo(function StopControl({ name, mode, onEvent }: { name: string; mode: ControllerOut["mode"]; onEvent(name: string, kind: "changed" | "removed"): void }) {
  const rig = useRig();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const regulating = mode === "regulating";

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

  return (
    <Stack component="span" direction="row" spacing={0.5} alignItems="center" sx={{ display: "inline-flex" }}>
      <Tooltip title="Stop regulating: the target holds its last demand and takes demands directly">
        <span>
          <Button
            variant="outlined"
            color="error"
            startIcon={<StopIcon />}
            disabled={mode === "manual" || busy}
            onClick={() => void act(() => rig.manual(name)).then((ok) => ok && onEvent(name, "changed"))}
            data-testid={`manual-${name}`}
          >
            Stop
          </Button>
        </span>
      </Tooltip>
      <Tooltip title="Remove the controller from the rig">
        <span>
          <IconButton aria-label={`remove controller ${name}`} disabled={busy} onClick={() => setConfirmRemove(true)}>
            <DeleteOutlineIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
      <Confirm
        open={confirmRemove}
        title={regulating ? `${name} is regulating — stop it and remove?` : `Remove controller ${name}?`}
        text={
          regulating
            ? `Removing ${name} stops it first: the target keeps its last demand and takes demands directly. Its source is then free for another controller.`
            : `Its source is then free for another controller; the target keeps its last demand.`
        }
        action={regulating ? "Stop and remove" : "Remove"}
        onClose={() => setConfirmRemove(false)}
        onConfirm={() => {
          void act(() => rig.detachController(name)).then((ok) => {
            setConfirmRemove(false);
            if (ok) onEvent(name, "removed");
          });
        }}
      />
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ py: 0 }}>
          {error}
        </Alert>
      )}
    </Stack>
  );
});

/**
 * One controller's faceplate: `ControllerPanel` with the source and target
 * signals it needs for units, bands and limits (the panel subscribes to the
 * store itself for PV, OP and the source device's run). No direct-demand
 * panel below it: the rig refuses a manual demand on a signal with a
 * controller attached, in any mode -- detach the controller to drive the
 * signal by hand, and the card becomes the write panel.
 */
const Faceplate = memo(function Faceplate({
  controller,
  source,
  target,
  history,
  windowS,
  yScale,
  every,
  exportHref,
  controls,
  headerControls,
}: {
  controller: ControllerOut;
  source: SignalOut;
  target: SignalOut | undefined;
  history: ControllerTrace | undefined;
  windowS?: number;
  yScale?: ChartSettings["yScale"];
  every?: number;
  exportHref?: string;
  controls?: ReactNode;
  headerControls?: ReactNode;
}) {
  return (
    <ControllerPanel
      controller={controller}
      source={source}
      target={target}
      history={history}
      windowS={windowS}
      yScale={yScale}
      every={every}
      exportHref={exportHref}
      trends
      detail
      controls={controls}
      headerControls={headerControls}
    />
  );
});

export interface ControllersProps extends ChartSettings {
  devices: DeviceOut[];
  /** One controller only (`#/controllers/{address}`): the writable signal of that address. */
  name?: string | null;
}

/**
 * One card per writable signal (DESIGN-SPEC §3.4/§3.5, merged): with a
 * controller (a controller is named by the signal it drives) the card is
 * the faceplate, the signal's direct-demand panel below it; without one it
 * is the write panel alone, plus a primary "Add controller" button that
 * opens the stepper with this target preselected. Live from
 * `/ws/controllers` and `/ws/writes`, with a dialog to add a controller to
 * any writable signal.
 */
export function Controllers({ devices, name = null, ...charts }: ControllersProps) {
  const { windowS, yScale, every } = charts;
  const rig = useRig();
  const stored = useRecordingExports();
  const { controllers, history, status } = useControllers(3600, every);
  const signals = useMemo(() => new Map(devices.flatMap((d) => signalsOf(d.signals)).map((s) => [s.address, s])), [devices]);
  // A controller's target is a demand: settable, with a readback that updates.
  const targets = useMemo(() => [...signals.values()].filter((s) => s.role === "demand"), [signals]);
  const controllerSchema = useQuery(() => rig.controllerSchema(), [rig]);
  const [adding, setAdding] = useState(false);
  const [addingFor, setAddingFor] = useState<SignalChoice | null>(null);
  // The stream never says a controller is gone: hide one we detached until the stream sends a new object for that name (re-created).
  const [removed, setRemoved] = useState<Record<string, ControllerOut>>({});
  const [created, setCreated] = useState<Record<string, ControllerOut>>({});

  const all = { ...created, ...controllers };
  // A controller is named by the signal it drives, so one filter picks out the single card for a detail route.
  const shown = name === null ? targets : targets.filter((t) => t.address === name);
  const controllerOf = (address: string): ControllerOut | undefined => {
    const c = all[address];
    return c && removed[c.name] !== c ? c : undefined;
  };
  // What the panels' controls report back; one stable callback so the controls need not re-render per tick.
  const latest = useRef(all);
  latest.current = all;
  const onEvent = useCallback(
    (controllerName: string, kind: "changed" | "removed") => {
      if (kind === "removed") {
        const gone = latest.current[controllerName];
        if (gone) setRemoved((r) => ({ ...r, [controllerName]: gone }));
        setCreated((c) => {
          const { [controllerName]: _gone, ...rest } = c;
          void _gone;
          return rest;
        });
      }
      controllerSchema.refresh();
    },
    [controllerSchema.refresh],
  );

  const openAdd = useCallback((target?: SignalChoice) => {
    setAddingFor(target ?? null);
    setAdding(true);
  }, []);

  const only = shown.length === 1 ? controllerOf(shown[0]!.address) : undefined;
  const driven = shown.flatMap((target) => {
    const c = controllerOf(target.address);
    const source = c ? signals.get(c.source) : undefined;
    return c && source ? [{ target, c, source }] : [];
  });
  const undriven = shown.filter((target) => !driven.some((d) => d.target === target));
  const toolbar = (
    <PageBar end={<ChartControls {...charts} unit={only ? signals.get(only.source)?.unit : undefined} />}>
      {name === null ? (
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => openAdd()} data-testid="add-controller">
          Add controller
        </Button>
      ) : (
        <Crumbs items={[{ label: "controllers", href: hashFor("controllers") }, { label: name }]} />
      )}
      {controllerSchema.error && <Typography color="error">{controllerSchema.error.message}</Typography>}
    </PageBar>
  );
  const closeDialog = useCallback(() => setAdding(false), []);
  const onCreated = useCallback(
    (controller: ControllerOut) => {
      setAdding(false);
      setCreated((c) => ({ ...c, [controller.name]: controller }));
      setRemoved((r) => {
        const { [controller.name]: _gone, ...rest } = r;
        void _gone;
        return rest;
      });
      controllerSchema.refresh();
    },
    [controllerSchema.refresh],
  );
  const dialog = <AddControllerDialog open={adding} schema={controllerSchema.data} devices={devices} initialTarget={addingFor} onClose={closeDialog} onCreated={onCreated} />;

  if (name !== null && shown.length === 0)
    return status === "connecting" ? <Typography color="text.secondary">loading…</Typography> : <Alert severity="warning">No writable signal at {name}.</Alert>;
  return (
    <>
      {toolbar}
      {shown.length === 0 &&
        (status === "connecting" ? (
          <StateBlock state="loading" message="Loading controllers…" />
        ) : (
          <StateBlock state="empty" message="No writable signal on this rig: nothing to control." />
        ))}
      {/* Controllers first, then the demands nothing drives yet: a person looking for a loop should not read past pumps. */}
      {name === null && shown.length > 0 && <SectionHead icon={PAGE_ICONS.controllers} title="Controllers" count={driven.length} />}
      {name === null && driven.length === 0 && shown.length > 0 && <StateBlock state="empty" message="No controller yet. Add one to a demand below, or with the button above." />}
      {/* Three cards abreast on a very wide screen (DESIGN-SPEC §3.4/§7 B-6), one per row otherwise. */}
      <div className="grid">
        {driven.map(({ target, c, source }) => {
          const tag = typeof c.law?.tag === "string" ? c.law.tag : null;
          return (
            <div key={target.address} className={(name === null ? "c12 xl4" : "c12") + " controller-cell"}>
              <Faceplate
                controller={c}
                source={source}
                target={target}
                history={history[c.name]}
                windowS={windowS}
                yScale={yScale}
                every={every}
                exportHref={stored.ticks(c.name)}
                controls={<SetpointControl name={c.name} unit={source.unit} mode={c.mode} tag={tag} generators={controllerSchema.data?.generators} onEvent={onEvent} />}
                headerControls={<StopControl name={c.name} mode={c.mode} onEvent={onEvent} />}
              />
            </div>
          );
        })}
      </div>
      {name === null && undriven.length > 0 && <SectionHead icon={PAGE_ICONS.controllers} title="Demands without a controller" count={undriven.length} />}
      <div className="grid">
        {undriven.map((target) => (
          <div key={target.address} className={(name === null ? "c12 xl4" : "c12") + " controller-cell"}>
            <WritePanel signal={target} title={signalTitle(target, devices)} />
            <Button
              variant="outlined"
              size="small"
              startIcon={<AddIcon />}
              sx={{ mt: 1 }}
              onClick={() => openAdd(controllerSchema.data?.targets.find((t) => t.address === target.address))}
              data-testid={`add-controller-${target.address}`}
            >
              Add controller
            </Button>
          </div>
        ))}
      </div>
      {dialog}
    </>
  );
}
