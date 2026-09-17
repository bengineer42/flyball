/**
 * Program text <-> document tree, in the browser, for the two-way editor.
 *
 * Parsing and dumping are done by `yaml` and `smol-toml`. YAML is read and
 * written against the 1.1 schema so plain scalars resolve the way the
 * runner's loader does (PyYAML, which is 1.1: `yes`/`no`/`on`/`off` booleans,
 * leading-zero octal, sexagesimal numbers, `_` digit separators). TOML dates
 * are flattened to their text form (as the runner's `tomllib` would print
 * them back) rather than left as `Date` subclasses, so the rest of the
 * builder can keep treating the tree as plain objects/arrays/scalars.
 * The runner remains the authority: what is saved goes through its parser.
 */
import { LineCounter, parseAllDocuments, parseDocument, stringify as stringifyYaml, YAMLParseError } from "yaml";
import { parse as parseTomlLib, stringify as stringifyToml, TomlDate, TomlError } from "smol-toml";
import type { ProgramFormat } from "@flyball/client";

export class ParseError extends Error {
  readonly line: number | undefined;
  constructor(message: string, line?: number) {
    super(line === undefined ? message : `line ${line}: ${message}`);
    this.name = "ParseError";
    this.line = line;
  }
}

export type Tree = Record<string, unknown>;

/** Which formats this module can parse and dump. All three; kept as data so the UI can disable one if support is ever dropped. */
export const SUPPORTED: Record<ProgramFormat, boolean> = { yaml: true, toml: true, json: true };

export function parseText(text: string, format: ProgramFormat): unknown {
  switch (format) {
    case "yaml":
      return parseYaml(text);
    case "toml":
      return parseToml(text);
    case "json":
      return parseJson(text);
  }
}

export function dumpText(tree: unknown, format: ProgramFormat): string {
  switch (format) {
    case "yaml":
      return dumpYaml(tree);
    case "toml":
      return dumpToml(tree);
    case "json":
      return `${JSON.stringify(tree ?? null, null, 2)}\n`;
  }
}

/** True when the text carries comments the dumper would drop. */
export function hasComments(text: string, format: ProgramFormat): boolean {
  if (format === "json") return false;
  return text.split("\n").some((line) => /(^|\s)#/.test(stripQuoted(line)));
}

/** A line with quoted spans blanked, so a `#` inside a string is not taken for a comment. */
function stripQuoted(line: string): string {
  return line.replace(/"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'/g, (m) => " ".repeat(m.length));
}

// region JSON

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch (e) {
    const m = /position (\d+)/.exec(String(e));
    const line = m ? text.slice(0, Number(m[1])).split("\n").length : undefined;
    throw new ParseError(String(e instanceof Error ? e.message : e), line);
  }
}

// region YAML

/** The first line of a library error, stripped of the "at line N, column N:" suffix and the source snippet it drags along. */
function firstLine(message: string): string {
  return message.split("\n")[0]!.replace(/\s*at line \d+, column \d+:?$/, "");
}

export function parseYaml(text: string): unknown {
  const lineCounter = new LineCounter();
  try {
    const docs = parseAllDocuments(text, { version: "1.1", lineCounter });
    if (docs.length > 1) throw new ParseError("only one document is supported", lineCounter.linePos(docs[1]!.range![0]).line);
    const doc = docs[0] ?? parseDocument("", { version: "1.1" });
    const error = doc.errors[0];
    if (error) throw new ParseError(firstLine(error.message), error.linePos?.[0]?.line);
    return doc.toJS();
  } catch (e) {
    if (e instanceof ParseError) throw e;
    if (e instanceof YAMLParseError) throw new ParseError(firstLine(e.message), e.linePos?.[0]?.line);
    throw new ParseError(e instanceof Error ? e.message : String(e));
  }
}

export function dumpYaml(tree: unknown): string {
  return stringifyYaml(tree ?? null, { version: "1.1", lineWidth: 0 });
}

// region TOML

/** `TomlDate` (and any nested copy) back to the text smol-toml would itself print, so the tree stays plain data. */
function detoml(v: unknown): unknown {
  if (v instanceof TomlDate) return v.toJSON();
  if (Array.isArray(v)) return v.map(detoml);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, detoml(x)]));
  return v;
}

export function parseToml(text: string): Tree {
  try {
    return detoml(parseTomlLib(text)) as Tree;
  } catch (e) {
    if (e instanceof TomlError) throw new ParseError(firstLine(e.message), e.line);
    throw new ParseError(e instanceof Error ? e.message : String(e));
  }
}

export function dumpToml(tree: unknown): string {
  if (!tree || typeof tree !== "object" || Array.isArray(tree)) throw new ParseError("a TOML document is a table");
  try {
    return `${stringifyToml(tree as Tree).replace(/\n*$/, "")}\n`;
  } catch (e) {
    throw new ParseError(e instanceof Error ? e.message : String(e));
  }
}
