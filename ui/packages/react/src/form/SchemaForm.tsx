/**
 * The keystone: JSON Schema in, validated JSON out. A thin wrapper over
 * react-jsonschema-form that teaches it the server's dialect -- the `unit`
 * key as a suffix, pydantic's `X | null` as an optional field, bounds as a
 * slider -- and nothing about any particular rig. The RJSF `Form` itself is a
 * prop, so an app can bring its own theme's.
 */

import Form, { type FormProps, type IChangeEvent } from "@rjsf/core";
import type { RJSFSchema, UiSchema } from "@rjsf/utils";
import validator from "@rjsf/validator-ajv8";
import type { JsonSchema } from "@flyball/client";
import { type ComponentType, useMemo, useState } from "react";
import { impliedUiSchema, simplifyNullables } from "./uiSchema.js";
import { widgets } from "./widgets.js";

export interface SchemaFormProps {
  schema: JsonSchema;
  /** Initial values. */
  value?: Record<string, unknown>;
  /** Called with validated data on submit. */
  onSubmit(data: Record<string, unknown>): void | Promise<unknown>;
  submitLabel?: string;
  disabled?: boolean;
  /** Extra RJSF `uiSchema`, merged over what the schema implies. */
  uiSchema?: UiSchema;
  /** The RJSF form to render with; `@rjsf/core`'s by default, a theme's (e.g. `@rjsf/mui`) if you have one. */
  form?: ComponentType<FormProps<any, any, any>>;
}

export function SchemaForm({ schema, value, onSubmit, submitLabel = "Run", disabled, uiSchema, form: RjsfForm = Form }: SchemaFormProps) {
  const simplified = useMemo(() => simplifyNullables(schema), [schema]);
  const ui = useMemo(
    () => ({
      ...impliedUiSchema(simplified, simplified),
      "ui:options": { label: false }, // the root object's title: the panel already names the command
      ...uiSchema,
      "ui:submitButtonOptions": { submitText: submitLabel },
    }),
    [simplified, uiSchema, submitLabel],
  );
  const [formData, setFormData] = useState<Record<string, unknown> | undefined>(value);

  return (
    <RjsfForm
      className="fb-form"
      schema={simplified as RJSFSchema}
      uiSchema={ui}
      validator={validator}
      widgets={widgets}
      formData={formData}
      disabled={disabled}
      liveValidate={false}
      showErrorList={false}
      noHtml5Validate
      onChange={(e: IChangeEvent) => setFormData(e.formData as Record<string, unknown>)}
      onSubmit={(e: IChangeEvent) => void onSubmit((e.formData ?? {}) as Record<string, unknown>)}
    />
  );
}
