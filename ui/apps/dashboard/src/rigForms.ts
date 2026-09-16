/**
 * Reading `GET /api/rig/schema` for the "Add device" / "Add link" dialogs:
 * the tags a driver or a link may take, and each one's own config schema,
 * with the whole schema's `$defs` still needed to resolve its `$ref`s.
 */
import { deref, type JsonSchema } from "@flyball/client";

export interface DriverVariant {
  /** The rig file's `driver:` tag. */
  tag: string;
  /** The driver's own config schema (unpatched): what `SchemaForm` needs, plus the root's `$defs`. */
  configSchema: JsonSchema;
}

/** Every device driver the rig schema offers, tag and config schema, in the order the server lists them. */
export function deviceDrivers(schema: JsonSchema): DriverVariant[] {
  const additional = schema.properties?.devices?.additionalProperties as JsonSchema | undefined;
  const variants = additional?.oneOf ?? [];
  const out: DriverVariant[] = [];
  for (const variant of variants) {
    // Each entry is `{oneOf: [layered, flat]}`; the layered branch has `config` as the driver's own schema.
    const layered = variant.oneOf?.[0];
    const tag = layered?.properties?.driver?.const;
    const configSchema = layered?.properties?.config as JsonSchema | undefined;
    if (typeof tag === "string" && configSchema) out.push({ tag, configSchema });
  }
  return out;
}

export interface LinkVariant {
  tag: string;
  /** The link's own config schema (has a `tag` const property; hide it, don't strip it -- `SchemaForm` fills its default). */
  configSchema: JsonSchema;
}

/** Every link kind the rig schema offers, from `links`' discriminator mapping. */
export function linkKinds(schema: JsonSchema): LinkVariant[] {
  const additional = schema.properties?.links?.additionalProperties as JsonSchema | undefined;
  const mapping = (additional?.discriminator?.mapping ?? {}) as Record<string, string>;
  return Object.entries(mapping).map(([tag, ref]) => ({ tag, configSchema: deref({ $ref: ref }, schema) }));
}

/**
 * True when `field` (a driver config's property schema) names a link by its
 * rig-file name: an `anyOf` branch (found directly, or through one `$ref`)
 * that is a bare `{"type": "string"}` with nothing else on it -- the shape
 * every `DriverConfig.link` field takes, whether it also accepts an inline
 * link config or is nullable.
 */
function namesALink(field: JsonSchema, root: JsonSchema): boolean {
  const resolved = field.$ref ? deref(field, root) : field;
  const options = resolved.anyOf ?? resolved.oneOf;
  return Boolean(options?.some((o) => o.type === "string" && Object.keys(o).length === 1));
}

/**
 * `configSchema` with its `link` property (if it names one, see `namesALink`)
 * turned into a plain string field offering `linkNames`: a select once there
 * are links to offer, else a free-form box so a link created after opening
 * the dialog is not needed to complete the form.
 */
export function withLinkSelect(configSchema: JsonSchema, root: JsonSchema, linkNames: string[]): JsonSchema {
  const original = configSchema.properties?.link;
  if (!original || !namesALink(original, root)) return configSchema;
  const resolved = original.$ref ? deref(original, root) : original;
  const patched: JsonSchema = {
    type: "string",
    title: original.title ?? resolved.title ?? "Link",
    description: original.description ?? resolved.description,
    ...(linkNames.length ? { enum: linkNames } : {}),
  };
  return { ...configSchema, properties: { ...configSchema.properties, link: patched } };
}

/** Hides a tagged union's `tag` field: `SchemaForm` still fills its const default, just not shown (the picker above already named it). */
export const TAG_HIDDEN = { tag: { "ui:widget": "hidden" } };
