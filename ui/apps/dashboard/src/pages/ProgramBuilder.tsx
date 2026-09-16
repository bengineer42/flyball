/**
 * The structured half of the program editor: name and description, a palette
 * of the dialect's commands, and one card per step with the command's
 * arguments as a live `SchemaForm`. Controlled: the tree comes in as a prop
 * and every edit goes out through `onChange`; nothing here fetches.
 *
 * Drag and drop is native HTML5: a palette chip drops in as a new step, a
 * card's handle moves the step; the insertion point is drawn as a bar. The
 * buttons on each card (up, down, duplicate, insert after, delete) and
 * click-to-append on the palette do the same without a pointer.
 */
import { useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import { Alert, Box, Button, Chip, Divider, FormControl, IconButton, InputLabel, Menu, MenuItem, Paper, Select, Stack, TextField, Tooltip, Typography } from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import ArrowDownwardIcon from "@mui/icons-material/ArrowDownward";
import ArrowUpwardIcon from "@mui/icons-material/ArrowUpward";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import DragIndicatorIcon from "@mui/icons-material/DragIndicator";
import PlaylistAddIcon from "@mui/icons-material/PlaylistAdd";
import { Form as MuiForm } from "@rjsf/mui";
import { SchemaForm } from "@flyball/react";
import type { JsonSchema } from "@flyball/client";
import { argsOf, commandsOf, formShape, fromForm, inOrder, isRareUnit, modifiersOf, newStep, onDeviceChange, retarget, sameValue, splitStep, timeEntries, toForm, withTime, type CommandInfo, type DeviceCommands, type ProgramTree, type Step, type TimeField } from "../programDoc.js";

const COMMAND_TYPE = "application/x-flyball-command";
const STEP_TYPE = "application/x-flyball-step";

const dragged = (e: DragEvent): { command?: string; step?: number } => {
  const types = Array.from(e.dataTransfer.types);
  if (types.includes(COMMAND_TYPE)) return { command: e.dataTransfer.getData(COMMAND_TYPE) || undefined };
  if (types.includes(STEP_TYPE)) {
    const n = Number(e.dataTransfer.getData(STEP_TYPE));
    return { step: Number.isFinite(n) ? n : undefined };
  }
  return {};
};

/** Whether a drag over the list is one of ours (Chromium hides the data until drop; the types are visible). */
const ours = (e: DragEvent) => {
  const types = Array.from(e.dataTransfer.types);
  return types.includes(COMMAND_TYPE) || types.includes(STEP_TYPE);
};

export interface ProgramBuilderProps {
  tree: ProgramTree;
  onChange(tree: ProgramTree): void;
  /** `GET /api/programs/schema`; without it there is no palette and the steps show as raw JSON. */
  programSchema: JsonSchema | undefined;
  /** The rig's loop names, for the `loop` pick; undefined while unknown. */
  loops: string[] | undefined;
  /** The rig's devices and their commands, for a `command` step's picks; undefined while unknown. */
  devices?: DeviceCommands;
  /** Check errors by step index (the daemon counts from zero). */
  stepErrors: Record<number, string>;
  /** Bumped whenever the tree was replaced from outside (the text), so the forms re-read their values. */
  revision: number;
  /** Create mode: the name field is shown, and is what the program will be stored as. A stored program is renamed from the header instead. */
  nameEditable?: boolean;
}

export function ProgramBuilder({ tree, onChange, programSchema, loops, devices, stepErrors, revision, nameEditable }: ProgramBuilderProps) {
  const commands = useMemo(() => commandsOf(programSchema), [programSchema]);
  const modifierSchemas = useMemo(() => modifiersOf(programSchema), [programSchema]);
  const byTag = useMemo(() => Object.fromEntries(commands.map((c) => [c.tag, c])), [commands]);
  // Structural edits (insert, move, delete) remount the cards' forms; argument edits do not.
  const [structure, setStructure] = useState(0);
  const [dropAt, setDropAt] = useState<number | null>(null);
  const [dragging, setDragging] = useState<number | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const setSteps = (steps: Step[]) => {
    setStructure((n) => n + 1);
    onChange({ ...tree, steps });
  };
  const setField = (key: "name" | "description", value: string) => {
    const next: ProgramTree = { ...tree };
    if (value === "") {
      delete next[key];
      onChange(next);
    } else if (key in tree) {
      next[key] = value;
      onChange(next);
    } else {
      // a new key reads best before `steps`: name, then description, then the rest in their order
      const ordered: Record<string, unknown> = {};
      if (key === "name") ordered.name = value;
      else {
        if ("name" in tree) ordered.name = tree.name;
        ordered.description = value;
      }
      for (const [k, v] of Object.entries(tree)) if (!(k in ordered)) ordered[k] = v;
      onChange(ordered as ProgramTree);
    }
  };
  const steps = tree.steps;
  const insert = (at: number, step: Step) => setSteps([...steps.slice(0, at), step, ...steps.slice(at)]);
  const move = (from: number, to: number) => {
    if (from === to || from + 1 === to) return;
    const rest = steps.filter((_, i) => i !== from);
    const at = to > from ? to - 1 : to;
    setSteps([...rest.slice(0, at), steps[from]!, ...rest.slice(at)]);
  };
  const replace = (i: number, step: Step) => onChange({ ...tree, steps: steps.map((s, j) => (j === i ? step : s)) });

  // The insertion index for a pointer at `y` over the list: before the first card whose middle is below it.
  const indexAt = (y: number): number => {
    const cards = Array.from(listRef.current?.querySelectorAll<HTMLElement>("[data-step]") ?? []);
    for (const [i, card] of cards.entries()) {
      const r = card.getBoundingClientRect();
      if (y < r.top + r.height / 2) return i;
    }
    return cards.length;
  };
  const onDragOver = (e: DragEvent) => {
    if (!ours(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = Array.from(e.dataTransfer.types).includes(STEP_TYPE) ? "move" : "copy";
    const at = indexAt(e.clientY);
    if (at !== dropAt) setDropAt(at);
  };
  const onDrop = (e: DragEvent) => {
    if (!ours(e)) return;
    e.preventDefault();
    const at = indexAt(e.clientY);
    const { command, step } = dragged(e);
    setDropAt(null);
    setDragging(null);
    if (command !== undefined && byTag[command]) insert(at, newStep(byTag[command]!));
    else if (step !== undefined) move(step, at);
  };
  const onDragLeave = (e: DragEvent) => {
    // leaving the list altogether, not moving between its children
    if (!listRef.current?.contains(e.relatedTarget as Node | null)) setDropAt(null);
  };

  const name = typeof tree.name === "string" ? tree.name : tree.name === undefined ? "" : String(tree.name);
  const description = typeof tree.description === "string" ? tree.description : tree.description === undefined ? "" : String(tree.description);

  return (
    <Stack spacing={1.5}>
      {nameEditable && <TextField label="name" value={name} required onChange={(e) => setField("name", e.target.value)} inputProps={{ "aria-label": "program name" }} helperText="The program is stored under this name." fullWidth />}
      <TextField label="description" value={description} onChange={(e) => setField("description", e.target.value)} inputProps={{ "aria-label": "program description" }} multiline minRows={1} maxRows={6} fullWidth />
      {commands.length > 0 && <Palette commands={commands} onPick={(c) => insert(steps.length, newStep(c))} />}
      <Box
        ref={listRef}
        data-testid="step-list"
        onDragOver={onDragOver}
        onDrop={onDrop}
        onDragLeave={onDragLeave}
        sx={{ display: "flex", flexDirection: "column", gap: 1.5, minHeight: 56, position: "relative", borderRadius: 1, ...(dropAt !== null ? { outline: "1px dashed", outlineColor: "primary.main", outlineOffset: 4 } : {}) }}
      >
        {steps.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ p: 1.5, textAlign: "center", border: "1px dashed", borderColor: "divider", borderRadius: 1 }} data-testid="drop-empty">
            no steps yet — drop a command here, or click one above
          </Typography>
        )}
        {steps.map((step, i) => {
          const { tag, value, modifiers } = splitStep(step, commands);
          const command = tag ? byTag[tag] : undefined;
          return (
            <Box key={`${revision}-${structure}-${i}`} sx={{ position: "relative" }}>
              {dropAt === i && <DropBar />}
              <StepCard
                index={i}
                count={steps.length}
                tag={tag}
                value={value}
                modifiers={modifiers}
                command={command}
                commands={commands}
                modifierSchemas={modifierSchemas}
                programSchema={programSchema}
                loops={loops}
                devices={devices}
                error={stepErrors[i]}
                dragging={dragging === i}
                onDragStart={(e) => {
                  e.dataTransfer.setData(STEP_TYPE, String(i));
                  e.dataTransfer.effectAllowed = "move";
                  const card = (e.currentTarget as HTMLElement).closest("[data-step]") as HTMLElement | null;
                  if (card) e.dataTransfer.setDragImage(card, 24, 24);
                  setDragging(i);
                }}
                onDragEnd={() => {
                  setDragging(null);
                  setDropAt(null);
                }}
                onArgs={(data) => {
                  if (!tag) return;
                  const before = fromForm(toForm(value, command), command);
                  let args = fromForm(data, command);
                  if (sameValue(args, before)) return; // nothing the user changed (RJSF reports its initial state too)
                  if (tag === "command") {
                    // the command and args picks follow the device: a new one remounts the form with the new picks
                    const next = onDeviceChange(args, before);
                    args = next.args;
                    if (next.changed) setStructure((n) => n + 1);
                  }
                  // the composite time field is not the form's: carry it over as it is
                  const current = argsOf(value, command);
                  const time = Object.fromEntries(Object.entries(current).filter(([k]) => command?.time?.keys.includes(k)));
                  replace(i, { ...step, [tag]: inOrder({ ...args, ...time }, current) });
                }}
                onTime={(key, v) => {
                  if (!tag || !command?.time) return;
                  replace(i, { ...step, [tag]: withTime(argsOf(value, command), command.time, key, v) });
                }}
                onCommand={(next) => {
                  if (!programSchema) return;
                  setStructure((n) => n + 1);
                  replace(i, retarget(step, command, next, commands, programSchema));
                }}
                onModifier={(key, v) => {
                  const next: Step = { ...step };
                  if (v === undefined) delete next[key];
                  else next[key] = v;
                  replace(i, next);
                }}
                onUp={() => move(i, i - 1)}
                onDown={() => move(i, i + 2)}
                onDuplicate={() => insert(i + 1, JSON.parse(JSON.stringify(step)) as Step)}
                onInsertAfter={(c) => insert(i + 1, newStep(c))}
                onDelete={() => setSteps(steps.filter((_, j) => j !== i))}
              />
            </Box>
          );
        })}
        {dropAt === steps.length && steps.length > 0 && (
          <Box sx={{ position: "relative", height: 0 }}>
            <DropBar />
          </Box>
        )}
      </Box>
      {commands.length > 0 && (
        <Box>
          <AddStepButton commands={commands} onPick={(c) => insert(steps.length, newStep(c))} label="Add step" />
        </Box>
      )}
    </Stack>
  );
}

/** The insertion marker: a bar across the list at the drop point. */
function DropBar() {
  return <Box data-testid="drop-bar" sx={{ position: "absolute", left: 0, right: 0, top: -5, height: 3, borderRadius: 2, bgcolor: "primary.main", pointerEvents: "none", zIndex: 1 }} />;
}

/** The dialect's commands as draggable chips; a click appends. */
function Palette({ commands, onPick }: { commands: CommandInfo[]; onPick(c: CommandInfo): void }) {
  return (
    <Box data-testid="palette">
      <Typography variant="body2" color="text.secondary" sx={{ mb: 0.75 }}>
        Commands — drag one into the list, or click to add at the end
      </Typography>
      <Stack direction="row" spacing={0.75} useFlexGap flexWrap="wrap">
        {commands.map((c) => (
          <Tooltip key={c.tag} title={c.short ?? c.tag} placement="top">
            <Chip
              label={c.title}
              variant="outlined"
              color="primary"
              icon={<DragIndicatorIcon fontSize="small" />}
              onClick={() => onPick(c)}
              draggable
              data-command={c.tag}
              onDragStart={(e: DragEvent<HTMLDivElement>) => {
                e.dataTransfer.setData(COMMAND_TYPE, c.tag);
                e.dataTransfer.effectAllowed = "copy";
              }}
              sx={{ cursor: "grab", fontFamily: "inherit" }}
            />
          </Tooltip>
        ))}
      </Stack>
    </Box>
  );
}

/** A button opening a menu of the commands. */
function AddStepButton({ commands, onPick, label, icon, iconOnly, title }: { commands: CommandInfo[]; onPick(c: CommandInfo): void; label: string; icon?: ReactNode; iconOnly?: boolean; title?: string }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  return (
    <>
      {iconOnly ? (
        <Tooltip title={title ?? label}>
          <IconButton aria-label={label} onClick={(e) => setAnchor(e.currentTarget)}>
            {icon}
          </IconButton>
        </Tooltip>
      ) : (
        <Button variant="outlined" startIcon={icon ?? <AddIcon />} onClick={(e) => setAnchor(e.currentTarget)}>
          {label}
        </Button>
      )}
      <Menu open={Boolean(anchor)} anchorEl={anchor} onClose={() => setAnchor(null)}>
        {commands.map((c) => (
          <MenuItem
            key={c.tag}
            onClick={() => {
              setAnchor(null);
              onPick(c);
            }}
          >
            <Stack>
              <span>{c.title}</span>
              {c.short && (
                <Typography variant="caption" color="text.secondary">
                  {c.short}
                </Typography>
              )}
            </Stack>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}

/** "per_minute" → "per minute"; "minutes" → "over minutes" beside a rate group, "minutes" alone. */
const unitLabel = (key: string, beside: boolean) => (key.startsWith("per_") ? `per ${key.slice(4)}` : beside ? `over ${key}` : key);

/** A composite time field as one control: a number and the unit it is in; exactly one key goes into the tree. */
function TimeControl({ field, entries, onChange, idPrefix }: { field: TimeField; entries: Array<[string, unknown]>; onChange(key: string, value: number | undefined): void; idPrefix: string }) {
  const beside = field.groups.length > 1;
  const options = field.groups.flatMap((g) => g.keys.map((key) => ({ key, label: unitLabel(key, beside), rare: isRareUnit(key) })));
  const first = field.groups[0]?.keys ?? [];
  const fallback = first.find((k) => /minute/.test(k)) ?? first[0] ?? "seconds";
  const held = entries.length === 1 ? entries[0]![0] : undefined;
  const [unit, setUnit] = useState(held ?? fallback);
  useEffect(() => {
    if (held && held !== unit) setUnit(held);
  }, [held]); // eslint-disable-line react-hooks/exhaustive-deps
  const current = entries.length === 1 ? entries[0]![1] : undefined;
  const label = (
    <Tooltip title={field.description ?? ""}>
      <span>{field.title}</span>
    </Tooltip>
  );
  if (entries.length > 1) {
    return (
      <Alert severity="warning" sx={{ mt: 1.5, py: 0.75 }} data-testid="time-conflict">
        <Typography variant="body2" sx={{ mb: 0.75 }}>
          {field.title}: the file says it more than one way; the rig takes only one. Keep:
        </Typography>
        <Stack direction="row" spacing={0.75} useFlexGap flexWrap="wrap">
          {entries.map(([k, v]) => (
            <Chip key={k} label={`${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`} color="warning" variant="outlined" onClick={() => onChange(k, typeof v === "number" ? v : undefined)} data-testid="time-keep" />
          ))}
        </Stack>
      </Alert>
    );
  }
  const menu: ReactNode[] = [];
  options.filter((o) => !o.rare).forEach((o) => menu.push(<MenuItem key={o.key} value={o.key}>{o.label}</MenuItem>));
  if (options.some((o) => o.rare)) {
    menu.push(<Divider key="rare-divider" />);
    options.filter((o) => o.rare).forEach((o) =>
      menu.push(
        <MenuItem key={o.key} value={o.key} sx={{ color: "text.secondary", fontSize: "0.85em" }}>
          {o.label}
        </MenuItem>,
      ),
    );
  }
  return (
    <Stack direction="row" spacing={1} alignItems="flex-start" sx={{ mt: 1.5 }} data-testid="time-control">
      <TextField
        id={`${idPrefix}_${field.name}`}
        label={label}
        type="number"
        value={typeof current === "number" ? current : ""}
        inputProps={{ step: "any", min: 0, "aria-label": field.title }}
        onChange={(e) => onChange(unit, e.target.value === "" ? undefined : Number(e.target.value))}
        sx={{ width: "9em" }}
      />
      <FormControl size="small" sx={{ minWidth: "10em" }}>
        <InputLabel id={`${idPrefix}_${field.name}_unit-label`}>unit</InputLabel>
        <Select
          labelId={`${idPrefix}_${field.name}_unit-label`}
          id={`${idPrefix}_${field.name}_unit`}
          label="unit"
          value={options.some((o) => o.key === unit) ? unit : fallback}
          inputProps={{ "aria-label": `${field.title} unit` }}
          onChange={(e) => {
            const next = String(e.target.value);
            setUnit(next);
            if (typeof current === "number") onChange(next, current);
          }}
        >
          {menu}
        </Select>
      </FormControl>
    </Stack>
  );
}

interface StepCardProps {
  index: number;
  count: number;
  tag: string | undefined;
  value: unknown;
  modifiers: Record<string, unknown>;
  command: CommandInfo | undefined;
  commands: CommandInfo[];
  modifierSchemas: Record<string, JsonSchema>;
  programSchema: JsonSchema | undefined;
  loops: string[] | undefined;
  devices: DeviceCommands | undefined;
  error: string | undefined;
  dragging: boolean;
  onDragStart(e: DragEvent<HTMLElement>): void;
  onDragEnd(): void;
  onArgs(data: Record<string, unknown>): void;
  /** The composite time field said one way: `key: value`; `value` undefined clears it. */
  onTime(key: string, value: number | undefined): void;
  onCommand(c: CommandInfo): void;
  onModifier(key: string, value: unknown): void;
  onUp(): void;
  onDown(): void;
  onDuplicate(): void;
  onInsertAfter(c: CommandInfo): void;
  onDelete(): void;
}

function StepCard({ index, count, tag, value, modifiers, command, commands, modifierSchemas, programSchema, loops, devices, error, dragging, onDragStart, onDragEnd, onArgs, onTime, onCommand, onModifier, onUp, onDown, onDuplicate, onInsertAfter, onDelete }: StepCardProps) {
  // The form's value and its schema are fixed at mount (the key changes on structural edits); the form owns the edits after that.
  const [initial] = useState(() => toForm(value, command));
  const shape = useMemo(() => (command && programSchema ? formShape(command, programSchema, loops, devices, initial) : null), [command, programSchema, loops, devices, initial]);
  // The time control is the tree's, not the form's: it follows `value` on every render.
  const time = command?.time;
  const entries = useMemo(() => (time ? timeEntries(argsOf(value, command), time) : []), [time, value, command]);
  const [modifierAnchor, setModifierAnchor] = useState<HTMLElement | null>(null);
  const unusedModifiers = Object.keys(modifierSchemas).filter((k) => !(k in modifiers));
  return (
    <Paper data-step={index + 1} data-command={tag ?? ""} sx={{ p: 2.25, display: "flex", flexDirection: "column", gap: 1.5, opacity: dragging ? 0.5 : 1, ...(error ? { borderColor: "error.main" } : {}) }}>
      <Stack direction="row" alignItems="center" spacing={0.5} flexWrap="wrap" useFlexGap>
        <Tooltip title="Drag to reorder">
          <Box
            component="span"
            draggable
            onDragStart={onDragStart}
            onDragEnd={onDragEnd}
            aria-label={`drag step ${index + 1}`}
            data-testid="drag-handle"
            sx={{ display: "inline-flex", cursor: "grab", color: "text.secondary", touchAction: "none" }}
          >
            <DragIndicatorIcon fontSize="small" />
          </Box>
        </Tooltip>
        <Typography variant="body2" color="text.secondary" sx={{ minWidth: "1.5em", fontVariantNumeric: "tabular-nums" }}>
          {index + 1}.
        </Typography>
        {commands.length > 0 ? (
          <FormControl size="small" sx={{ minWidth: 140 }}>
            <InputLabel id={`step-${index}-command-label`}>command</InputLabel>
            <Select
              labelId={`step-${index}-command-label`}
              label="command"
              value={tag && commands.some((c) => c.tag === tag) ? tag : ""}
              inputProps={{ "aria-label": `step ${index + 1} command` }}
              onChange={(e) => {
                const next = commands.find((c) => c.tag === e.target.value);
                if (next && next.tag !== tag) onCommand(next);
              }}
            >
              {commands.map((c) => (
                <MenuItem key={c.tag} value={c.tag}>
                  {c.title}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        ) : (
          <Typography fontWeight={600}>{tag ?? "?"}</Typography>
        )}
        {command?.short && (
          <Tooltip title={command.description ?? command.short}>
            <Typography variant="body2" color="text.secondary" sx={{ flex: "1 1 12em", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {command.short}
            </Typography>
          </Tooltip>
        )}
        <Box sx={{ flexGrow: 1 }} />
        <Tooltip title="Move up">
          <span>
            <IconButton aria-label={`move step ${index + 1} up`} disabled={index === 0} onClick={onUp}>
              <ArrowUpwardIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip title="Move down">
          <span>
            <IconButton aria-label={`move step ${index + 1} down`} disabled={index >= count - 1} onClick={onDown}>
              <ArrowDownwardIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
        <Tooltip title="Duplicate">
          <IconButton aria-label={`duplicate step ${index + 1}`} onClick={onDuplicate}>
            <ContentCopyIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        {commands.length > 0 && <AddStepButton commands={commands} onPick={onInsertAfter} label={`insert step after ${index + 1}`} title="Insert a step after this one" icon={<PlaylistAddIcon fontSize="small" />} iconOnly />}
        <Tooltip title="Delete">
          <IconButton aria-label={`delete step ${index + 1}`} onClick={onDelete}>
            <DeleteOutlineIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Stack>
      {error && (
        <Alert severity="error" sx={{ py: 0 }} data-testid="step-error">
          {error}
        </Alert>
      )}
      {!tag && (
        <Alert severity="warning" sx={{ py: 0 }}>
          This step names no known command: {JSON.stringify(value ?? modifiers)}
        </Alert>
      )}
      {shape ? (
        <Box
          className="fb-step-form"
          sx={{
            // RJSF's MUI object template stacks one field per row; a step's few arguments sit side by side instead
            "& .MuiGrid-container": { flexWrap: "wrap", alignItems: "flex-start" },
            "& .MuiGrid-container > .MuiGrid-item": { flex: "1 1 11em", maxWidth: "26em", width: "auto", pt: "6px !important" },
            "& .MuiGrid-container > .MuiGrid-item:has(.MuiFormGroup-row)": { flexBasis: "100%", maxWidth: "none" },
            "& .MuiFormControl-root": { my: 0 },
            "& .fb-field-label": { display: "block", fontSize: "0.8rem", color: "text.secondary", mb: 0.375 },
            "& .MuiFormHelperText-root": { mt: 0.375 },
          }}
        >
          <SchemaForm schema={shape.schema} uiSchema={shape.uiSchema} value={initial} onChange={onArgs} idPrefix={`step${index + 1}`} form={MuiForm} />
          {time && <TimeControl field={time} entries={entries} onChange={onTime} idPrefix={`step${index + 1}`} />}
        </Box>
      ) : tag && !command ? (
        <Typography variant="body2" color="text.secondary" sx={{ fontFamily: "monospace" }}>
          {JSON.stringify(value)}
        </Typography>
      ) : null}
      {(Object.keys(modifiers).length > 0 || unusedModifiers.length > 0) && (
        <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
          {Object.entries(modifiers).map(([k, v]) => (
            <Chip key={k} label={`${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`} color="info" variant="outlined" onDelete={() => onModifier(k, undefined)} />
          ))}
          {unusedModifiers.length > 0 && (
            <>
              <Button size="small" onClick={(e) => setModifierAnchor(e.currentTarget)}>
                add modifier
              </Button>
              <Menu open={Boolean(modifierAnchor)} anchorEl={modifierAnchor} onClose={() => setModifierAnchor(null)}>
                {unusedModifiers.map((k) => (
                  <MenuItem
                    key={k}
                    onClick={() => {
                      setModifierAnchor(null);
                      onModifier(k, modifierSchemas[k]?.default ?? null);
                    }}
                  >
                    {k}
                  </MenuItem>
                ))}
              </Menu>
            </>
          )}
        </Stack>
      )}
      {Object.keys(modifiers).length > 0 && programSchema && (
        <SchemaForm
          schema={{ type: "object", properties: Object.fromEntries(Object.keys(modifiers).map((k) => [k, modifierSchemas[k] ?? {}])), $defs: programSchema.$defs }}
          value={modifiers}
          idPrefix={`step${index + 1}_modifiers`}
          onChange={(data) => {
            for (const k of Object.keys(modifiers)) if (JSON.stringify(data[k]) !== JSON.stringify(modifiers[k])) onModifier(k, data[k]);
          }}
          form={MuiForm}
        />
      )}
    </Paper>
  );
}
