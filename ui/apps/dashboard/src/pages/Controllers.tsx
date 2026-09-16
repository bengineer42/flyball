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
  FormControlLabel,
  IconButton,
  List,
  ListItemButton,
  ListItemText,
  ListSubheader,
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
import { LoopPanel, SchemaForm, channelKey, useActuatorStates, useFreshness, useLoops, useQuery, useReaderPeriods, useRig, useRigSchema, useSources, type LoopTrace } from "@flyball/react";
import { alarmLevel, type ActuatorChoice, type ActuatorSchema, type ChannelOut, type FeedforwardConfig, type GeneratorConfig, type JsonSchema, type LawConfig, type LoopOut, type LoopSchema, type ReaderSchema } from "@flyball/client";
import { Actuator } from "../Actuator.js";
import { Confirm } from "../Confirm.js";
import { useRecordingExports } from "../model.js";
import { TuningPicker } from "../TuningPicker.js";
import { ChartControls, type ChartSettings } from "../YScaleSelect.js";
import { PageBar } from "../PageBar.js";
import { SectionHead, StateBlock } from "../cards.js";
import { PAGE_ICONS } from "../icons.js";
import { Crumbs } from "./Sources.js";
import { hashFor } from "../router.js";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const channelName = (c: ChannelOut) => `${c.source}.${c.measurand}`;
/** `/api/loops/schema` channels carry a `dimension`; the wire type does not declare it yet. */
const dimensionOf = (c: ChannelOut) => {
  const d = (c as { dimension?: unknown }).dimension;
  return typeof d === "string" ? d : "";
};

type LawChoice = "stored" | "configure" | "none";

/** What the dialog sends: `law` is a tuning's name, a config, or null for no law; `feedforward` a config. */
interface Draft {
  actuator: ActuatorChoice | null;
  channel: string | null;
  lawChoice: LawChoice;
  tuning: string;
  config: LawConfig | null;
  /** Null until an actuator and channel are chosen (the default depends on their units). */
  feedforward: FeedforwardConfig | null;
  isDefault: boolean;
}

const EMPTY_DRAFT: Draft = { actuator: null, channel: null, lawChoice: "none", tuning: "", config: null, feedforward: null, isDefault: false };

/** The unit an actuator's demands are in: its own, or the channel's when it has none. */
const demandUnitOf = (actuator: ActuatorChoice | null, channel: ChannelOut | null) => actuator?.demand_unit ?? channel?.unit ?? null;

/**
 * What the rig would pick when no feedforward is given: `setpoint` when the
 * actuator takes the channel's unit (demand = setpoint), else `none` (the
 * law does all the work, in the actuator's unit).
 */
const defaultFeedforward = (actuator: ActuatorChoice | null, channel: ChannelOut | null): FeedforwardConfig | null =>
  actuator && channel ? { tag: actuator.demand_unit === null || actuator.demand_unit === channel.unit ? "setpoint" : "none" } : null;

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
 * Make a controller in four steps: the actuator, a channel it may take, the
 * law, and the feedforward that maps the setpoint into the actuator's unit
 * (defaulted from the units, as the rig would). Channels another controller
 * already regulates are disabled. Opened either from the page bar (any
 * actuator) or from an unlooped actuator's own card, which preselects it
 * (`initialActuator`) and jumps straight to the channel step.
 */
/** Display names by identifier, for the pickers; a name not here is shown as itself. */
interface Labels {
  actuators: Record<string, string | null | undefined>;
  sources: Record<string, string | null | undefined>;
}

const AddLoopDialog = memo(function AddLoopDialog({
  open,
  schema,
  labels,
  initialActuator,
  onClose,
  onCreated,
}: {
  open: boolean;
  schema: LoopSchema | undefined;
  labels?: Labels;
  /** Preselect this actuator (an unlooped actuator's own "Add controller" button) and open on the channel step. */
  initialActuator?: ActuatorChoice | null;
  onClose(): void;
  onCreated(loop: LoopOut): void;
}) {
  const actuatorLabel = (name: string) => labels?.actuators[name] ?? name;
  const sourceLabel = (name: string) => labels?.sources[name] ?? name;
  const rig = useRig();
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  // Bumped to remount a SchemaForm with new initial values (it keeps its own form state after the first render).
  const [formKey, setFormKey] = useState(0);
  const [active, setActive] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const patch = (p: Partial<Draft>) => setDraft((d) => ({ ...d, ...p }));
  // Opening on a preselected actuator (from its own card) starts the stepper on the channel step.
  const openedFor = useRef<string | null>(null);
  useEffect(() => {
    if (open && initialActuator && openedFor.current !== initialActuator.name) {
      openedFor.current = initialActuator.name;
      setDraft({ ...EMPTY_DRAFT, actuator: initialActuator });
      setActive(1);
    } else if (!open) {
      openedFor.current = null;
    }
  }, [open, initialActuator]);

  const driving = useMemo(() => {
    // actuator name → the controller it drives (a controller is named after its actuator).
    const out: Record<string, string> = {};
    for (const loop of Object.values(schema?.regulated ?? {})) out[loop] = loop;
    return out;
  }, [schema]);

  const bySource = useMemo(() => {
    const allowed = new Set(draft.actuator?.channels ?? []);
    const groups: Record<string, ChannelOut[]> = {};
    for (const c of schema?.channels ?? []) {
      if (!allowed.has(channelName(c))) continue;
      (groups[c.source] ??= []).push(c);
    }
    return groups;
  }, [schema, draft.actuator]);

  const channel = useMemo(() => schema?.channels.find((c) => channelName(c) === draft.channel) ?? null, [schema, draft.channel]);
  const demandUnit = demandUnitOf(draft.actuator, channel);
  const unitsAgree = channel !== null && demandUnit === channel.unit;
  // The feedforward the rig would pick on its own; set once both ends are known, and again whenever they change.
  const feedforward = draft.feedforward ?? defaultFeedforward(draft.actuator, channel);
  const feedforwardSchema = useMemo(
    () => (schema?.feedforwards ? titledByTag(unitsAgree ? schema.feedforwards : withoutSetpoint(schema.feedforwards)) : undefined),
    [schema, unitsAgree],
  );

  const lawReady = draft.lawChoice === "none" || (draft.lawChoice === "stored" ? draft.tuning !== "" : draft.config !== null);
  const canCreate = Boolean(draft.actuator && draft.channel && lawReady && feedforward);

  const create = async () => {
    if (!draft.actuator || !draft.channel) return;
    setBusy(true);
    try {
      const law = draft.lawChoice === "none" ? null : draft.lawChoice === "stored" ? draft.tuning : draft.config;
      const loop = await rig.makeLoop({ channel: draft.channel, actuator: draft.actuator.name, law, feedforward, default: draft.isDefault });
      setError(null);
      onCreated(loop);
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
            <Step completed={draft.actuator !== null}>
              <StepLabel onClick={() => setActive(0)} sx={{ cursor: "pointer" }}>
                Actuator{draft.actuator && active !== 0 ? `: ${actuatorLabel(draft.actuator.name)}` : ""}
              </StepLabel>
              <StepContent>
                {schema.actuators.length === 0 && <Typography color="text.secondary">This rig has no actuators to drive.</Typography>}
                <List dense disablePadding>
                  {schema.actuators.map((a) => {
                    const taken = driving[a.name];
                    return (
                      <ListItemButton
                        key={a.name}
                        selected={draft.actuator?.name === a.name}
                        disabled={Boolean(taken)}
                        onClick={() => {
                          patch({ actuator: a, channel: a.channels.includes(draft.channel ?? "") ? draft.channel : null, feedforward: null });
                          setActive(1);
                        }}
                        data-actuator={a.name}
                      >
                        <ListItemText
                          primary={actuatorLabel(a.name)}
                          secondary={`${labels?.actuators[a.name] ? `${a.name} · ` : ""}${a.type} · ${taken ? `already drives controller ${taken}` : a.demand_unit ? `takes demands in ${a.demand_unit}` : "any channel"}`}
                        />
                      </ListItemButton>
                    );
                  })}
                </List>
              </StepContent>
            </Step>
            <Step completed={draft.channel !== null}>
              <StepLabel onClick={() => draft.actuator && setActive(1)} sx={{ cursor: draft.actuator ? "pointer" : "default" }}>
                Channel{draft.channel && active !== 1 ? `: ${draft.channel}` : ""}
              </StepLabel>
              <StepContent>
                {Object.keys(bySource).length === 0 && (
                  <Typography color="text.secondary">{draft.actuator ? `${draft.actuator.name} may regulate no channel on this rig.` : "Pick an actuator first."}</Typography>
                )}
                <List dense disablePadding>
                  {Object.entries(bySource).map(([source, channels]) => (
                    <li key={source}>
                      <ul style={{ padding: 0 }}>
                        <ListSubheader disableSticky sx={{ lineHeight: "28px" }}>
                          {sourceLabel(source)}
                          {labels?.sources[source] && (
                            <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1.5 }}>
                              {source}
                            </Typography>
                          )}
                        </ListSubheader>
                        {channels.map((c) => {
                          const name = channelName(c);
                          const by = schema.regulated[name];
                          return (
                            <ListItemButton
                              key={name}
                              selected={draft.channel === name}
                              disabled={Boolean(by)}
                              onClick={() => {
                                patch({ channel: name, feedforward: null });
                                setActive(2);
                              }}
                              data-channel={name}
                            >
                              <ListItemText primary={`${c.label || c.measurand} · ${c.unit}`} secondary={`${dimensionOf(c)}${by ? ` · regulated by ${by}` : ""}`} />
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
              <StepLabel onClick={() => draft.channel && setActive(2)} sx={{ cursor: draft.channel ? "pointer" : "default" }}>
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
              <StepLabel onClick={() => draft.channel && setActive(3)} sx={{ cursor: draft.channel ? "pointer" : "default" }}>
                Feedforward{feedforward && active !== 3 ? `: ${feedforward.tag}` : ""}
              </StepLabel>
              <StepContent>
                <Stack spacing={1.5}>
                  {!(draft.actuator && channel) && <Typography color="text.secondary">Pick an actuator and a channel first.</Typography>}
                  {draft.actuator && channel && (
                    <Typography variant="body2" color="text.secondary" data-testid="feedforward-help">
                      {unitsAgree
                        ? `${draft.actuator.name} takes demands in ${demandUnit}, the same as ${channelName(channel)} — "setpoint" passes the setpoint straight through and the law corrects in ${demandUnit}.`
                        : `${draft.actuator.name} takes ${demandUnit}; ${channelName(channel)} is ${channel.unit} — the feedforward maps one to the other, the law corrects in ${demandUnit}. "setpoint" is not offered: the units differ. "none" leaves all of it to the law.`}
                    </Typography>
                  )}
                  {feedforward && (
                    <Chip label={`feedforward set: ${feedforward.tag}${draft.feedforward === null ? " (the rig's default)" : ""}`} color="success" variant="outlined" sx={{ alignSelf: "flex-start" }} data-testid="feedforward-set" />
                  )}
                  {draft.actuator && channel && feedforwardSchema && (
                    <Box data-testid="feedforward-form">
                      <SchemaForm
                        key={`${formKey}-${draft.actuator.name}-${draft.channel}`}
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
        <Button variant="contained" onClick={() => void create()} disabled={!canCreate || busy} data-testid="create-loop">
          Create
        </Button>
      </DialogActions>
    </Dialog>
  );
});

/** How a ramp's pace is given: a rate in the channel's unit per minute or second, or the time the whole ramp takes. */
const PACES = [
  { key: "per_minute", label: (unit: string) => `${unit}/min` },
  { key: "per_second", label: (unit: string) => `${unit}/s` },
  { key: "minutes", label: () => "min" },
  { key: "seconds", label: () => "s" },
] as const;
type PaceKey = (typeof PACES)[number]["key"];

/**
 * The setpoint entry and its verb button, rendered inline in the faceplate's
 * Target row (DESIGN-SPEC §3.4): "Regulate at" hands control to the law at
 * this value (a bumpless start) while stopped, "Move target" changes the
 * target and leaves the law running while regulating. A "ramp" toggle turns
 * the value into a destination and adds a pace and a starting point -- the
 * current setpoint unless the reading or a typed value is chosen -- and the
 * rig walks the setpoint from there. Takes primitives and a stable callback so it does not
 * re-render on every tick (its text field is a MUI form control, which sets
 * state in an effect whenever it renders in development). Split from the
 * stop/remove control below so Tab reaches this field and its button before
 * Stop, which the faceplate places in the header regardless of where it sits
 * in the DOM.
 */
const LoopSetpointControl = memo(function LoopSetpointControl({ name, unit, mode, tag, onEvent }: { name: string; unit: string; mode: LoopOut["mode"]; tag: string | null; onEvent(name: string, kind: "changed" | "removed"): void }) {
  const rig = useRig();
  const [setpoint, setSetpoint] = useState("");
  const [ramp, setRamp] = useState(false);
  const [pace, setPace] = useState("");
  const [paceKey, setPaceKey] = useState<PaceKey>("per_minute");
  const [from, setFrom] = useState<"setpoint" | "process" | "value">("setpoint");
  const [fromValue, setFromValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasLaw = tag !== null && tag !== "open_loop";
  const regulating = mode === "regulating";

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
  const paceValue = pace.trim() === "" ? null : Number(pace);
  const startValue = fromValue.trim() === "" ? null : Number(fromValue);
  const valid =
    value !== null &&
    Number.isFinite(value) &&
    (!ramp || (paceValue !== null && Number.isFinite(paceValue) && paceValue > 0 && (from !== "value" || (startValue !== null && Number.isFinite(startValue)))));
  // A ramp sets off from the current setpoint unless told otherwise: the reading, or a value typed in.
  const generator: GeneratorConfig | null = ramp && paceValue !== null ? { tag: "ramp", to: value!, pace: { [paceKey]: paceValue } } : null;
  const at = from === "value" ? startValue! : from;
  const start = () => (generator ? rig.regulate(name, { at, generator }) : rig.regulate(name, { at: value! }));
  const move = () => (generator ? rig.setReference(name, at, generator) : rig.setReference(name, value!));

  // One field, one verb. Stopped: "Regulate at" hands control to the law at
  // this target (a bumpless start). Regulating: "Move target" changes the
  // target and leaves the law running as it is. Both write the same number;
  // what differs is whether control is being started or already on.
  const startLabel = !hasLaw ? (tag === null ? "No law: give the controller a tuning first" : "Open loop: no law to regulate with") : ramp ? "Start the law where the ramp sets off and ramp to the target" : "Aim here and start the law (bumpless)";

  return (
    <Stack component="span" direction="row" spacing={0.75} alignItems="center" useFlexGap sx={{ display: "inline-flex", flexWrap: { xs: "wrap", md: "nowrap" } }}>
      <TextField
        type="number"
        label={ramp ? "ramp to" : "target"}
        value={setpoint}
        onChange={(e) => setSetpoint(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && valid && !busy) {
            void act(regulating ? move : start);
          }
        }}
        inputProps={{ "aria-label": `target ${name}`, step: "any", "data-testid": `regulate-at-${name}`, style: { width: "4.5em" } }}
        InputProps={{ endAdornment: <span style={{ fontSize: "0.8rem", opacity: 0.7 }}>{unit}</span> }}
        sx={{ flexShrink: 0, "& .MuiInputBase-input": { py: 0.75 } }}
      />
      <Tooltip title={ramp ? "Go straight to the target" : "Walk the setpoint there at a pace"}>
        <ToggleButton value="ramp" size="small" selected={ramp} onChange={() => setRamp((r) => !r)} aria-label={`ramp ${name}`} data-testid={`ramp-${name}`} sx={{ flexShrink: 0, py: 0.5, textTransform: "none" }}>
          ramp
        </ToggleButton>
      </Tooltip>
      {ramp && (
        <TextField
          select
          label="from"
          value={from}
          onChange={(e) => setFrom(e.target.value as typeof from)}
          SelectProps={{ native: true }}
          inputProps={{ "aria-label": `ramp from ${name}`, "data-testid": `ramp-from-${name}` }}
          sx={{ flexShrink: 0, "& .MuiInputBase-input": { py: 0.75 } }}
        >
          <option value="setpoint">setpoint</option>
          <option value="process">reading</option>
          <option value="value">value…</option>
        </TextField>
      )}
      {ramp && from === "value" && (
        <TextField
          type="number"
          label="from"
          value={fromValue}
          onChange={(e) => setFromValue(e.target.value)}
          inputProps={{ "aria-label": `ramp from value ${name}`, step: "any", "data-testid": `ramp-from-value-${name}`, style: { width: "4.5em" } }}
          InputProps={{ endAdornment: <span style={{ fontSize: "0.8rem", opacity: 0.7 }}>{unit}</span> }}
          sx={{ flexShrink: 0, "& .MuiInputBase-input": { py: 0.75 } }}
        />
      )}
      {ramp && (
        <TextField
          type="number"
          label="pace"
          value={pace}
          onChange={(e) => setPace(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && valid && !busy) void act(regulating ? move : start);
          }}
          inputProps={{ "aria-label": `pace ${name}`, step: "any", min: 0, "data-testid": `pace-${name}`, style: { width: "4em" } }}
          InputProps={{
            endAdornment: (
              <select value={paceKey} onChange={(e) => setPaceKey(e.target.value as PaceKey)} aria-label={`pace unit ${name}`} style={{ fontSize: "0.8rem", border: "none", background: "transparent", color: "inherit" }}>
                {PACES.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.label(unit)}
                  </option>
                ))}
              </select>
            ),
          }}
          sx={{ flexShrink: 0, "& .MuiInputBase-input": { py: 0.75 } }}
        />
      )}
      {regulating ? (
        <Tooltip title={ramp ? "Start the ramp; the law keeps running as it is" : "Change the target; the law keeps running as it is"}>
          <span>
            <Button
              variant="contained"
              size="small"
              disabled={!valid || busy}
              onClick={() => void act(move)}
              data-testid={`set-reference-${name}`}
              sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
            >
              {ramp ? "Ramp" : "Move target"}
            </Button>
          </span>
        </Tooltip>
      ) : (
        <Tooltip title={startLabel}>
          <span>
            <Button
              variant="contained"
              size="small"
              startIcon={<PlayArrowIcon />}
              disabled={!hasLaw || !valid || busy}
              onClick={() => void act(start)}
              data-testid={`regulate-${name}`}
              sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
            >
              {ramp ? "Ramp" : "Regulate at"}
            </Button>
          </span>
        </Tooltip>
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
const LoopStopControl = memo(function LoopStopControl({ name, mode, onEvent }: { name: string; mode: LoopOut["mode"]; onEvent(name: string, kind: "changed" | "removed"): void }) {
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
      <Tooltip title="Stop regulating: the actuator holds its last demand and takes commands directly">
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
            ? `Removing ${name} stops it first: the actuator keeps its last demand and takes commands directly. The channel is then free for another controller.`
            : `The channel is then free for another controller; the actuator stays attached.`
        }
        action={regulating ? "Stop and remove" : "Remove"}
        onClose={() => setConfirmRemove(false)}
        onConfirm={() => {
          void act(() => rig.removeLoop(name)).then((ok) => {
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
 * One controller's faceplate, wrapping `LoopPanel` so `useFreshness` runs
 * once per controller rather than a variable number of times inside a
 * list's `.map` (which would break the rules of hooks as controllers come
 * and go). Reader-offline (B-3) is the channel's own staleness: its
 * reader's period against the time since its last sample, in rig time.
 * `extra` is the collapsible actuator state/config/settings/commands
 * section the merged Controllers page adds below the faceplate (no
 * duplication of demand/output range, which the Output row already shows).
 */
const LoopFaceplate = memo(function LoopFaceplate({
  loop,
  history,
  periods,
  readers,
  outputRange,
  windowS,
  yScale,
  every,
  exportHref,
  controls,
  headerControls,
  extra,
}: {
  loop: LoopOut;
  history: LoopTrace | undefined;
  periods: Record<string, number | null>;
  readers: ReaderSchema[];
  outputRange: [number, number] | null;
  windowS?: number;
  yScale?: ChartSettings["yScale"];
  every?: number;
  exportHref?: string;
  controls?: ReactNode;
  headerControls?: ReactNode;
  extra?: ReactNode;
}) {
  const reader = readers.find((r) => r.sources.some((s) => s.name === loop.channel.source));
  const fresh = useFreshness(channelKey(loop.channel), reader ? periods[reader.name] : undefined);
  const readerOffline = alarmLevel(null, {}, fresh) === "stale";
  return (
    <LoopPanel
      loop={loop}
      history={history}
      windowS={windowS}
      yScale={yScale}
      every={every}
      exportHref={exportHref}
      outputRange={outputRange}
      readerOffline={readerOffline}
      trends
      detail
      controls={controls}
      headerControls={headerControls}
      extra={extra}
    />
  );
});

/** The actuator's state/config/settings/commands, open inline below a controller's faceplate -- one card, no second frame, no disclosure. */
function ActuatorExtra({ schema, state }: { schema: ActuatorSchema; state: Parameters<typeof Actuator>[0]["state"] }) {
  return (
    <section className="fb-loop-law">
      <h4 className="fb-section-title" title={schema.description || undefined}>actuator: state &amp; commands</h4>
      <Actuator schema={schema} state={state} bare omitFields={ACTUATOR_EXTRA_OMIT} />
    </section>
  );
}
const ACTUATOR_EXTRA_OMIT = ["demand", "output_range"];

export interface ControllersProps extends ChartSettings {
  /** One actuator only (`#/controllers/{name}`). */
  name?: string | null;
}

/**
 * One card per actuator (DESIGN-SPEC §3.4/§3.5, merged): with a controller
 * (a loop is always bound to exactly one actuator) the card is the loop
 * faceplate, its actuator's state/config/settings/commands collapsed below;
 * without one it is the actuator card as `#/actuators` showed, plus a
 * primary "Add controller" button that opens the stepper with this actuator
 * preselected. Live from `/ws/loops` and the actuator-state store, with a
 * dialog to add a controller to any actuator.
 */
export function Controllers({ name = null, ...charts }: ControllersProps) {
  const { windowS, yScale, every } = charts;
  const rig = useRig();
  const stored = useRecordingExports();
  const rigSchema = useRigSchema();
  const sources = useSources();
  const { loops, history, status } = useLoops(3600, every);
  const { states: actuatorStates } = useActuatorStates();
  const periods = useReaderPeriods();
  const readers = useMemo(() => Object.values(rigSchema.data?.readers ?? {}), [rigSchema.data]);
  const allActuators = useMemo(() => Object.values(rigSchema.data?.actuators ?? {}), [rigSchema.data]);
  const labels = useMemo<Labels>(
    () => ({
      actuators: Object.fromEntries(allActuators.map((a) => [a.name, a.label])),
      sources: Object.fromEntries((sources.data ?? []).map((s) => [s.name, s.label])),
    }),
    [allActuators, sources.data],
  );
  const loopSchema = useQuery(() => rig.loopSchema(), [rig]);
  const [adding, setAdding] = useState(false);
  const [addingFor, setAddingFor] = useState<ActuatorChoice | null>(null);
  // The stream never says a loop is gone: hide one we removed until the stream sends a new object for that name (re-created).
  const [removed, setRemoved] = useState<Record<string, LoopOut>>({});
  const [created, setCreated] = useState<Record<string, LoopOut>>({});

  const all = { ...created, ...loops };
  // A loop is named after the actuator it drives, so one filter picks out the single card for a detail route.
  const shownActuators = name === null ? allActuators : allActuators.filter((a) => a.name === name);
  const loopOf = (actuatorName: string): LoopOut | undefined => {
    const l = all[actuatorName];
    return l && removed[l.name] !== l ? l : undefined;
  };
  // What the panels' controls report back; one stable callback so the controls need not re-render per tick.
  const latest = useRef(all);
  latest.current = all;
  const onEvent = useCallback(
    (loopName: string, kind: "changed" | "removed") => {
      if (kind === "removed") {
        const gone = latest.current[loopName];
        if (gone) setRemoved((r) => ({ ...r, [loopName]: gone }));
        setCreated((c) => {
          const { [loopName]: _gone, ...rest } = c;
          void _gone;
          return rest;
        });
      }
      loopSchema.refresh();
    },
    [loopSchema.refresh],
  );

  const openAdd = useCallback((actuator?: ActuatorChoice) => {
    setAddingFor(actuator ?? null);
    setAdding(true);
  }, []);

  const toolbar = (
    <PageBar end={<ChartControls {...charts} unit={shownActuators.length === 1 ? loopOf(shownActuators[0]!.name)?.channel.unit : undefined} />}>
      {name === null ? (
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => openAdd()} data-testid="add-loop">
          Add controller
        </Button>
      ) : (
        <Crumbs items={[{ label: "controllers", href: hashFor("controllers") }, { label: name }]} />
      )}
      {loopSchema.error && <Typography color="error">{loopSchema.error.message}</Typography>}
    </PageBar>
  );
  const closeDialog = useCallback(() => setAdding(false), []);
  const onCreated = useCallback(
    (loop: LoopOut) => {
      setAdding(false);
      setCreated((c) => ({ ...c, [loop.name]: loop }));
      setRemoved((r) => {
        const { [loop.name]: _gone, ...rest } = r;
        void _gone;
        return rest;
      });
      loopSchema.refresh();
    },
    [loopSchema.refresh],
  );
  const dialog = <AddLoopDialog open={adding} schema={loopSchema.data} labels={labels} initialActuator={addingFor} onClose={closeDialog} onCreated={onCreated} />;

  if (name !== null && shownActuators.length === 0)
    return status === "connecting" ? <Typography color="text.secondary">loading…</Typography> : <Alert severity="warning">No controller named {name}.</Alert>;
  return (
    <>
      {toolbar}
      {name === null && <SectionHead icon={PAGE_ICONS.controllers} title="Controllers" count={shownActuators.length} />}
      {shownActuators.length === 0 &&
        (status === "connecting" ? (
          <StateBlock state="loading" message="Loading controllers…" />
        ) : (
          <StateBlock state="empty" message="No controllers yet." action={{ label: "Add controller", onClick: () => openAdd() }} />
        ))}
      {/* Three cards abreast on a very wide screen (DESIGN-SPEC §3.4/§7 B-6), one per row otherwise. */}
      <div className="grid">
        {shownActuators.map((a) => {
          const l = loopOf(a.name);
          const state = actuatorStates[a.name];
          if (l) {
            const tag = typeof l.law?.tag === "string" ? l.law.tag : null;
            return (
              <div key={a.name} className={(name === null ? "c12 xl4" : "c12") + " controller-cell"}>
                <LoopFaceplate
                  loop={l}
                  history={history[l.name]}
                  periods={periods}
                  readers={readers}
                  windowS={windowS}
                  yScale={yScale}
                  every={every}
                  exportHref={stored.ticks(l.name)}
                  outputRange={state?.output_range ?? null}
                  controls={<LoopSetpointControl name={l.name} unit={l.channel.unit} mode={l.mode} tag={tag} onEvent={onEvent} />}
                  headerControls={<LoopStopControl name={l.name} mode={l.mode} onEvent={onEvent} />}
                  extra={<ActuatorExtra schema={a} state={state} />}
                />
              </div>
            );
          }
          return (
            <div key={a.name} className={(name === null ? "c12 xl4" : "c12") + " controller-cell"}>
              <Actuator schema={a} state={state} />
              <Button
                variant="contained"
                fullWidth
                startIcon={<AddIcon />}
                sx={{ mt: 1 }}
                onClick={() => openAdd(loopSchema.data?.actuators.find((c) => c.name === a.name))}
                data-testid={`add-controller-${a.name}`}
              >
                Add controller
              </Button>
            </div>
          );
        })}
      </div>
      {dialog}
    </>
  );
}
