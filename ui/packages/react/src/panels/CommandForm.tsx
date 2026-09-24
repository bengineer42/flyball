import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import type { Address, CommandSchema, Interrupted, JsonSchema, SignalSchema, Value } from "@flyball/client";
import { deviceOf, formatValue, humanise, isEmpty, linkedSignal, RigError } from "@flyball/client";
import { SchemaForm, type SchemaFormProps } from "../form/SchemaForm.js";
import { formatTagged } from "../form/tagged.js";
import { useTelemetry } from "../provider.js";
import { useLatestValue } from "../store/hooks.js";
import { ValueView } from "./ValueView.js";

/** A value by its field's schema: a tagged union (`BlendFlow`) as `Absolute · 1 L/min · clamp`, else `formatValue`. */
function describeValue(value: unknown, field: JsonSchema | undefined, root: JsonSchema): string {
  return (field && formatTagged(value, field, root)) ?? formatValue(value, field);
}

export interface CommandFormProps {
  name: string;
  command: CommandSchema;
  onRun(args: Record<string, unknown>): Promise<unknown>;
  busy?: boolean;
  /** This browser may run commands (`AuthState.canOperate`, or a dashboard's `canWrite`); default true. False disables the form and its button, still shown. */
  canOperate?: boolean;
  result?: { result?: unknown; interrupted?: Interrupted[]; error?: Error; at: number };
  /** The RJSF form to render with (a theme's `Form`); default `@rjsf/core`. */
  form?: SchemaFormProps["form"];
  /**
   * The device this command belongs to: looks up `last.<command>` for the "ran
   * at … with …" line. Omitted (a simulation command, say) drops that line.
   */
  device?: string;
  /** The device's current `mode` value, to highlight the command that would put it there. */
  currentMode?: Value;
  /**
   * The device's signals by path (`DeviceSchema.signals`): tells a linked
   * argument that sets a demand from one that sets a setting, for the
   * "refused while a controller regulates" check. Omitted, only `mode` and
   * `writes` count.
   */
  signals?: Record<string, SignalSchema>;
}

/** An error as the person should read it: a rig's refusal is its `detail`, word for word (it names what to do). */
export const errorText = (error: unknown): string => (error instanceof RigError ? error.detail : error instanceof Error ? error.message : String(error));

/**
 * Whether running the command changes what drives the device -- a `mode`,
 * a `writes`, or an argument linked to a demand -- and so is refused while a
 * controller regulates one of the device's signals, unless it `interrupts`
 * (the rig then puts that controller in manual). A setting's command (a
 * blend flow) is not what a controller drives.
 */
export function commandDrives(command: CommandSchema, device?: string, signals?: Record<string, SignalSchema>): boolean {
  if (command.mode !== null && command.mode !== undefined) return true;
  if ((command.writes ?? []).length > 0) return true; // `?? []`: a runner from before `writes`
  if (!device || !signals) return false;
  return linkedArguments(command.arguments).some(({ address }) => signals[address.slice(device.length + 1)]?.role === "demand");
}

/** The names of the controllers regulating a signal of `device` now (a controller is named by its output), live. */
export function useRegulating(device: string | undefined): string[] {
  const store = useTelemetry();
  const subscribe = useCallback((cb: () => void) => (device === undefined ? () => undefined : store.subscribeController(null, cb, 250)), [store, device]);
  const names = useSyncExternalStore(subscribe, () =>
    device === undefined
      ? ""
      : Object.values(store.controllers())
          .filter((c) => c.mode === "regulating" && deviceOf(c.output_signal) === device)
          .map((c) => c.name)
          .sort()
          .join("\n"),
  );
  useEffect(() => {
    if (device !== undefined) void store.seedControllers();
  }, [store, device]);
  return useMemo(() => (names ? names.split("\n") : []), [names]);
}

/** A value as a key for "did it change": objects with their keys sorted, so a form's reordering is no edit. */
function stableKey(value: unknown): string {
  return JSON.stringify(value, (_k, v: unknown) =>
    v && typeof v === "object" && !Array.isArray(v) ? Object.fromEntries(Object.entries(v as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b))) : v,
  );
}

/**
 * Of a submitted form, only what the person edited: an argument whose value
 * differs from what the form opened with (the prefilled current value, or
 * the schema's default). An argument left as it opened is not sent, so the
 * rig keeps it where it is -- a demand group's untouched members are not
 * written, and a default the method has already is not repeated.
 */
export function editedArguments(data: Record<string, unknown>, opened: Record<string, unknown>, schema: JsonSchema): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(data)) {
    if (value === undefined) continue;
    const field = schema.properties?.[name] as JsonSchema | undefined;
    const baseline = name in opened ? opened[name] : field?.default;
    if (baseline !== undefined && stableKey(baseline) === stableKey(value)) continue;
    out[name] = value;
  }
  return out;
}

/** The one argument's name, when a command takes exactly one -- the common "ask for a single number" shape (`demand`, `set_setpoint`, ...). */
function onlyArgument(schema: JsonSchema): string | null {
  const keys = Object.keys(schema.properties ?? {});
  return keys.length === 1 ? keys[0]! : null;
}

/** Every argument linked to a signal (`x-signal`), in schema order: what to prefill and show a readback for. */
export function linkedArguments(schema: JsonSchema): Array<{ name: string; field: JsonSchema; address: Address }> {
  const out: Array<{ name: string; field: JsonSchema; address: Address }> = [];
  for (const [name, field] of Object.entries(schema.properties ?? {})) {
    const address = linkedSignal(field);
    if (address) out.push({ name, field, address });
  }
  return out;
}

/** One linked argument's live readback, beside the form: `Flow · now 12.3 L/min`. */
function LinkedReadback({ name, field, address, root }: { name: string; field: JsonSchema; address: Address; root: JsonSchema }) {
  const live = useLatestValue(address);
  if (!live) return null;
  return (
    <span className="fb-muted fb-command-link" title={address}>
      {field.title || humanise(name)} · now {describeValue(live.value, field, root)}
    </span>
  );
}

/** `last.<command>` (`{args, at}`), live: when the command last ran and with what. */
function LastRan({ device, name, label, schema }: { device: string; name: string; label: string; schema: JsonSchema }) {
  const live = useLatestValue(`${device}.last.${name}`);
  const value = live?.value;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const { args, at } = value as { args?: Record<string, unknown>; at?: number };
  if (typeof at !== "number") return null;
  const argsText =
    args && Object.keys(args).length
      ? Object.entries(args)
          .map(([k, v]) => `${k}=${describeValue(v, schema.properties?.[k] as JsonSchema | undefined, schema)}`)
          .join(", ")
      : "no arguments";
  return (
    <p className="fb-muted fb-command-last">
      {label} ran at {new Date(at / 1e6).toLocaleTimeString()} with {argsText}
    </p>
  );
}

/**
 * One command: a heading (its description on a hover hint, not as body
 * text -- every command on every device shares this, sim commands
 * included), what it does to the device (its `mode`, whether it interrupts
 * a driving controller), a form for its arguments (or just a button), when
 * it last ran, and the last outcome. A single-argument command (`demand`'s
 * `demand`, say) would otherwise repeat its name three times -- the
 * heading, the field's own label, the submit button -- so that one field
 * goes unlabelled (the heading already named it; the widget's unit suffix
 * still says what unit) and the button reads "Set". A linked argument
 * (`x-signal`) is prefilled from the store's current value for that signal
 * (left blank again, the rig keeps it where it is) and shows the signal's
 * live readback beside the form. Only the arguments the person changed are
 * sent (`editedArguments`). A command that drives the device and does not
 * interrupt says it is refused while a controller regulates the device,
 * and its button is held until that controller is in manual.
 */
export function CommandForm({ name, command, onRun, busy, result, form, device, currentMode, signals, canOperate = true }: CommandFormProps) {
  const only = onlyArgument(command.arguments);
  const args = only
    ? { ...command.arguments, properties: { ...command.arguments.properties, [only]: { ...command.arguments.properties![only]!, title: "" } } }
    : command.arguments;
  const label = humanise(name);
  // The raw name beside the heading is only worth showing when it carries something the
  // heading doesn't: `demand` -> "Demand" is a bare recapitalisation, so the chip would just
  // repeat the heading in lowercase; `set_flows` -> "Set flows" hides the underscore a caller
  // scripting against the API would need, so the chip earns its place there.
  const showName = label.toLowerCase() !== name.toLowerCase();
  const store = useTelemetry();
  const linked = useMemo(() => linkedArguments(command.arguments), [command.arguments]);
  // A one-time snapshot at mount: the operator types over it to change it, and the schema
  // marks every linked argument optional, so leaving it blank keeps the signal where it is.
  const initial = useMemo(
    () => Object.fromEntries(linked.map((a) => [a.name, store.latestValue(a.address)?.value]).filter(([, v]) => v !== undefined)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [linked, store],
  );
  const hasMode = command.mode !== null && command.mode !== undefined;
  const active = hasMode && currentMode !== undefined && command.mode === currentMode;
  // Refused while a controller regulates the device, unless it interrupts: said up front, and the button held.
  const regulating = useRegulating(device);
  const refusedBy = !command.interrupts && regulating.length > 0 && commandDrives(command, device, signals) ? regulating : [];
  const refusal = refusedBy.length > 0 ? `refused while ${refusedBy.join(", ")} regulates: put it in manual first` : null;
  const run = (data: Record<string, unknown>) => onRun(editedArguments(data, initial, command.arguments));
  return (
    <section className={`fb-command${active ? " fb-command-active" : ""}`}>
      <header>
        <h4 title={command.description || undefined}>
          {label} {showName && <code className="fb-tag">{name}</code>}
          {command.description && (
            <span className="fb-info" title={command.description} aria-label={`${label}: ${command.description}`}>
              ⓘ
            </span>
          )}
        </h4>
        {refusal && (
          <p className="fb-muted fb-command-refused" data-testid="command-refused">
            {refusal}
          </p>
        )}
        {(hasMode || command.interrupts) && (
          <p className="fb-muted fb-command-effect">
            {hasMode && <>→ mode {formatValue(command.mode)}</>}
            {hasMode && command.interrupts && " · "}
            {command.interrupts && "interrupts a driving controller"}
          </p>
        )}
      </header>
      {isEmpty(command.arguments) ? (
        <button type="button" className="btn btn-primary" disabled={busy || !canOperate || refusal !== null} title={refusal ?? undefined} onClick={() => void onRun({})}>
          {label}
        </button>
      ) : (
        <>
          <SchemaForm schema={args} value={initial} onSubmit={run} submitLabel={only ? "Set" : label} disabled={(busy ?? false) || !canOperate || refusal !== null} form={form} />
          {linked.length > 0 && (
            <div className="fb-command-links">
              {linked.map((a) => (
                <LinkedReadback key={a.name} {...a} root={command.arguments} />
              ))}
            </div>
          )}
        </>
      )}
      {device && <LastRan device={device} name={name} label={label} schema={command.arguments} />}
      {result?.error && <div className="fb-error">{errorText(result.error)}</div>}
      {result && !result.error && (
        <div className="fb-result">
          <div className="fb-result-head">
            <span>returned</span>
            <time className="fb-muted">{new Date(result.at).toLocaleTimeString()}</time>
          </div>
          {result.result === undefined || result.result === null ? (
            <span className="fb-muted">done</span>
          ) : (
            <ValueView value={result.result} />
          )}
          {result.interrupted && result.interrupted.length > 0 && (
            <div className="fb-muted" data-testid="interrupted">
              put {result.interrupted.map((i) => i.controller).join(", ")} in manual
            </div>
          )}
        </div>
      )}
    </section>
  );
}
