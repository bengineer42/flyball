import { Chip, Link, Paper, Stack, Typography } from "@mui/material";
import { fields, formatValue, humanise, unwrapNullable, deref, type JsonSchema } from "@flyball/client";
import { ValueView } from "@flyball/react";
import { hrefFor } from "../router.js";
import { channelRefOf, commandDescription, commandSchemaFor, durationSeconds, enumTitle, formatDuration, formatRate, isDurationSchema, type NormalisedProgram, type NormalisedStep } from "../steps.js";

/** A value the way its argument schema says: durations in words, rates with their unit, channels as links, enums by title. */
function ArgValue({ value, schema, root }: { value: unknown; schema: JsonSchema | undefined; root: JsonSchema | undefined }) {
  if (value === null || value === undefined) return <span className="fb-muted">—</span>;
  const resolved = schema && root ? deref(unwrapNullable(schema).inner, root) : schema;
  // Durations and rates are recognised by their shape as well as their schema: `{seconds: 90}`, `{per_minute: 2}`.
  // A pace may be either (a speed, or the time the whole ramp takes), so the value decides.
  if (typeof value === "object") {
    const r = formatRate(value, resolved?.unit);
    if (r) return <>{r}</>;
    const s = durationSeconds(value);
    if (s !== null) return <>{formatDuration(s)}</>;
  } else if (typeof value === "number" && isDurationSchema(resolved)) {
    return <>{formatDuration(value)}</>;
  }
  const channel = channelRefOf(value);
  if (channel) {
    const href = hrefFor({ kind: "channel", name: channel.source, measurand: channel.measurand });
    const label = `${channel.source}.${channel.measurand}`;
    return href ? (
      <Link href={href} underline="hover">
        {label}
      </Link>
    ) : (
      <>{label}</>
    );
  }
  const title = enumTitle(resolved, value);
  if (title) return <>{title}</>;
  if (typeof value === "number") return <>{formatValue(value, resolved)}</>;
  if (typeof value === "boolean") return <>{value ? "yes" : "no"}</>;
  if (typeof value === "string") return <>{value}</>;
  if (Array.isArray(value)) {
    const item = Array.isArray(resolved?.items) ? resolved?.items[0] : resolved?.items;
    return (
      <>
        {value.map((v, i) => (
          <span key={i} className="fb-array-item">
            <ArgValue value={v} schema={item} root={root} />
          </span>
        ))}
      </>
    );
  }
  if (typeof value === "object" && resolved?.properties && root) {
    return <ArgRows value={value as Record<string, unknown>} schema={resolved} root={root} />;
  }
  return <ValueView value={value} />;
}

/** One labelled row per field the value has, in the schema's order first, then anything the schema does not name. */
function ArgRows({ value, schema, root }: { value: Record<string, unknown>; schema: JsonSchema | undefined; root: JsonSchema | undefined }) {
  const known = schema && root ? fields(schema, root) : [];
  const names = new Set(known.map(([n]) => n));
  const rows: Array<[string, JsonSchema | undefined]> = [
    ...known.filter(([n]) => n in value).map(([n, s]) => [n, s] as [string, JsonSchema]),
    ...Object.keys(value)
      .filter((n) => !names.has(n))
      .map((n) => [n, undefined] as [string, undefined]),
  ];
  if (rows.length === 0) return <span className="fb-muted">no arguments</span>;
  return (
    <dl className="fb-state">
      {rows.map(([name, field]) => (
        <div key={name} className="fb-state-row">
          <dt title={field?.description}>{field?.title ?? humanise(name)}</dt>
          <dd>
            <ArgValue value={value[name]} schema={field} root={root} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** A modifier beside the command, as "key: value" for a chip. */
const modifierText = (key: string, value: unknown): string => {
  const s = durationSeconds(value);
  if (s !== null && typeof value === "object") return `${key}: ${formatDuration(s)}`;
  const channel = channelRefOf(value);
  if (channel) return `${key}: ${channel.source}.${channel.measurand}`;
  if (value === true) return key;
  if (value === null || value === undefined) return key;
  if (typeof value === "object") return `${key}: ${JSON.stringify(value)}`;
  return `${key}: ${String(value)}`;
};

export interface ProgramStepsProps {
  program: NormalisedProgram;
  /** `GET /api/programs/commands`. Without it the arguments show unlabelled. */
  commands: JsonSchema | undefined;
  /** `GET /api/programs/schema`, for the commands' descriptions. */
  programSchema?: JsonSchema;
}

/** One card per step, read-only: the command, what it does, its arguments with units, and the modifiers as chips. */
export function ProgramSteps({ program, commands, programSchema }: ProgramStepsProps) {
  if (program.steps.length === 0) return <Typography color="text.secondary">no steps</Typography>;
  return (
    <Stack spacing={1}>
      {program.steps.map((step, i) => (
        <StepCard key={i} index={i} step={step} commands={commands} programSchema={programSchema} />
      ))}
    </Stack>
  );
}

function StepCard({ index, step, commands, programSchema }: { index: number; step: NormalisedStep; commands: JsonSchema | undefined; programSchema: JsonSchema | undefined }) {
  const { command: tag, ...args } = step.command;
  const { command: _c, ...modifiers } = step;
  void _c;
  const schema = commandSchemaFor(commands, tag);
  const description = schema?.description ?? commandDescription(programSchema, tag);
  const shortDescription = description?.split(/\n\s*\n/)[0]?.replace(/\s+/g, " ");
  return (
    <Paper sx={{ p: 2.25, display: "flex", flexDirection: "column", gap: 1.125 }} data-step={index + 1}>
      <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" useFlexGap>
        <Typography variant="body2" color="text.secondary" sx={{ minWidth: "1.5em", fontVariantNumeric: "tabular-nums" }}>
          {index + 1}.
        </Typography>
        <Typography fontWeight={600}>{schema?.title && !/Request$|Config$/.test(schema.title) ? schema.title : humanise(tag)}</Typography>
        <Chip label={tag} variant="outlined" sx={{ fontFamily: "monospace" }} />
        {Object.entries(modifiers).map(([k, v]) => (
          <Chip key={k} label={modifierText(k, v)} color="info" variant="outlined" title={`modifier ${k}`} />
        ))}
      </Stack>
      {shortDescription && (
        <Typography variant="body2" color="text.secondary" title={description}>
          {shortDescription}
        </Typography>
      )}
      <ArgRows value={args} schema={schema ? { ...schema, properties: Object.fromEntries(Object.entries(schema.properties ?? {}).filter(([k]) => k !== "command")) } : undefined} root={commands} />
    </Paper>
  );
}
