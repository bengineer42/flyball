import type { CommandSchema, JsonSchema } from "@flyball/client";
import { humanise, isEmpty } from "@flyball/client";
import { SchemaForm, type SchemaFormProps } from "../form/SchemaForm.js";
import { ValueView } from "./ValueView.js";

export interface CommandFormProps {
  tag: string;
  command: CommandSchema;
  onRun(args: Record<string, unknown>): Promise<unknown>;
  busy?: boolean;
  result?: { result?: unknown; error?: Error; at: number };
  /** The RJSF form to render with (a theme's `Form`); default `@rjsf/core`. */
  form?: SchemaFormProps["form"];
}

/** The one argument's name, when a command takes exactly one -- the common "ask for a single number" shape (`demand`, `set_reference`, ...). */
function onlyArgument(schema: JsonSchema): string | null {
  const keys = Object.keys(schema.properties ?? {});
  return keys.length === 1 ? keys[0]! : null;
}

/**
 * One command: a heading (its description on a hover hint, not as body
 * text -- every command on every device shares this, sim commands
 * included), a form for its arguments (or just a button), and the last
 * outcome. A single-argument command (`demand`'s `demand`, say) would
 * otherwise repeat its name three times -- the heading, the field's own
 * label, the submit button -- so that one field goes unlabelled (the
 * heading already named it; the widget's unit suffix still says what unit)
 * and the button reads "Set".
 */
export function CommandForm({ tag, command, onRun, busy, result, form }: CommandFormProps) {
  const only = onlyArgument(command.arguments);
  const args = only
    ? { ...command.arguments, properties: { ...command.arguments.properties, [only]: { ...command.arguments.properties![only]!, title: "" } } }
    : command.arguments;
  return (
    <section className="fb-command">
      <header>
        <h4 title={command.description || undefined}>
          {humanise(tag)} <code className="fb-tag">{tag}</code>
          {command.description && (
            <span className="fb-info" title={command.description} aria-label={`${humanise(tag)}: ${command.description}`}>
              ⓘ
            </span>
          )}
        </h4>
      </header>
      {isEmpty(command.arguments) ? (
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void onRun({})}>
          {humanise(tag)}
        </button>
      ) : (
        <SchemaForm schema={args} onSubmit={onRun} submitLabel={only ? "Set" : humanise(tag)} disabled={busy ?? false} form={form} />
      )}
      {result?.error && <div className="fb-error">{result.error.message}</div>}
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
        </div>
      )}
    </section>
  );
}
