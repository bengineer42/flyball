/**
 * Program text <-> document tree, in the browser, for the two-way editor.
 *
 * No YAML or TOML library is installed, so these are hand-written subset
 * parsers and dumpers covering what a program file uses: mappings, lists,
 * flow / inline collections, quoted and block strings, numbers, booleans,
 * null, comments. Not covered: YAML anchors, aliases, tags, multi-document
 * streams, complex keys; TOML date-times (kept as text). Scalars resolve
 * the way the daemon's loaders do (PyYAML 1.1 for YAML, `tomllib` for TOML).
 * The daemon remains the authority: what is saved goes through its parser.
 */
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

const isObject = (v: unknown): v is Tree => Boolean(v) && typeof v === "object" && !Array.isArray(v);

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

// region YAML scalars

const YAML_BOOL_TRUE = /^(?:yes|Yes|YES|true|True|TRUE|on|On|ON)$/;
const YAML_BOOL_FALSE = /^(?:no|No|NO|false|False|FALSE|off|Off|OFF)$/;
const YAML_NULL = /^(?:~|null|Null|NULL|)$/;
const YAML_INT = /^[-+]?(?:0b[01_]+|0x[0-9a-fA-F_]+|0[0-7_]+|(?:0|[1-9][0-9_]*)|[1-9][0-9_]*(?::[0-5]?[0-9])+)$/;
const YAML_FLOAT = /^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?|[-+]?\.[0-9_]+(?:[eE][-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$/;

/** A plain (unquoted) YAML scalar to the value PyYAML's resolver gives it. */
function resolvePlain(s: string): unknown {
  if (YAML_NULL.test(s)) return null;
  if (YAML_BOOL_TRUE.test(s)) return true;
  if (YAML_BOOL_FALSE.test(s)) return false;
  if (YAML_INT.test(s)) {
    const clean = s.replace(/_/g, "");
    const sign = clean.startsWith("-") ? -1 : 1;
    const body = clean.replace(/^[-+]/, "");
    if (body.includes(":")) {
      const n = body.split(":").reduce((acc, part) => acc * 60 + Number(part), 0);
      return sign * n;
    }
    if (body.startsWith("0b")) return sign * parseInt(body.slice(2), 2);
    if (body.startsWith("0x")) return sign * parseInt(body.slice(2), 16);
    if (body.length > 1 && body.startsWith("0")) return sign * parseInt(body.slice(1), 8);
    return sign * Number(body);
  }
  if (YAML_FLOAT.test(s)) {
    const clean = s.replace(/_/g, "");
    if (/inf/i.test(clean)) return clean.startsWith("-") ? -Infinity : Infinity;
    if (/nan/i.test(clean)) return NaN;
    if (clean.includes(":")) {
      const sign = clean.startsWith("-") ? -1 : 1;
      const n = clean
        .replace(/^[-+]/, "")
        .split(":")
        .reduce((acc, part) => acc * 60 + Number(part), 0);
      return sign * n;
    }
    return Number(clean);
  }
  return s;
}

const ESCAPES: Record<string, string> = { "0": "\0", a: "\x07", b: "\b", t: "\t", "\t": "\t", n: "\n", v: "\v", f: "\f", r: "\r", e: "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", N: "\u0085", _: "\u00a0", L: "\u2028", P: "\u2029" };

/** Read a quoted scalar starting at `s[i]` (a `"` or `'`); returns the value and the index after the closing quote. */
function readQuoted(s: string, i: number, line?: number): [string, number] {
  const q = s[i]!;
  let out = "";
  i++;
  while (i < s.length) {
    const c = s[i]!;
    if (q === "'") {
      if (c === "'") {
        if (s[i + 1] === "'") {
          out += "'";
          i += 2;
          continue;
        }
        return [out, i + 1];
      }
      out += c;
      i++;
      continue;
    }
    if (c === '"') return [out, i + 1];
    if (c === "\\") {
      const e = s[i + 1];
      if (e === undefined) break;
      if (e === "x" || e === "u" || e === "U") {
        const n = e === "x" ? 2 : e === "u" ? 4 : 8;
        const hex = s.slice(i + 2, i + 2 + n);
        if (!/^[0-9a-fA-F]+$/.test(hex) || hex.length !== n) throw new ParseError(`bad escape \\${e}${hex}`, line);
        out += String.fromCodePoint(parseInt(hex, 16));
        i += 2 + n;
        continue;
      }
      if (e === "\n") {
        // escaped line break: continuation
        i += 2;
        while (s[i] === " " || s[i] === "\t") i++;
        continue;
      }
      const mapped = ESCAPES[e];
      if (mapped === undefined) throw new ParseError(`unknown escape \\${e}`, line);
      out += mapped;
      i += 2;
      continue;
    }
    if (c === "\n") {
      // a quoted scalar folded over lines: a line break becomes a space, blank lines newlines
      let j = i + 1;
      let blanks = 0;
      while (j < s.length) {
        const k = j;
        while (s[j] === " " || s[j] === "\t") j++;
        if (s[j] === "\n") {
          blanks++;
          j++;
          continue;
        }
        if (j === k && s[j] !== "\n") break;
        break;
      }
      out = out.replace(/[ \t]+$/, "") + (blanks ? "\n".repeat(blanks) : " ");
      i = j;
      continue;
    }
    out += c;
    i++;
  }
  throw new ParseError("unterminated quoted string", line);
}

// region YAML flow collections

class FlowReader {
  i = 0;
  s: string;
  line: number;
  constructor(s: string, line: number) {
    this.s = s;
    this.line = line;
  }
  skip() {
    for (;;) {
      while (this.i < this.s.length && /\s/.test(this.s[this.i]!)) this.i++;
      if (this.s[this.i] === "#" && (this.i === 0 || /\s/.test(this.s[this.i - 1]!))) {
        while (this.i < this.s.length && this.s[this.i] !== "\n") this.i++;
        continue;
      }
      return;
    }
  }
  peek() {
    this.skip();
    return this.s[this.i];
  }
  value(): unknown {
    const c = this.peek();
    if (c === "{") return this.mapping();
    if (c === "[") return this.sequence();
    if (c === '"' || c === "'") {
      const [v, next] = readQuoted(this.s, this.i, this.line);
      this.i = next;
      return v;
    }
    if (c === "&" || c === "*" || c === "!") throw new ParseError("anchors, aliases and tags are not supported", this.line);
    return resolvePlain(this.plain());
  }
  /** A plain scalar in flow context: up to `,` `]` `}` or `: `/`:` at the end. */
  plain(): string {
    const start = this.i;
    while (this.i < this.s.length) {
      const c = this.s[this.i]!;
      if (c === "," || c === "]" || c === "}") break;
      if (c === ":" && (this.i + 1 >= this.s.length || /[\s,\]}]/.test(this.s[this.i + 1]!))) break;
      if (c === "#" && /\s/.test(this.s[this.i - 1] ?? " ")) break;
      if (c === "\n") break;
      this.i++;
    }
    return this.s.slice(start, this.i).trim();
  }
  key(): string {
    const c = this.peek();
    if (c === '"' || c === "'") {
      const [v, next] = readQuoted(this.s, this.i, this.line);
      this.i = next;
      return v;
    }
    return this.plain();
  }
  expect(ch: string) {
    if (this.peek() !== ch) throw new ParseError(`expected '${ch}'${this.s[this.i] === undefined ? " before the end" : ` at '${this.s.slice(this.i, this.i + 12)}'`}`, this.line);
    this.i++;
  }
  mapping(): Tree {
    this.expect("{");
    const out: Tree = {};
    for (;;) {
      if (this.peek() === "}") {
        this.i++;
        return out;
      }
      const k = this.key();
      if (this.peek() === ":") {
        this.i++;
        const c = this.peek();
        out[k] = c === "," || c === "}" ? null : this.value();
      } else out[k] = null;
      const c = this.peek();
      if (c === ",") {
        this.i++;
        continue;
      }
      if (c === "}") continue;
      throw new ParseError(`expected ',' or '}' in flow mapping`, this.line);
    }
  }
  sequence(): unknown[] {
    this.expect("[");
    const out: unknown[] = [];
    for (;;) {
      if (this.peek() === "]") {
        this.i++;
        return out;
      }
      const v = this.value();
      if (this.peek() === ":" && typeof v === "string") {
        // `[a: 1]`: a single-pair mapping as a sequence item
        this.i++;
        out.push({ [v]: this.value() });
      } else out.push(v);
      const c = this.peek();
      if (c === ",") {
        this.i++;
        continue;
      }
      if (c === "]") continue;
      throw new ParseError(`expected ',' or ']' in flow sequence`, this.line);
    }
  }
}

// region YAML block structure

interface Line {
  /** 1-based, for errors. */
  no: number;
  indent: number;
  /** After the indent; comments not yet stripped (block scalars keep them). */
  text: string;
}

/** Strip a trailing ` # comment` from block-context text, honouring quotes. */
function stripComment(text: string): string {
  let inQ: string | null = null;
  for (let i = 0; i < text.length; i++) {
    const c = text[i]!;
    if (inQ) {
      if (c === "\\" && inQ === '"') i++;
      else if (c === inQ) inQ = null;
      continue;
    }
    if (c === '"' || c === "'") inQ = c;
    else if (c === "#" && (i === 0 || /\s/.test(text[i - 1]!))) return text.slice(0, i).trimEnd();
  }
  return text.trimEnd();
}

/** Where a block mapping key ends: the index of the `:` that is followed by a space or the end, outside quotes and flow brackets. */
function keyColon(text: string): number {
  if (text.startsWith("- ") || text === "-") return -1;
  if (text[0] === '"' || text[0] === "'") {
    try {
      const [, next] = readQuoted(text, 0);
      const rest = text.slice(next).trimStart();
      return rest.startsWith(":") && (rest.length === 1 || /\s/.test(rest[1]!)) ? next + (text.slice(next).length - rest.length) : -1;
    } catch {
      return -1;
    }
  }
  if (text[0] === "[" || text[0] === "{") return -1;
  let depth = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text[i]!;
    if (c === "[" || c === "{") depth++;
    else if (c === "]" || c === "}") depth--;
    else if (c === "#" && i > 0 && /\s/.test(text[i - 1]!)) return -1;
    else if (c === ":" && depth === 0 && (i + 1 === text.length || /\s/.test(text[i + 1]!))) return i;
  }
  return -1;
}

class YamlParser {
  lines: Line[] = [];
  pos = 0;

  constructor(text: string) {
    const raw = text.replace(/\r\n?/g, "\n").split("\n");
    if (raw.length > 1 && raw[raw.length - 1] === "") raw.pop(); // the newline that ends the file is not a blank line
    raw.forEach((l, i) => {
      const indent = l.length - l.trimStart().length;
      this.lines.push({ no: i + 1, indent, text: l.slice(indent) });
    });
  }

  /** The next line that has content (not blank, not a comment), from `pos`. */
  private skipBlank() {
    while (this.pos < this.lines.length) {
      const l = this.lines[this.pos]!;
      if (l.text === "" || l.text.startsWith("#") || l.text.trim() === "") this.pos++;
      else if (l.text.startsWith("%")) this.pos++; // a directive
      else return;
    }
  }

  /** The next content line, or undefined at the end of the document (`...` / a second `---` count as the end). */
  private current(): Line | undefined {
    this.skipBlank();
    const l = this.lines[this.pos];
    if (l && l.indent === 0 && (l.text === "..." || l.text === "---" || l.text.startsWith("--- "))) return undefined;
    return l;
  }

  parse(): unknown {
    this.skipBlank();
    let l = this.lines[this.pos];
    if (l && l.indent === 0 && (l.text === "---" || l.text.startsWith("--- "))) {
      const rest = l.text.slice(3).trim();
      if (rest) this.lines[this.pos] = { ...l, text: rest, indent: 4 };
      else this.pos++;
    }
    l = this.current();
    if (!l) return null;
    const value = this.node(l.indent, -1);
    const after = this.current();
    if (after) throw new ParseError(`unexpected content '${after.text.slice(0, 20)}'`, after.no);
    // an end marker may follow; after it, nothing but blank lines: one document only
    this.skipBlank();
    if (this.lines[this.pos]) {
      this.pos++;
      this.skipBlank();
      const more = this.lines[this.pos];
      if (more) throw new ParseError("only one document is supported", more.no);
    }
    return value;
  }

  /** The node starting at the current line, which sits at `indent`; `parentIndent` bounds it. */
  private node(indent: number, parentIndent: number): unknown {
    const l = this.current();
    if (!l || l.indent <= parentIndent) return null;
    if (l.text === "-" || l.text.startsWith("- ")) return this.sequence(l.indent);
    if (keyColon(l.text) >= 0) return this.mapping(l.indent);
    return this.scalarLines(parentIndent);
  }

  /** A scalar that is the whole node: a flow collection (maybe over several lines), a quoted or a plain scalar. */
  private scalarLines(parentIndent: number): unknown {
    const l = this.current()!;
    const c = l.text[0];
    if (c === "[" || c === "{" || c === '"' || c === "'") return this.inlineValue(l.text, l.no, parentIndent, true);
    return this.inlineValue(l.text, l.no, parentIndent, true);
  }

  /**
   * The value written after a key or a dash: a flow collection or quoted string, which may
   * continue on following lines while unbalanced; a block scalar; or a plain scalar (one line,
   * or folded over more-indented lines).
   */
  private inlineValue(text: string, no: number, parentIndent: number, consumeCurrent: boolean): unknown {
    const c = text[0]!;
    if (c === "|" || c === ">") return this.blockScalar(text, parentIndent, consumeCurrent);
    if (c === "&" || c === "*" || c === "!") throw new ParseError("anchors, aliases and tags are not supported", no);
    if (c === "[" || c === "{" || c === '"' || c === "'") {
      // gather lines until the collection / string closes
      let joined = text;
      let end = this.pos + (consumeCurrent ? 1 : 1);
      const closed = (s: string) => {
        try {
          const r = new FlowReader(s, no);
          r.value();
          r.skip();
          return r.i >= s.length;
        } catch {
          return false;
        }
      };
      while (!closed(joined)) {
        const next = this.lines[end];
        if (!next) throw new ParseError(c === "[" || c === "{" ? "unclosed flow collection" : "unterminated quoted string", no);
        joined += `\n${next.text}`;
        end++;
      }
      this.pos = end;
      const r = new FlowReader(joined, no);
      const v = r.value();
      r.skip();
      if (r.i < joined.length) throw new ParseError(`unexpected '${joined.slice(r.i, r.i + 12)}' after value`, no);
      return v;
    }
    // plain scalar; may fold over following lines indented deeper than the parent
    let s = stripComment(text);
    if (keyColon(s) >= 0) throw new ParseError("mapping values are not allowed here", no);
    this.pos++;
    for (;;) {
      const next = this.lines[this.pos];
      if (!next || next.text.trim() === "" || next.text.startsWith("#")) break;
      if (next.indent <= parentIndent) break;
      if (next.text.startsWith("- ") || next.text === "-" || keyColon(next.text) >= 0) break;
      s += ` ${stripComment(next.text)}`;
      this.pos++;
    }
    return resolvePlain(s);
  }

  private blockScalar(header: string, parentIndent: number, consumeCurrent: boolean): string {
    const m = /^([|>])([-+]?)(\d?)([-+]?)\s*(#.*)?$/.exec(header);
    if (!m) throw new ParseError(`bad block scalar header '${header}'`, this.lines[this.pos]?.no);
    const folded = m[1] === ">";
    const chomp = m[2] || m[4] || "";
    const explicit = m[3] ? parentIndent + Number(m[3]) : undefined;
    if (consumeCurrent) this.pos++;
    const body: string[] = [];
    let contentIndent = explicit;
    while (this.pos < this.lines.length) {
      const l = this.lines[this.pos]!;
      const blank = l.text.trim() === "";
      if (!blank) {
        if (contentIndent === undefined) {
          if (l.indent <= parentIndent) break;
          contentIndent = l.indent;
        }
        if (l.indent < contentIndent) break;
      }
      body.push(blank ? "" : " ".repeat(l.indent - contentIndent!) + l.text);
      this.pos++;
    }
    // trailing blank lines are the chomped part
    let trailing = 0;
    while (body.length && body[body.length - 1] === "") {
      body.pop();
      trailing++;
    }
    let out: string;
    if (folded) {
      out = "";
      let prevMore = false;
      body.forEach((line, i) => {
        const more = line.startsWith(" ");
        if (i === 0) out = line;
        else if (line === "") out += "\n";
        else if (more || prevMore) out += (out.endsWith("\n") ? "" : "\n") + line;
        else out += (out.endsWith("\n") ? "" : " ") + line;
        prevMore = more;
      });
    } else out = body.join("\n");
    if (chomp === "-") return out;
    if (chomp === "+") return `${out}\n${"\n".repeat(trailing)}`;
    return body.length ? `${out}\n` : "";
  }

  private mapping(indent: number): Tree {
    const out: Tree = {};
    for (;;) {
      const l = this.current();
      if (!l || l.indent < indent) return out;
      if (l.indent > indent) throw new ParseError(`unexpected indent`, l.no);
      const colon = keyColon(l.text);
      if (colon < 0) throw new ParseError(l.text.startsWith("- ") || l.text === "-" ? "a list item where a key was expected" : `expected 'key: value', got '${l.text.slice(0, 20)}'`, l.no);
      const rawKey = l.text.slice(0, colon).trim();
      const key = rawKey[0] === '"' || rawKey[0] === "'" ? readQuoted(rawKey, 0, l.no)[0] : rawKey;
      if (key.startsWith("?") || key.startsWith("&") || key.startsWith("*")) throw new ParseError("complex keys, anchors and aliases are not supported", l.no);
      const rest = l.text.slice(colon + 1).trim();
      if (rest === "" || rest.startsWith("#")) {
        this.pos++;
        const next = this.current();
        // `key:` followed by a list at the same indent is allowed
        if (next && (next.indent > indent || (next.indent === indent && (next.text === "-" || next.text.startsWith("- "))))) {
          out[key] = next.indent === indent ? this.sequence(indent) : this.node(next.indent, indent);
        } else out[key] = null;
      } else {
        out[key] = this.inlineValue(rest, l.no, indent, true);
      }
    }
  }

  private sequence(indent: number): unknown[] {
    const out: unknown[] = [];
    for (;;) {
      const l = this.current();
      if (!l || l.indent < indent) return out;
      if (l.indent > indent) throw new ParseError("unexpected indent", l.no);
      if (!(l.text === "-" || l.text.startsWith("- "))) return out;
      const rest = l.text.slice(1).trimStart();
      if (rest === "" || rest.startsWith("#")) {
        this.pos++;
        const next = this.current();
        out.push(next && next.indent > indent ? this.node(next.indent, indent) : null);
        continue;
      }
      const restIndent = indent + (l.text.length - rest.length);
      if (rest === "-" || rest.startsWith("- ") || keyColon(rest) >= 0) {
        // compact nested collection: `- key: v` / `- - v`; re-slice the line at the item's column
        this.lines[this.pos] = { ...l, indent: restIndent, text: rest };
        out.push(this.node(restIndent, indent));
        continue;
      }
      out.push(this.inlineValue(rest, l.no, indent, true));
    }
  }
}

export function parseYaml(text: string): unknown {
  return new YamlParser(text).parse();
}

// region YAML dumping

const PLAIN_SAFE = /^[A-Za-z0-9_./][A-Za-z0-9_./°%()+=-]*$/;

function yamlScalar(v: unknown, flow: boolean): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return yamlNumber(v);
  if (typeof v === "string") {
    if (PLAIN_SAFE.test(v) && typeof resolvePlain(v) === "string") return v;
    void flow;
    return JSON.stringify(v);
  }
  return JSON.stringify(v);
}

function yamlNumber(n: number): string {
  if (Number.isNaN(n)) return ".nan";
  if (n === Infinity) return ".inf";
  if (n === -Infinity) return "-.inf";
  let s = String(n);
  if (/e/i.test(s)) {
    // PyYAML wants a dot and a signed exponent to read a float
    let [mant, exp] = s.split(/e/i) as [string, string];
    if (!mant.includes(".")) mant += ".0";
    if (!/^[-+]/.test(exp)) exp = `+${exp}`;
    s = `${mant}e${exp}`;
  }
  return s;
}

function yamlFlow(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(yamlFlow).join(", ")}]`;
  if (isObject(v)) {
    const entries = Object.entries(v).filter(([, x]) => x !== undefined);
    return entries.length ? `{ ${entries.map(([k, x]) => `${yamlScalar(k, true)}: ${yamlFlow(x)}`).join(", ")} }` : "{}";
  }
  return yamlScalar(v, true);
}

/** Flow style suits a value with no multi-line strings that fits on a line. */
function fitsFlow(v: unknown, limit: number): boolean {
  const hasNewline = (x: unknown): boolean => (typeof x === "string" ? x.includes("\n") : Array.isArray(x) ? x.some(hasNewline) : isObject(x) ? Object.values(x).some(hasNewline) : false);
  return !hasNewline(v) && yamlFlow(v).length <= limit;
}

function yamlBlockString(s: string, indent: string): string {
  // literal block; `|-` when there is no trailing newline, `|` when there is exactly one
  const chomp = s.endsWith("\n") ? (s.endsWith("\n\n") ? "+" : "") : "-";
  const body = (chomp === "" ? s.slice(0, -1) : s).split("\n");
  return `|${chomp}\n${body.map((l) => (l === "" ? "" : indent + l)).join("\n")}`;
}

/** A value in block context, placed after `key:` or `- `; `indent` is the child indentation. */
function yamlValue(v: unknown, indent: string, limit: number): string {
  if (typeof v === "string" && v.includes("\n")) return yamlBlockString(v, indent);
  if (Array.isArray(v)) {
    if (v.length === 0) return "[]";
    if (v.every((x) => !isObject(x) && !Array.isArray(x)) && fitsFlow(v, limit)) return yamlFlow(v);
    return `\n${v.map((x) => `${indent}- ${yamlItem(x, indent, limit)}`).join("\n")}`;
  }
  if (isObject(v)) {
    const entries = Object.entries(v).filter(([, x]) => x !== undefined);
    if (entries.length === 0) return "{}";
    return `\n${entries.map(([k, x]) => `${indent}${yamlScalar(k, false)}:${yamlSpace(x)}${yamlValue(x, `${indent}  `, limit)}`).join("\n")}`;
  }
  return yamlScalar(v, false);
}

/** A sequence item: a short mapping or list in flow style (`- ramp: { … }`), a longer mapping compact-nested. */
function yamlItem(v: unknown, indent: string, limit: number): string {
  if (isObject(v)) {
    const entries = Object.entries(v).filter(([, x]) => x !== undefined);
    if (entries.length === 0) return "{}";
    if (entries.length === 1 && entries[0]) {
      const [k, x] = entries[0];
      if (fitsFlow(x, limit - indent.length - k.length - 4)) return `${yamlScalar(k, false)}: ${yamlFlow(x)}`;
    }
    const inner = `${indent}  `;
    return entries.map(([k, x], i) => `${i ? inner : ""}${yamlScalar(k, false)}:${yamlSpace(x)}${yamlValue(x, `${inner}  `, limit)}`).join("\n");
  }
  if (Array.isArray(v)) return fitsFlow(v, limit) ? yamlFlow(v) : yamlValue(v, `${indent}  `, limit);
  return yamlValue(v, `${indent}  `, limit);
}

/** The separator after `key:`: nothing when the value starts on its own lines, a space otherwise. */
function yamlSpace(v: unknown): string {
  if (Array.isArray(v)) return v.length === 0 || (v.every((x) => !isObject(x) && !Array.isArray(x)) && fitsFlow(v, 100)) ? " " : "";
  if (isObject(v)) return Object.values(v).some((x) => x !== undefined) ? "" : " ";
  return " ";
}

export function dumpYaml(tree: unknown, limit = 100): string {
  if (!isObject(tree)) return `${yamlValue(tree, "  ", limit)}\n`;
  const entries = Object.entries(tree).filter(([, x]) => x !== undefined);
  if (entries.length === 0) return "{}\n";
  return `${entries.map(([k, x]) => `${yamlScalar(k, false)}:${yamlSpace(x)}${yamlValue(x, "  ", limit)}`).join("\n")}\n`;
}

// region TOML

class TomlParser {
  i = 0;
  line = 1;
  root: Tree = {};
  /** Tables defined by a header (so a dotted key cannot redefine them), and arrays of tables. */
  private explicit = new Set<unknown>();
  private arrays = new Set<unknown>();

  s: string;
  constructor(s: string) {
    this.s = s.replace(/\r\n?/g, "\n");
  }

  private err(msg: string): never {
    throw new ParseError(msg, this.line);
  }

  private ws() {
    while (this.s[this.i] === " " || this.s[this.i] === "\t") this.i++;
  }

  /** Whitespace, comments and newlines: between statements and inside arrays. */
  private wsNl() {
    for (;;) {
      this.ws();
      const c = this.s[this.i];
      if (c === "#") {
        while (this.i < this.s.length && this.s[this.i] !== "\n") this.i++;
        continue;
      }
      if (c === "\n") {
        this.i++;
        this.line++;
        continue;
      }
      return;
    }
  }

  private endOfStatement() {
    this.ws();
    const c = this.s[this.i];
    if (c === "#") {
      while (this.i < this.s.length && this.s[this.i] !== "\n") this.i++;
    }
    if (this.i >= this.s.length) return;
    if (this.s[this.i] !== "\n") this.err(`unexpected '${this.s.slice(this.i, this.i + 10)}' after value`);
  }

  parse(): Tree {
    let table: Tree = this.root;
    for (;;) {
      this.wsNl();
      if (this.i >= this.s.length) return this.root;
      const c = this.s[this.i];
      if (c === "[") {
        const isArray = this.s[this.i + 1] === "[";
        this.i += isArray ? 2 : 1;
        this.ws();
        const path = this.keyPath();
        this.ws();
        if (isArray) {
          if (this.s.slice(this.i, this.i + 2) !== "]]") this.err("expected ']]'");
          this.i += 2;
        } else {
          if (this.s[this.i] !== "]") this.err("expected ']'");
          this.i++;
        }
        table = this.openTable(path, isArray);
        this.endOfStatement();
        continue;
      }
      const path = this.keyPath();
      this.ws();
      if (this.s[this.i] !== "=") this.err("expected '='");
      this.i++;
      this.ws();
      const value = this.value();
      this.assign(table, path, value);
      this.endOfStatement();
    }
  }

  private openTable(path: string[], isArray: boolean): Tree {
    let node: Tree = this.root;
    path.forEach((k, idx) => {
      const last = idx === path.length - 1;
      const existing = node[k];
      if (last && isArray) {
        let arr = existing;
        if (arr === undefined) {
          arr = [];
          node[k] = arr;
          this.arrays.add(arr);
        } else if (!Array.isArray(arr) || !this.arrays.has(arr)) this.err(`'${k}' is not an array of tables`);
        const t: Tree = {};
        (arr as Tree[]).push(t);
        node = t;
        return;
      }
      if (existing === undefined) {
        const t: Tree = {};
        node[k] = t;
        node = t;
      } else if (Array.isArray(existing) && this.arrays.has(existing)) {
        node = existing[existing.length - 1] as Tree;
      } else if (isObject(existing)) {
        if (last && this.explicit.has(existing)) this.err(`table '${path.join(".")}' defined twice`);
        node = existing;
      } else this.err(`'${k}' is not a table`);
    });
    if (!isArray) this.explicit.add(node);
    return node;
  }

  private assign(table: Tree, path: string[], value: unknown) {
    let node = table;
    for (let idx = 0; idx < path.length - 1; idx++) {
      const k = path[idx]!;
      const existing = node[k];
      if (existing === undefined) {
        const t: Tree = {};
        node[k] = t;
        node = t;
      } else if (isObject(existing) && !this.explicit.has(existing)) node = existing;
      else this.err(`cannot extend '${k}' with a dotted key`);
    }
    const leaf = path[path.length - 1]!;
    if (leaf in node) this.err(`'${leaf}' defined twice`);
    node[leaf] = value;
  }

  private keyPath(): string[] {
    const parts: string[] = [];
    for (;;) {
      this.ws();
      const c = this.s[this.i];
      if (c === '"' || c === "'") parts.push(this.string());
      else {
        const m = /^[A-Za-z0-9_-]+/.exec(this.s.slice(this.i));
        if (!m) this.err(`expected a key, got '${this.s.slice(this.i, this.i + 10)}'`);
        parts.push(m[0]);
        this.i += m[0].length;
      }
      this.ws();
      if (this.s[this.i] === ".") {
        this.i++;
        continue;
      }
      return parts;
    }
  }

  private string(): string {
    const q = this.s[this.i]!;
    const triple = this.s.slice(this.i, this.i + 3) === q.repeat(3);
    if (triple) {
      this.i += 3;
      if (this.s[this.i] === "\n") {
        this.i++;
        this.line++;
      }
      let out = "";
      while (this.i < this.s.length) {
        if (this.s.slice(this.i, this.i + 3) === q.repeat(3)) {
          // up to two extra quotes belong to the content
          let extra = 0;
          while (this.s[this.i + 3 + extra] === q && extra < 2) extra++;
          out += q.repeat(extra);
          this.i += 3 + extra;
          return out;
        }
        const c = this.s[this.i]!;
        if (c === "\n") this.line++;
        if (q === '"' && c === "\\") {
          if (/^\\[ \t]*\n/.test(this.s.slice(this.i))) {
            // line-ending backslash: trim whitespace and newlines
            this.i++;
            while (/[ \t\n]/.test(this.s[this.i] ?? "")) {
              if (this.s[this.i] === "\n") this.line++;
              this.i++;
            }
            continue;
          }
          out += this.escape();
          continue;
        }
        out += c;
        this.i++;
      }
      this.err("unterminated string");
    }
    this.i++;
    let out = "";
    while (this.i < this.s.length) {
      const c = this.s[this.i]!;
      if (c === q) {
        this.i++;
        return out;
      }
      if (c === "\n") this.err("unterminated string");
      if (q === '"' && c === "\\") {
        out += this.escape();
        continue;
      }
      out += c;
      this.i++;
    }
    this.err("unterminated string");
  }

  private escape(): string {
    const e = this.s[this.i + 1];
    const simple: Record<string, string> = { b: "\b", t: "\t", n: "\n", f: "\f", r: "\r", '"': '"', "\\": "\\", e: "\x1b" };
    if (e !== undefined && simple[e] !== undefined) {
      this.i += 2;
      return simple[e]!;
    }
    if (e === "u" || e === "U") {
      const n = e === "u" ? 4 : 8;
      const hex = this.s.slice(this.i + 2, this.i + 2 + n);
      if (!/^[0-9a-fA-F]+$/.test(hex) || hex.length !== n) this.err(`bad escape \\${e}${hex}`);
      this.i += 2 + n;
      return String.fromCodePoint(parseInt(hex, 16));
    }
    this.err(`unknown escape \\${e ?? ""}`);
  }

  private value(): unknown {
    const c = this.s[this.i];
    if (c === undefined) this.err("expected a value");
    if (c === '"' || c === "'") return this.string();
    if (c === "[") return this.array();
    if (c === "{") return this.inlineTable();
    const rest = this.s.slice(this.i);
    let m: RegExpExecArray | null;
    if ((m = /^(true|false)(?![A-Za-z0-9_-])/.exec(rest))) {
      this.i += m[0].length;
      return m[0] === "true";
    }
    if ((m = /^[-+]?(inf|nan)(?![A-Za-z0-9_-])/.exec(rest))) {
      this.i += m[0].length;
      return m[1] === "nan" ? NaN : m[0].startsWith("-") ? -Infinity : Infinity;
    }
    // date-times are kept as text
    if ((m = /^\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[-+]\d{2}:\d{2})?)?|^\d{2}:\d{2}:\d{2}(?:\.\d+)?/.exec(rest))) {
      this.i += m[0].length;
      return m[0];
    }
    if ((m = /^0x[0-9A-Fa-f](?:_?[0-9A-Fa-f])*/.exec(rest))) {
      this.i += m[0].length;
      return parseInt(m[0].slice(2).replace(/_/g, ""), 16);
    }
    if ((m = /^0o[0-7](?:_?[0-7])*/.exec(rest))) {
      this.i += m[0].length;
      return parseInt(m[0].slice(2).replace(/_/g, ""), 8);
    }
    if ((m = /^0b[01](?:_?[01])*/.exec(rest))) {
      this.i += m[0].length;
      return parseInt(m[0].slice(2).replace(/_/g, ""), 2);
    }
    if ((m = /^[-+]?(?:0|[1-9](?:_?\d)*)(?:\.\d(?:_?\d)*)?(?:[eE][-+]?\d(?:_?\d)*)?(?![A-Za-z0-9_.-])/.exec(rest))) {
      this.i += m[0].length;
      return Number(m[0].replace(/_/g, ""));
    }
    this.err(`unexpected '${rest.slice(0, 10)}'`);
  }

  private array(): unknown[] {
    this.i++;
    const out: unknown[] = [];
    for (;;) {
      this.wsNl();
      if (this.s[this.i] === "]") {
        this.i++;
        return out;
      }
      out.push(this.value());
      this.wsNl();
      const c = this.s[this.i];
      if (c === ",") {
        this.i++;
        continue;
      }
      if (c === "]") continue;
      this.err("expected ',' or ']' in array");
    }
  }

  private inlineTable(): Tree {
    this.i++;
    const out: Tree = {};
    this.ws();
    if (this.s[this.i] === "}") {
      this.i++;
      return out;
    }
    for (;;) {
      this.ws();
      const path = this.keyPath();
      this.ws();
      if (this.s[this.i] !== "=") this.err("expected '=' in inline table");
      this.i++;
      this.ws();
      this.assign(out, path, this.value());
      this.ws();
      const c = this.s[this.i];
      if (c === ",") {
        this.i++;
        continue;
      }
      if (c === "}") {
        this.i++;
        return out;
      }
      this.err("expected ',' or '}' in inline table");
    }
  }
}

export function parseToml(text: string): Tree {
  return new TomlParser(text).parse();
}

const BARE_KEY = /^[A-Za-z0-9_-]+$/;

function tomlKey(k: string): string {
  return BARE_KEY.test(k) ? k : JSON.stringify(k);
}

function tomlString(s: string): string {
  if (s.includes("\n") && !s.includes('"""')) return `"""\n${s.replace(/\\/g, "\\\\")}"""`;
  return JSON.stringify(s);
}

function tomlInline(v: unknown): string {
  if (v === null || v === undefined) throw new ParseError("TOML has no null: leave the key out instead");
  if (typeof v === "string") return tomlString(v);
  if (typeof v === "number") return Number.isNaN(v) ? "nan" : v === Infinity ? "inf" : v === -Infinity ? "-inf" : Number.isInteger(v) ? String(v) : String(v).includes(".") || /e/i.test(String(v)) ? String(v) : `${v}.0`;
  if (typeof v === "boolean") return v ? "true" : "false";
  if (Array.isArray(v)) return `[${v.filter((x) => x !== undefined && x !== null).map(tomlInline).join(", ")}]`;
  if (isObject(v)) {
    const entries = Object.entries(v).filter(([, x]) => x !== undefined && x !== null);
    return entries.length ? `{ ${entries.map(([k, x]) => `${tomlKey(k)} = ${tomlInline(x)}`).join(", ")} }` : "{}";
  }
  return JSON.stringify(v);
}

/** The server's style: scalars and arrays as `key = value`, objects as `[path.key]` subtables, lists of objects as `[[path.key]]`. */
function tomlTable(v: Tree, path: string[], out: string[]) {
  const entries = Object.entries(v).filter(([, x]) => x !== undefined && x !== null);
  const scalars = entries.filter(([, x]) => !isObject(x) && !(Array.isArray(x) && x.length > 0 && x.every(isObject)));
  const tables = entries.filter(([, x]) => isObject(x));
  const arrays = entries.filter(([, x]) => Array.isArray(x) && x.length > 0 && x.every(isObject));
  for (const [k, x] of scalars) out.push(`${tomlKey(k)} = ${tomlInline(x)}`);
  for (const [k, x] of tables) {
    const p = [...path, k];
    if (out.length) out.push("");
    out.push(`[${p.map(tomlKey).join(".")}]`);
    tomlTable(x as Tree, p, out);
  }
  for (const [k, x] of arrays) {
    const p = [...path, k];
    for (const item of x as Tree[]) {
      if (out.length) out.push("");
      out.push(`[[${p.map(tomlKey).join(".")}]]`);
      tomlTable(item, p, out);
    }
  }
}

export function dumpToml(tree: unknown): string {
  if (!isObject(tree)) throw new ParseError("a TOML document is a table");
  const out: string[] = [];
  tomlTable(tree, [], out);
  return `${out.join("\n")}\n`;
}
