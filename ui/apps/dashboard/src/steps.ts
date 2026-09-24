/**
 * Reading a program's normalised document against the schemas the runner
 * publishes: which command a step runs, that command's argument schema,
 * and how to say a duration or a rate in words. No rendering.
 */
import { deref, type JsonSchema, type ProgramCheck } from "@flyball/client";

/** One step of `ProgramCheck.normalised`: the command with its arguments, and any modifiers beside it. */
export interface NormalisedStep {
  command: { type: string; [arg: string]: unknown };
  [modifier: string]: unknown;
}

export interface NormalisedProgram {
  name?: string;
  description?: string;
  steps: NormalisedStep[];
}

/** The normalised document when the check produced one. */
export function normalisedOf(check: ProgramCheck | undefined): NormalisedProgram | null {
  const n = check?.normalised as Partial<NormalisedProgram> | null | undefined;
  if (!n || !Array.isArray(n.steps)) return null;
  const steps = n.steps.filter((s): s is NormalisedStep => Boolean(s && typeof s === "object" && s.command && typeof s.command === "object" && typeof (s.command as { type?: unknown }).type === "string"));
  return { name: typeof n.name === "string" ? n.name : undefined, description: typeof n.description === "string" ? n.description : undefined, steps };
}

/** The argument schema of `tag` from `GET /api/programs/commands`: the discriminator's mapping, else the variant whose `type` is that const. */
export function commandSchemaFor(commands: JsonSchema | undefined, tag: string): JsonSchema | undefined {
  if (!commands) return undefined;
  const ref = commands.discriminator?.mapping?.[tag];
  if (ref) return deref({ $ref: ref }, commands);
  const variants = [...(commands.oneOf ?? []), ...(commands.anyOf ?? []), ...Object.values(commands.$defs ?? {})];
  return variants.map((v) => deref(v, commands)).find((v) => v.properties?.type?.const === tag);
}

/** What the file-dialect schema (`GET /api/programs/schema`) says a command does: the step variant keyed by `tag`. */
export function commandDescription(programSchema: JsonSchema | undefined, tag: string): string | undefined {
  const items = programSchema?.properties?.steps?.items;
  const step = Array.isArray(items) ? items[0] : items;
  const variants = [...(step?.oneOf ?? []), ...(step?.anyOf ?? [])].map((v) => deref(v, programSchema!));
  const match = variants.find((v) => v.required?.includes(tag) || (v.properties && tag in v.properties));
  return match?.description ?? match?.properties?.[tag]?.description;
}

const UNIT_S: Record<string, number> = { nanoseconds: 1e-9, microseconds: 1e-6, milliseconds: 1e-3, seconds: 1, minutes: 60, hours: 3600, days: 86400 };

/** A duration schema: the `Duration` union (unit keys or a bare number of seconds) or the object half of it. */
export function isDurationSchema(schema: JsonSchema | undefined): boolean {
  if (!schema) return false;
  if (schema.title === "Duration") return true;
  const has = (s: JsonSchema) => Boolean(s.properties && "seconds" in s.properties && "minutes" in s.properties);
  return has(schema) || Boolean(schema.anyOf?.some(has)) || Boolean(schema.oneOf?.some(has));
}

/** Seconds from a duration value: a number, or `{seconds: 90, nanoseconds: 5}` summed. Null when it is neither. */
export function durationSeconds(value: unknown): number | null {
  if (typeof value === "number") return value;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  let total = 0;
  let any = false;
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    const f = UNIT_S[k];
    if (f === undefined || typeof v !== "number") return null;
    total += v * f;
    any = true;
  }
  return any ? total : null;
}

const trim = (n: number, digits: number) => n.toFixed(digits).replace(/\.?0+$/, "");

/** `90` → "1 min 30 s"; `0.25` → "250 ms"; `3600` → "1 h"; `90000` → "1 d 1 h". */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds)) return String(seconds);
  if (seconds < 0) return `-${formatDuration(-seconds)}`;
  if (seconds === 0) return "0 s";
  if (seconds < 1) return seconds < 1e-3 ? `${trim(seconds * 1e6, 1)} µs` : `${trim(seconds * 1e3, 1)} ms`;
  const parts: string[] = [];
  let rest = seconds;
  for (const [label, size] of [["d", 86400], ["h", 3600], ["min", 60]] as const) {
    const n = Math.floor(rest / size);
    if (n) {
      parts.push(`${n} ${label}`);
      rest -= n * size;
    }
  }
  if (rest > 1e-9 || parts.length === 0) parts.push(`${trim(rest, 3)} s`);
  return parts.slice(0, 2).join(" ");
}

const PER: Record<string, string> = { second: "s", seconds: "s", minute: "min", minutes: "min", hour: "h", hours: "h", day: "d", days: "d" };

/**
 * A rate as "2 %RH/min": `{per_minute: 2}` / `{"per-minute": 2}` / `{per: "minute", value: 2}`,
 * with the schema's unit in front of the slash. Null when the value is not one of those shapes.
 */
export function formatRate(value: unknown, unit: string | undefined): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const entries = Object.entries(value as Record<string, unknown>);
  const say = (n: number, per: string) => (unit ? `${trim(n, 4)} ${unit}/${per}` : `${trim(n, 4)} per ${per}`);
  if (entries.length === 1) {
    const [k, v] = entries[0]!;
    const m = /^per[_-]?(\w+)$/.exec(k);
    if (m && typeof v === "number" && PER[m[1]!]) return say(v, PER[m[1]!]!);
  }
  const rec = value as { per?: unknown; value?: unknown };
  if (typeof rec.per === "string" && typeof rec.value === "number" && PER[rec.per]) return say(rec.value, PER[rec.per]!);
  return null;
}

/** The title an enum member carries, from `oneOf: [{const, title}]`, or `enumNames`/`x-enumNames`; else null. */
export function enumTitle(schema: JsonSchema | undefined, value: unknown): string | null {
  if (!schema) return null;
  const variants = [...(schema.oneOf ?? []), ...(schema.anyOf ?? [])];
  const hit = variants.find((v) => "const" in v && v.const === value);
  if (hit?.title) return hit.title;
  const names = (schema.enumNames ?? schema["x-enumNames"]) as unknown;
  if (schema.enum && Array.isArray(names)) {
    const i = schema.enum.indexOf(value);
    if (i >= 0 && typeof names[i] === "string") return names[i] as string;
  }
  return null;
}

/** "3 steps: wait, ramp, settle" for a list row; the first few commands, then "…". */
export function stepsSummary(program: NormalisedProgram, max = 5): string {
  const n = program.steps.length;
  const tags = program.steps.map((s) => s.command.type);
  const shown = tags.slice(0, max).join(", ");
  return `${n} step${n === 1 ? "" : "s"}${n ? `: ${shown}${tags.length > max ? "…" : ""}` : ""}`;
}
