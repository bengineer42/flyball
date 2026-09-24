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
import PanToolOutlinedIcon from "@mui/icons-material/PanToolOutlined";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import { Form as MuiForm } from "@rjsf/mui";
import { ControllerPanel, SchemaForm, WritePanel, useControllers, useQuery, useRig, type ControllerTrace } from "@flyball/react";
import { signalTitle, signalsOf, writable, type ControllerOut, type ControllerSchema, type DeviceOut, type FaultAction, type FeedforwardConfig, type JsonSchema, type OnFault, type LawConfig, type SetpointSpec, type SignalChoice, type StartSpec, type SignalOut } from "@flyball/client";
import { Confirm } from "../Confirm.js";
import { useControllerHolds } from "../controllerHolds.js";
import { useAuth } from "../auth.js";
import { useRecordingExports } from "../model.js";
import { TuningPicker } from "../TuningPicker.js";
import { ChartControls, type ChartSettings } from "../YScaleSelect.js";
import { generatorChoices, generatorFormSchema, generatorUiSchema, prune } from "../generatorForm.js";
import { PageBar } from "../PageBar.js";
import { SectionHead, StateBlock } from "../cards.js";
import { PAGE_ICONS } from "../icons.js";
import { Crumbs } from "./Readings.js";
import { hashFor } from "../router.js";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

type LawChoice = "stored" | "configure" | "none";

/** What the dialog sends: `law` is a tuning's name, a config, or null for no law; `feedforward` a config. */
interface Draft {
  /** The demand to drive, its output: the controller's name. */
  output: SignalChoice | null;
  /** The published signal to regulate, its measured signal, by address. */
  measured: string | null;
  lawChoice: LawChoice;
  tuning: string;
  config: LawConfig | null;
  /** Null until an output and a measured signal are chosen (the default depends on their units). */
  feedforward: FeedforwardConfig | null;
  isDefault: boolean;
  /** `on_fault`'s action; `freeze` (the rig's default) is not sent. */
  faultAction: FaultAction;
  /** Seconds of fault time to stay frozen before `faultAction` (`{freeze_s, then}`); blank: the rig's own wait. */
  freezeS: string;
  /** `setpoint_period_s`; blank: the rig's `max(0.1 s, poll_s / 4)`. */
  setpointPeriodS: string;
}

const EMPTY_DRAFT: Draft = { output: null, measured: null, lawChoice: "none", tuning: "", config: null, feedforward: null, isDefault: false, faultAction: "freeze", freezeS: "", setpointPeriodS: "" };

/** What each `on_fault` action does once the source has been faulty for its wait, for the picker. */
const FAULT_ACTIONS: Array<{ value: FaultAction; label: string; hint: string }> = [
  { value: "freeze", label: "freeze (default)", hint: "the law stays frozen and the output where it was; it resumes by itself after 3 readings with a value" },
  { value: "manual", label: "manual", hint: "the controller goes to manual; the output keeps its last value; latched until a Reset" },
  { value: "stop", label: "stop", hint: "manual, then the output's stop is written; the controller and output are latched until a Reset" },
  { value: "stop_device", label: "stop device", hint: "manual, then the output's whole device is stopped; latched until a Reset" },
];

/** An `on_fault` as the step's summary reads it: `freeze`, `stop`, `freeze 30 s, then stop`. */
export function describeOnFault(onFault: OnFault | undefined): string {
  if (onFault === undefined) return "freeze";
  if (typeof onFault === "string") return onFault.replace("_", " ");
  return `freeze ${onFault.freeze_s} s, then ${onFault.then.replace("_", " ")}`;
}

/** A positive number from a text box, else null (blank or not a number). */
const positive = (text: string): number | null => {
  const n = Number(text);
  return text.trim() !== "" && Number.isFinite(n) && n > 0 ? n : null;
};

/** The draft's `on_fault`: omitted for `freeze`; `{freeze_s, then}` when a freeze time is given. */
export function draftOnFault(action: FaultAction, freezeS: string): OnFault | undefined {
  if (action === "freeze") return undefined;
  const wait = positive(freezeS);
  return wait === null ? action : { freeze_s: wait, then: action };
}

/**
 * What the rig would pick when no feedforward is given: `identity` when the
 * output takes the measured signal's unit (output = setpoint), else `none`
 * (the law does all the work, in the output's unit).
 */
const defaultFeedforward = (target: SignalChoice | null, source: SignalChoice | null): FeedforwardConfig | null =>
  target && source ? { type: target.unit === source.unit ? "identity" : "none" } : null;

/**
 * A discriminated union's branches titled by their type (`PI`, not `PIConfig`),
 * so the form's kind picker reads as the types the rig speaks; the `type`
 * field itself is then hidden by `TYPE_HIDDEN`.
 */
function titledByType(schema: JsonSchema): JsonSchema {
  const defs = Object.fromEntries(
    Object.entries(schema.$defs ?? {}).map(([name, def]) => {
      const type = (def.properties?.type as JsonSchema | undefined)?.const;
      return [name, typeof type === "string" ? { ...def, title: type } : def];
    }),
  );
  return { ...schema, $defs: defs };
}
const TYPE_HIDDEN = { type: { "ui:widget": "hidden" } };

/** The feedforward schema with the `identity` branch dropped: across differing units the rig refuses it (409). */
function withoutIdentity(schema: JsonSchema): JsonSchema {
  const mapping = { ...(schema.discriminator?.mapping ?? {}) };
  const ref = mapping["identity"];
  delete mapping["identity"];
  return {
    ...schema,
    oneOf: (schema.oneOf ?? []).filter((b) => b.$ref !== ref && (b.properties?.type as JsonSchema | undefined)?.const !== "identity"),
    ...(schema.discriminator ? { discriminator: { ...schema.discriminator, mapping } } : {}),
  };
}

/**
 * Make a controller in five steps: the demand to drive (its output,
 * labelled "Output"), a published signal to regulate (labelled
 * "Measured"), the law, the feedforward that maps the
 * setpoint into the output's unit (defaulted from the units, as the rig
 * would), and what it does on a fault (`on_fault`, with the
 * `setpoint_period_s` beside it; blank fields take the rig's defaults). Signals another controller already regulates and outputs already
 * driven are disabled; measured signals in the output's own unit that nothing
 * regulates yet are listed first as "Suggested". Opened either from the
 * page bar (any output) or from an undriven signal's own card, which
 * preselects it (`initialTarget`) and jumps straight to the measured step.
 */
export const AddControllerDialog = memo(function AddControllerDialog({
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
      setDraft({ ...EMPTY_DRAFT, output: initialTarget });
      setActive(1);
    } else if (!open) {
      openedFor.current = null;
    }
  }, [open, initialTarget]);

  const source = useMemo(() => schema?.measured.find((c) => c.address === draft.measured) ?? null, [schema, draft.measured]);
  const demandUnit = draft.output?.unit ?? null;
  const unitsAgree = source !== null && demandUnit === source.unit;

  // Published signals by device; any unit may be regulated, the feedforward maps it into the output's.
  // Ranked once an output is chosen: a "Suggested" group (the output's own unit, not yet regulated)
  // above "All signals"; one flat list when there is no target or nothing matches -- never an empty
  // Suggested group.
  const sourceGroups = useMemo(() => {
    const group = (choices: SignalChoice[]) => {
      const groups: Record<string, SignalChoice[]> = {};
      for (const c of choices) (groups[c.device] ??= []).push(c);
      return groups;
    };
    const all = schema?.measured ?? [];
    const suggested = demandUnit === null ? [] : all.filter((c) => c.unit === demandUnit && !schema?.regulated[c.address]);
    if (suggested.length === 0) return [{ title: null, byDevice: group(all) }];
    const rest = all.filter((c) => !suggested.includes(c));
    return [
      { title: "Suggested", byDevice: group(suggested) },
      { title: "All signals", byDevice: group(rest) },
    ];
  }, [schema, demandUnit]);
  const hasSources = (schema?.measured.length ?? 0) > 0;
  // The feedforward the rig would pick on its own; set once both ends are known, and again whenever they change.
  const feedforward = draft.feedforward ?? defaultFeedforward(draft.output, source);
  const feedforwardSchema = useMemo(
    () => (schema?.feedforwards ? titledByType(unitsAgree ? schema.feedforwards : withoutIdentity(schema.feedforwards)) : undefined),
    [schema, unitsAgree],
  );

  const lawReady = draft.lawChoice === "none" || (draft.lawChoice === "stored" ? draft.tuning !== "" : draft.config !== null);
  // A blank box takes the rig's default; anything else must be a positive number.
  const freezeOk = draft.freezeS.trim() === "" || positive(draft.freezeS) !== null;
  const periodOk = draft.setpointPeriodS.trim() === "" || positive(draft.setpointPeriodS) !== null;
  const canCreate = Boolean(draft.output && draft.measured && lawReady && feedforward && freezeOk && periodOk);
  const onFault = draftOnFault(draft.faultAction, draft.freezeS);

  const create = async () => {
    if (!draft.output || !draft.measured) return;
    setBusy(true);
    try {
      const law = draft.lawChoice === "none" ? null : draft.lawChoice === "stored" ? draft.tuning : draft.config;
      const onFault = draftOnFault(draft.faultAction, draft.freezeS);
      const period = positive(draft.setpointPeriodS);
      const controller = await rig.createController({
        output: draft.output.address,
        measured: draft.measured,
        law,
        feedforward,
        is_default: draft.isDefault,
        ...(onFault !== undefined && { on_fault: onFault }),
        ...(period !== null && { setpoint_period_s: period }),
      });
      setError(null);
      onCreated(controller);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const tunings = schema?.tunings ?? [];
  const lawSchema = useMemo(() => (schema?.laws ? titledByType(schema.laws) : undefined), [schema]);

  return (
    <Dialog open={open} onClose={() => (busy ? undefined : onClose())} fullWidth maxWidth="sm">
      <DialogTitle>Add a controller</DialogTitle>
      <DialogContent>
        {!schema && <Typography color="text.secondary">loading what the rig can regulate…</Typography>}
        {schema && (
          <Stepper activeStep={active} orientation="vertical" nonLinear>
            <Step completed={draft.output !== null}>
              <StepLabel onClick={() => setActive(0)} sx={{ cursor: "pointer" }}>
                Output{draft.output && active !== 0 ? `: ${draft.output.address}` : ""}
                <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                  the demand the controller drives
                </Typography>
              </StepLabel>
              <StepContent>
                {schema.outputs.length === 0 && <Typography color="text.secondary">This rig has no writable signal to command.</Typography>}
                <List dense disablePadding>
                  {schema.outputs.map((t) => {
                    const taken = schema.driven[t.address];
                    return (
                      <ListItemButton
                        key={t.address}
                        selected={draft.output?.address === t.address}
                        disabled={Boolean(taken)}
                        onClick={() => {
                          patch({ output: t, feedforward: null });
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
            <Step completed={draft.measured !== null}>
              <StepLabel onClick={() => draft.output && setActive(1)} sx={{ cursor: draft.output ? "pointer" : "default" }}>
                Measured{draft.measured && active !== 1 ? `: ${draft.measured}` : ""}
                <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                  the published signal the controller regulates
                </Typography>
              </StepLabel>
              <StepContent>
                {!hasSources && <Typography color="text.secondary">{draft.output ? "No signal on this rig publishes." : "Pick an output first."}</Typography>}
                {sourceGroups.map(({ title, byDevice }) => (
                  <Box key={title ?? "all"} data-testid={title === null ? "sources" : `sources-${title.split(" ")[0]!.toLowerCase()}`}>
                    {title !== null && (
                      <Typography variant="overline" component="div" color={title === "Suggested" ? "primary" : "text.secondary"} sx={{ mt: title === "Suggested" ? 0 : 1.5 }}>
                        {title}
                        {title === "Suggested" && (
                          <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1, textTransform: "none", letterSpacing: 0 }}>
                            in {demandUnit}, the output's unit, and not yet regulated
                          </Typography>
                        )}
                      </Typography>
                    )}
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
                                  selected={draft.measured === c.address}
                                  disabled={Boolean(by)}
                                  onClick={() => {
                                    patch({ measured: c.address, feedforward: null });
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
                  </Box>
                ))}
              </StepContent>
            </Step>
            <Step completed={lawReady && draft.lawChoice !== "none"}>
              <StepLabel onClick={() => draft.measured && setActive(2)} sx={{ cursor: draft.measured ? "pointer" : "default" }}>
                Law
                {active !== 2 && (draft.lawChoice === "none" ? ": none (manual only)" : draft.lawChoice === "stored" ? `: tuning ${draft.tuning || "…"}` : draft.config ? `: ${String(draft.config.type)}` : "")}
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
                      {draft.config && <Chip label={`law set: ${String(draft.config.type)}`} color="success" variant="outlined" sx={{ mb: 1.5 }} />}
                      <SchemaForm
                        key={formKey}
                        schema={lawSchema}
                        value={draft.config ?? undefined}
                        uiSchema={TYPE_HIDDEN}
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
              <StepLabel onClick={() => draft.measured && setActive(3)} sx={{ cursor: draft.measured ? "pointer" : "default" }}>
                Feedforward{feedforward && active !== 3 ? `: ${feedforward.type}` : ""}
              </StepLabel>
              <StepContent>
                <Stack spacing={1.5}>
                  {!(draft.output && source) && <Typography color="text.secondary">Pick an output and a measured signal first.</Typography>}
                  {draft.output && source && (
                    <Typography variant="body2" color="text.secondary" data-testid="feedforward-help">
                      {unitsAgree
                        ? `${draft.output.address} takes demands in ${demandUnit}, the same as ${source.address} — "identity" passes the setpoint straight through and the law corrects in ${demandUnit}.`
                        : `${draft.output.address} takes ${demandUnit}; ${source.address} is ${source.unit} — the feedforward maps one to the other, the law corrects in ${demandUnit}. "identity" is not offered: the units differ. "none" leaves all of it to the law.`}
                    </Typography>
                  )}
                  {feedforward && (
                    <Chip label={`feedforward set: ${feedforward.type}${draft.feedforward === null ? " (the rig's default)" : ""}`} color="success" variant="outlined" sx={{ alignSelf: "flex-start" }} data-testid="feedforward-set" />
                  )}
                  {draft.output && source && feedforwardSchema && (
                    <Box data-testid="feedforward-form">
                      <SchemaForm
                        key={`${formKey}-${draft.output.address}-${draft.measured}`}
                        schema={feedforwardSchema}
                        value={feedforward ?? undefined}
                        uiSchema={TYPE_HIDDEN}
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
            <Step completed={draft.measured !== null}>
              <StepLabel onClick={() => draft.measured && setActive(4)} sx={{ cursor: draft.measured ? "pointer" : "default" }}>
                On fault{active !== 4 ? `: ${describeOnFault(onFault)}` : ""}
                <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                  what it does once its measured signal has had no value for a while
                </Typography>
              </StepLabel>
              <StepContent>
                <Stack spacing={1.5}>
                  <FormControl size="small" sx={{ maxWidth: 260 }}>
                    <Select value={draft.faultAction} onChange={(e) => patch({ faultAction: e.target.value as FaultAction })} inputProps={{ "aria-label": "on fault", "data-testid": "on-fault" }}>
                      {FAULT_ACTIONS.map((a) => (
                        <MenuItem key={a.value} value={a.value}>
                          {a.label}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                  <Typography variant="body2" color="text.secondary">
                    {FAULT_ACTIONS.find((a) => a.value === draft.faultAction)!.hint}
                  </Typography>
                  {draft.faultAction !== "freeze" && (
                    <TextField
                      size="small"
                      label="freeze first for (s, optional)"
                      value={draft.freezeS}
                      onChange={(e) => patch({ freezeS: e.target.value })}
                      error={!freezeOk}
                      helperText={freezeOk ? "seconds of fault time to stay frozen before acting; blank: the rig's own wait (about two poll periods)" : "a number of seconds above zero"}
                      inputProps={{ inputMode: "decimal", "data-testid": "freeze-s" }}
                      sx={{ maxWidth: 360 }}
                    />
                  )}
                  <TextField
                    size="small"
                    label="setpoint period (s, optional)"
                    value={draft.setpointPeriodS}
                    onChange={(e) => patch({ setpointPeriodS: e.target.value })}
                    error={!periodOk}
                    helperText={periodOk ? "while following a moving setpoint, re-apply the feedforward this often between readings; blank: max(0.1 s, poll period / 4)" : "a number of seconds above zero"}
                    inputProps={{ inputMode: "decimal", "data-testid": "setpoint-period" }}
                    sx={{ maxWidth: 360 }}
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
type From = "setpoint" | "measured" | "value";

/**
 * The target entry and its verb button, rendered inline in the faceplate's
 * Setpoint row (DESIGN-SPEC §3.4). A kind picker offers a plain value or any
 * setpoint generator the rig registers (`GET /api/controllers/schema` →
 * `generators`, never a list kept here). A plain value: one box, and
 * "Regulate" hands control to the law at it (a bumpless start) while stopped,
 * "Move setpoint" changes it and leaves the law running while regulating. A
 * generator: its config as a form built from its own JSON schema
 * (`generatorFormSchema`), a "from" choice for where it starts (the setpoint,
 * the reading, or a value -- the request's `start`), and one "Start <kind>"
 * button that regulates while stopped and moves the reference while
 * regulating. Takes primitives and a stable callback so it does not re-render
 * on every tick (its text field is a MUI form control, which sets state in an
 * effect whenever it renders in development). Split from the stop/remove
 * control below so Tab reaches this field and its button before Manual, which
 * the faceplate places in the header regardless of where it sits in the DOM.
 */
const SetpointControl = memo(function SetpointControl({ name, unit, mode, law, hasSetpoint, generators, onEvent }: { name: string; unit: string; mode: ControllerOut["mode"]; law: string | null; hasSetpoint: boolean; generators: JsonSchema | undefined; onEvent(name: string, kind: "changed" | "removed"): void }) {
  const rig = useRig();
  const [setpoint, setSetpoint] = useState("");
  const [kind, setKind] = useState("value");
  const [from, setFrom] = useState<From | null>(null);
  const [fromValue, setFromValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasLaw = law !== null && law !== "open_loop";
  const regulating = mode === "regulating";
  // A faceplate offers a value or a ramp to it; a hold or a profile is a program's business, not a knob on a loop.
  const choices = useMemo(() => generatorChoices(generators).filter((c) => /ramp/i.test(c.type)), [generators]);
  const chosen = choices.find((c) => c.type === kind);
  const formSchema = useMemo(() => (generators && chosen ? generatorFormSchema(generators, chosen.type, unit) : undefined), [generators, chosen, unit]);
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
  const send = (at: SetpointSpec, start?: StartSpec | null) => act(() => (regulating ? rig.setSetpoint(name, at, start) : rig.regulate(name, start == null ? { at } : { at, start })));

  // Where a generator starts: the server's own rule (the setpoint while regulating, else the
  // reading) unless the operator says otherwise, which is what is shown as chosen.
  const fromNow: From = from ?? (regulating ? "setpoint" : "measured");
  const typedStart = fromValue.trim() === "" ? null : Number(fromValue);
  const start: StartSpec | null = fromNow === "value" ? (typedStart !== null && Number.isFinite(typedStart) ? typedStart : null) : fromNow;
  const startValid = fromNow !== "value" || start !== null;

  const startLabel = hasLaw ? "Aim here and start the law (bumpless)" : law === null ? "No law: give the controller a tuning first" : "Open loop: no law to regulate with";
  // One height for every control on the row (a select, a number box, a button), so they sit on one line, centred.
  const box = { "& .MuiInputBase-root": { height: 32, fontSize: "0.875rem" }, "& .MuiInputBase-input": { py: 0, height: 32, boxSizing: "border-box" } } as const;
  const select = { height: 32, fontSize: "0.85rem", "& .MuiSelect-select": { py: 0, display: "flex", alignItems: "center", height: "32px !important", boxSizing: "border-box" } } as const;
  const button = { flexShrink: 0, whiteSpace: "nowrap", height: 32 } as const;
  const canStart = regulating || hasLaw;

  return (
    <Stack component="span" direction="row" spacing={0.75} alignItems="center" useFlexGap sx={{ display: "inline-flex", flexWrap: "wrap", minWidth: 0 }}>
      {choices.length > 0 && (
        <FormControl size="small" sx={{ flexShrink: 0 }}>
          <Select value={chosen ? chosen.type : "value"} onChange={(e) => setKind(e.target.value)} inputProps={{ "aria-label": `how to set the target of ${name}` }} data-testid={`kind-${name}`} sx={select}>
            <MenuItem value="value">go to</MenuItem>
            {choices.map((c) => (
              <MenuItem key={c.type} value={c.type}>
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
            size="small"
            hiddenLabel
            placeholder="target"
            value={setpoint}
            onChange={(e) => setSetpoint(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && valid && !busy && canStart) void send(value!);
            }}
            inputProps={{ "aria-label": `target ${name}`, step: "any", "data-testid": `regulate-at-${name}`, style: { width: "5em" } }}
            InputProps={{ endAdornment: <span style={{ fontSize: "0.8rem", opacity: 0.7, marginLeft: 4 }}>{unit}</span> }}
            sx={{ flexShrink: 0, ...box }}
          />
          {regulating ? (
            <Tooltip title="Change the target; the law keeps running as it is">
              <span>
                <Button variant="contained" size="small" disabled={!valid || busy} onClick={() => void send(value!)} data-testid={`set-reference-${name}`} sx={button}>
                  Move setpoint
                </Button>
              </span>
            </Tooltip>
          ) : (
            <Tooltip title={startLabel}>
              <span>
                <Button variant="contained" size="small" startIcon={<PlayArrowIcon />} disabled={!hasLaw || !valid || busy} onClick={() => void send(value!)} data-testid={`regulate-${name}`} sx={button}>
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
            <Select value={fromNow} onChange={(e) => setFrom(e.target.value as From)} inputProps={{ "aria-label": `where the ${chosen.label.toLowerCase()} on ${name} starts` }} data-testid={`from-${name}`} sx={select}>
              <Tooltip title={hasSetpoint ? "" : "This loop has never run: there is no setpoint yet to start from."} placement="right">
                <span>
                  <MenuItem value="setpoint" disabled={!hasSetpoint} data-testid={`from-setpoint-${name}`}>
                    from setpoint
                  </MenuItem>
                </span>
              </Tooltip>
              <MenuItem value="measured">from measured</MenuItem>
              <MenuItem value="value">from a value</MenuItem>
            </Select>
          </FormControl>
          {fromNow === "value" && (
            <TextField
              type="number"
              size="small"
              hiddenLabel
              placeholder="start"
              value={fromValue}
              onChange={(e) => setFromValue(e.target.value)}
              inputProps={{ "aria-label": `start value for ${name}`, step: "any", "data-testid": `start-${name}`, style: { width: "5em" } }}
              InputProps={{ endAdornment: <span style={{ fontSize: "0.8rem", opacity: 0.7, marginLeft: 4 }}>{unit}</span> }}
              sx={{ flexShrink: 0, ...box }}
            />
          )}
          {/* The generator's own fields, from its schema; the whole row's width, under the pickers. */}
          <Tooltip title={!canStart ? startLabel : regulating ? `${chosen.verb}; the law keeps running as it is` : `${chosen.verb} and hand control to the law (bumpless)`}>
            <Box className="fb-generator" data-testid={`generator-${name}`} sx={{ width: "100%", minWidth: 0 }}>
              <SchemaForm
                key={`${name}-${chosen.type}`}
                schema={formSchema}
                uiSchema={formUi}
                form={MuiForm}
                idPrefix={`gen-${name.replace(/\W/g, "_")}`}
                submitLabel={chosen.verb}
                disabled={busy || !canStart || !startValid}
                onSubmit={(data) => {
                  const spec = prune(data) ?? {};
                  void send({ ...spec, type: chosen.type }, start);
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
 * Manual and remove, rendered in the faceplate's header (DESIGN-SPEC §3.4)
 * even though the panel places them after the Setpoint row in the DOM, so Tab
 * reaches target -> Move -> Manual in that order. Manual stops regulating; it
 * is not the rig stop, and writes nothing.
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
      <Tooltip title="Put in manual: stop regulating; the output holds its last demand and takes demands directly">
        <span>
          <Button
            variant="outlined"
            startIcon={<PanToolOutlinedIcon />}
            disabled={mode === "manual" || busy}
            onClick={() => void act(() => rig.manual(name)).then((ok) => ok && onEvent(name, "changed"))}
            data-testid={`manual-${name}`}
          >
            Manual
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
        title={regulating ? `${name} is regulating — put it in manual and remove?` : `Remove controller ${name}?`}
        text={
          regulating
            ? `Removing ${name} puts it in manual first: the target keeps its last demand and takes demands directly. Its source is then free for another controller.`
            : `Its source is then free for another controller; the target keeps its last demand.`
        }
        action={regulating ? "Manual and remove" : "Remove"}
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
  exportHref?: string;
  controls?: ReactNode;
  headerControls?: ReactNode;
}) {
  const auth = useAuth();
  const holds = useControllerHolds(controller.name);
  return (
    <>
    <ControllerPanel
      controller={controller}
      source={source}
      target={target}
      history={history}
      windowS={windowS}
      yScale={yScale}
      exportHref={exportHref}
      trends
      detail
      controls={controls}
      headerControls={headerControls}
      canOperate={auth.canOperate}
      onReset={holds.onReset}
    />
    {holds.dialog}
    </>
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
  const { windowS, yScale } = charts;
  const auth = useAuth();
  const rig = useRig();
  const stored = useRecordingExports();
  const { controllers, history, status } = useControllers(3600);
  const signals = useMemo(() => new Map(devices.flatMap((d) => signalsOf(d.signals)).map((s) => [s.address, s])), [devices]);
  // A controller's output is a demand: settable, with a readback that updates. A demand the driver
  // declares read-only (a composite's readback, e.g. a blender's per-pump flows) is not one.
  const targets = useMemo(() => [...signals.values()].filter((s) => s.role === "demand" && writable(s)), [signals]);
  const controllerSchema = useQuery(() => rig.controllerSchema(), [rig]);
  // The stream never says a controller is gone: hide one we detached until the stream sends a new object for that name (re-created).
  const [removed, setRemoved] = useState<Record<string, ControllerOut>>({});

  const all = controllers;
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
      }
      controllerSchema.refresh();
    },
    [controllerSchema.refresh],
  );

  // Three cards abreast on a very wide screen (DESIGN-SPEC §3.4/§7 B-6) only once there are
  // enough of them to fill a row; fewer than that would just leave dead space beside them.
  const cardClass = (count: number) => (count >= 3 ? "c12 xl4" : count === 2 ? "c12 xl6" : "c12");
  const only = shown.length === 1 ? controllerOf(shown[0]!.address) : undefined;
  const driven = shown.flatMap((target) => {
    const c = controllerOf(target.address);
    const source = c ? signals.get(c.measured_signal) : undefined;
    return c && source ? [{ target, c, source }] : [];
  });
  // Every demand nothing drives, including the others on a device one of whose demands is driven (a
  // furnace's other zones): a composite's inner demands are readbacks now, not writable, so not listed.
  const undriven = shown.filter((target) => !driven.some((d) => d.target === target));
  const toolbar = (
    <PageBar end={<ChartControls {...charts} unit={only ? signals.get(only.measured_signal)?.unit : undefined} />}>
      {name !== null && <Crumbs items={[{ label: "controllers", href: hashFor("controllers") }, { label: name }]} />}
      {controllerSchema.error && <Typography color="error">{controllerSchema.error.message}</Typography>}
    </PageBar>
  );
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
      {name === null && driven.length === 0 && shown.length > 0 && <StateBlock state="empty" message="No controller yet. Add one in Config." />}
      {/* Three cards abreast on a very wide screen (DESIGN-SPEC §3.4/§7 B-6), fewer if there
          aren't three to show, one per row otherwise. */}
      <div className="grid">
        {driven.map(({ target, c, source }) => {
          const law = typeof c.law?.type === "string" ? c.law.type : null;
          return (
            <div key={target.address} className={(name === null ? cardClass(driven.length) : "c12") + " controller-cell"}>
              <Faceplate
                controller={c}
                source={source}
                target={target}
                history={history[c.name]}
                windowS={windowS}
                yScale={yScale}
                exportHref={stored.ticks(c.name)}
                controls={<SetpointControl name={c.name} unit={source.unit} mode={c.mode} law={law} hasSetpoint={c.reference !== null} generators={controllerSchema.data?.generators} onEvent={onEvent} />}
                headerControls={<StopControl name={c.name} mode={c.mode} onEvent={onEvent} />}
              />
            </div>
          );
        })}
      </div>
      {name === null && undriven.length > 0 && <SectionHead icon={PAGE_ICONS.controllers} title="Demands without a controller" count={undriven.length} />}
      <div className="grid">
        {undriven.map((target) => (
          <div key={target.address} className={(name === null ? cardClass(undriven.length) : "c12") + " controller-cell"}>
            <WritePanel signal={target} title={signalTitle(target, devices)} canOperate={auth.canOperate} />
          </div>
        ))}
      </div>
    </>
  );
}
