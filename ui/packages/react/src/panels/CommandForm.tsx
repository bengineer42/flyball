import type { CommandSchema } from "@flyball/client";
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

/** One command: its description, a form for its arguments (or just a button), and the last outcome. */
export function CommandForm({ tag, command, onRun, busy, result, form }: CommandFormProps) {
  return (
    <section className="fb-command">
      <header>
        <h4>
          {humanise(tag)} <code className="fb-tag">{tag}</code>
        </h4>
        {command.description && <p className="fb-muted">{command.description}</p>}
      </header>
      {isEmpty(command.arguments) ? (
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void onRun({})}>
          {humanise(tag)}
        </button>
      ) : (
        <SchemaForm schema={command.arguments} onSubmit={onRun} submitLabel={humanise(tag)} disabled={busy ?? false} form={form} />
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
