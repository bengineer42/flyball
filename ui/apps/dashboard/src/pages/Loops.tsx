import { memo, useCallback, useMemo, useRef, useState } from "react";
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
import { LoopPanel, SchemaForm, useLoops, useQuery, useRig, useRigSchema, useSources } from "@flyball/react";
import type { ActuatorChoice, ChannelOut, FeedforwardConfig, JsonSchema, LawConfig, LoopOut, LoopSchema } from "@flyball/client";
import { Confirm } from "../Confirm.js";
import { useRecordingExports } from "../model.js";
import { TuningPicker } from "../TuningPicker.js";
import { ChartControls, type ChartSettings } from "../YScaleSelect.js";
import { PageBar } from "../PageBar.js";
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
 * Make a loop in four steps: the actuator, a channel it may take, the law,
 * and the feedforward that maps the setpoint into the actuator's unit
 * (defaulted from the units, as the rig would). Channels another loop
 * already regulates are disabled.
 */
/** Display names by identifier, for the pickers; a name not here is shown as itself. */
interface Labels {
  actuators: Record<string, string | null | undefined>;
  sources: Record<string, string | null | undefined>;
}

const AddLoopDialog = memo(function AddLoopDialog({ open, schema, labels, onClose, onCreated }: { open: boolean; schema: LoopSchema | undefined; labels?: Labels; onClose(): void; onCreated(loop: LoopOut): void }) {
  const actuatorLabel = (name: string) => labels?.actuators[name] ?? name;
  const sourceLabel = (name: string) => labels?.sources[name] ?? name;
  const rig = useRig();
  const [draft, setDraft] = useState<Draft>({ actuator: null, channel: null, lawChoice: "none", tuning: "", config: null, feedforward: null, isDefault: false });
  // Bumped to remount a SchemaForm with new initial values (it keeps its own form state after the first render).
  const [formKey, setFormKey] = useState(0);
  const [active, setActive] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const patch = (p: Partial<Draft>) => setDraft((d) => ({ ...d, ...p }));

  const driving = useMemo(() => {
    // actuator name → the loop it drives (a loop is named after its actuator).
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
      <DialogTitle>Add a loop</DialogTitle>
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
                          secondary={`${labels?.actuators[a.name] ? `${a.name} · ` : ""}${a.type} · ${taken ? `already drives loop ${taken}` : a.demand_unit ? `takes demands in ${a.demand_unit}` : "any channel"}`}
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
                            <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
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
                      {draft.config && <Chip label={`law set: ${String(draft.config.tag)}`} color="success" variant="outlined" sx={{ mb: 1 }} />}
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
                      No law: the loop starts in manual and cannot regulate until a tuning is given.
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
                    label="default loop (the one programs address when they name none)"
                  />
                </Stack>
              </StepContent>
            </Step>
          </Stepper>
        )}
        {error && (
          <Alert severity="error" onClose={() => setError(null)} sx={{ mt: 1.5 }}>
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

/**
 * The loop's own commands, in the panel's header: regulate at, manual, set
 * reference, remove. Takes primitives and a stable callback so that it does
 * not re-render on every tick (its text fields are MUI form controls, which
 * set state in an effect whenever they render in development).
 */
const LoopControls = memo(function LoopControls({ name, unit, mode, tag, onEvent }: { name: string; unit: string; mode: LoopOut["mode"]; tag: string | null; onEvent(name: string, kind: "changed" | "removed"): void }) {
  const rig = useRig();
  const [setpoint, setSetpoint] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const hasLaw = tag !== null && tag !== "open_loop";
  const regulating = mode === "regulating";

  const act = async (what: string, op: () => Promise<unknown>) => {
    setBusy(what);
    try {
      await op();
      setError(null);
      onEvent(name, "changed");
      return true;
    } catch (e) {
      setError(message(e));
      return false;
    } finally {
      setBusy(null);
    }
  };
  const value = setpoint.trim() === "" ? null : Number(setpoint);
  const valid = value !== null && Number.isFinite(value);

  // One field, one verb. Stopped: "Regulate at" hands control to the law at
  // this setpoint (a bumpless start). Regulating: "Move setpoint" changes the
  // target and leaves the law running as it is. Both write the same number;
  // what differs is whether control is being started or already on.
  const startLabel = hasLaw ? "Aim here and start the law (bumpless)" : tag === null ? "No law: give the loop a tuning first" : "Open loop: no law to regulate with";

  return (
    <Stack component="span" direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap sx={{ display: "inline-flex" }}>
      <TextField
        type="number"
        label="setpoint"
        value={setpoint}
        onChange={(e) => setSetpoint(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && valid && busy === null) {
            void act(regulating ? "reference" : "regulate", () =>
              regulating ? rig.setReference(name, value!) : rig.regulate(name, { at: value! }),
            );
          }
        }}
        inputProps={{ "aria-label": `setpoint ${name}`, step: "any", "data-testid": `regulate-at-${name}`, style: { width: "6em" } }}
        InputProps={{ endAdornment: <span style={{ fontSize: "0.8rem", opacity: 0.7 }}>{unit}</span> }}
        sx={{ "& .MuiInputBase-input": { py: 0.5 } }}
      />
      {regulating ? (
        <Tooltip title="Change the target; the law keeps running as it is">
          <span>
            <Button
              variant="contained"
              disabled={!valid || busy !== null}
              onClick={() => void act("reference", () => rig.setReference(name, value!))}
              data-testid={`set-reference-${name}`}
            >
              Move setpoint
            </Button>
          </span>
        </Tooltip>
      ) : (
        <Tooltip title={startLabel}>
          <span>
            <Button
              variant="contained"
              startIcon={<PlayArrowIcon />}
              disabled={!hasLaw || !valid || busy !== null}
              onClick={() => void act("regulate", () => rig.regulate(name, { at: value! }))}
              data-testid={`regulate-${name}`}
            >
              Regulate at
            </Button>
          </span>
        </Tooltip>
      )}
      <Tooltip title="Stop regulating: the actuator holds its last demand and takes commands directly">
        <span>
          <Button
            variant="outlined"
            color="error"
            startIcon={<StopIcon />}
            disabled={mode === "manual" || busy !== null}
            onClick={() => void act("manual", () => rig.manual(name))}
            data-testid={`manual-${name}`}
          >
            Stop
          </Button>
        </span>
      </Tooltip>
      <Tooltip title="Remove the loop from the rig">
        <span>
          <IconButton aria-label={`remove loop ${name}`} disabled={busy !== null} onClick={() => setConfirmRemove(true)}>
            <DeleteOutlineIcon fontSize="small" />
          </IconButton>
        </span>
      </Tooltip>
      <Confirm
        open={confirmRemove}
        title={regulating ? `${name} is regulating — stop it and remove?` : `Remove loop ${name}?`}
        text={
          regulating
            ? `Removing ${name} stops it first: the actuator keeps its last demand and takes commands directly. The channel is then free for another loop.`
            : `The channel is then free for another loop; the actuator stays attached.`
        }
        action={regulating ? "Stop and remove" : "Remove"}
        onClose={() => setConfirmRemove(false)}
        onConfirm={() => {
          void act("remove", () => rig.removeLoop(name)).then((ok) => {
            setConfirmRemove(false);
            if (ok) onEvent(name, "removed");
          });
        }}
      />
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ py: 0, width: "100%" }}>
          {error}
        </Alert>
      )}
    </Stack>
  );
});

export interface LoopsProps extends ChartSettings {
  /** One loop only (`#/loops/{name}`). */
  name?: string | null;
}

/** One `LoopPanel` per loop, live from `/ws/loops`, each with its controls; and a dialog to add one. */
export function Loops({ name = null, ...charts }: LoopsProps) {
  const { windowS, yScale, every } = charts;
  const rig = useRig();
  const stored = useRecordingExports();
  const rigSchema = useRigSchema();
  const sources = useSources();
  const { loops, history, status } = useLoops(3600, every);
  const labels = useMemo<Labels>(
    () => ({
      actuators: Object.fromEntries(Object.values(rigSchema.data?.actuators ?? {}).map((a) => [a.name, a.label])),
      sources: Object.fromEntries((sources.data ?? []).map((s) => [s.name, s.label])),
    }),
    [rigSchema.data, sources.data],
  );
  const loopSchema = useQuery(() => rig.loopSchema(), [rig]);
  const [adding, setAdding] = useState(false);
  // The stream never says a loop is gone: hide one we removed until the stream sends a new object for that name (re-created).
  const [removed, setRemoved] = useState<Record<string, LoopOut>>({});
  const [created, setCreated] = useState<Record<string, LoopOut>>({});

  const all = { ...created, ...loops };
  const shown = (name === null ? Object.values(all) : all[name] ? [all[name]!] : []).filter((l) => removed[l.name] !== l);
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

  const toolbar = (
    <PageBar end={<ChartControls {...charts} unit={shown[0]?.channel.unit} />}>
      {name === null ? (
        <Button variant="contained" startIcon={<AddIcon />} onClick={() => setAdding(true)} data-testid="add-loop">
          Add loop
        </Button>
      ) : (
        <Crumbs items={[{ label: "loops", href: hashFor("loops") }, { label: name }]} />
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
  const dialog = <AddLoopDialog open={adding} schema={loopSchema.data} labels={labels} onClose={closeDialog} onCreated={onCreated} />;

  if (name !== null && shown.length === 0)
    return status === "connecting" ? <Typography color="text.secondary">loading…</Typography> : <Alert severity="warning">No loop named {name}.</Alert>;
  return (
    <>
      {toolbar}
      {shown.length === 0 && (
        <Typography color="text.secondary">{status === "connecting" ? "loading…" : "This rig has no loops attached."}</Typography>
      )}
      {/* Two loops abreast on a wide screen, each a faceplate with its charts beside the readouts; one per row otherwise. */}
      <div className="grid">
        {shown.map((l) => (
          <div key={l.name} className={name === null ? "c12 xl6" : "c12"}>
          <LoopPanel
            loop={l}
            history={history[l.name]}
            windowS={windowS}
            yScale={yScale}
            every={every}
            exportHref={stored.ticks(l.name)}
            controls={<LoopControls name={l.name} unit={l.channel.unit} mode={l.mode} tag={typeof l.law?.tag === "string" ? l.law.tag : null} onEvent={onEvent} />}
          />
          </div>
        ))}
      </div>
      {dialog}
    </>
  );
}
