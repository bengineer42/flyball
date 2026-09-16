#!/usr/bin/env node
/**
 * WCAG contrast for every fg/bg token pair, in both themes, read straight out of
 * `packages/react/src/styles.css` (the one source of truth for `--fb-*`) so this
 * never drifts from the tokens actually shipped. Usage: `node contrast.mjs`.
 *
 * Pass thresholds (DESIGN-SPEC.md §1.1): body text >= 4.5:1, tertiary text >= 3:1,
 * every semantic colour >= 4.3:1 against the surfaces it appears on.
 */
import { readFileSync } from "node:fs";

const CSS_PATH = process.argv[2] ?? new URL("../../ui/packages/react/src/styles.css", import.meta.url).pathname;
const css = readFileSync(CSS_PATH, "utf8");

/** Pull the body of one `:root...{ ... }` block out of the stylesheet, by a marker on its selector line. */
function block(selectorRe) {
  const m = selectorRe.exec(css);
  if (!m) throw new Error(`selector not found: ${selectorRe}`);
  const start = m.index + m[0].length;
  let depth = 1,
    i = start;
  while (depth > 0 && i < css.length) {
    if (css[i] === "{") depth++;
    else if (css[i] === "}") depth--;
    i++;
  }
  return css.slice(start, i - 1);
}

/** `--fb-x: value;` pairs in a block, values left as raw text (may reference `var(--fb-y)`). */
function vars(body) {
  const out = new Map();
  for (const m of body.matchAll(/--fb-([a-zA-Z0-9-]+)\s*:\s*([^;]+);/g)) out.set(m[1], m[2].trim());
  return out;
}

// Light is the first bare `:root {`; dark is the explicit `:root[data-theme="dark"] {` block
// (identical, per styles.css's comment, to the one under the OS media query).
const lightBody = block(/:root\s*\{/);
const darkBody = block(/:root\[data-theme="dark"\]\s*\{/);
const light = vars(lightBody);
const dark = vars(darkBody);

/** Resolve `var(--fb-x)` chains and `rgba(var lookalike)` isn't a thing here — values are literal colours or `var()`. */
function resolve(name, table, seen = new Set()) {
  if (seen.has(name)) throw new Error(`cycle resolving --fb-${name}`);
  seen.add(name);
  const raw = table.get(name);
  if (raw === undefined) return undefined;
  const m = /^var\(--fb-([a-zA-Z0-9-]+)\)$/.exec(raw);
  return m ? resolve(m[1], table, seen) : raw;
}

function parseColor(str) {
  str = str.trim();
  let m = /^#([0-9a-f]{6})$/i.exec(str);
  if (m) {
    const n = parseInt(m[1], 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255, a: 1 };
  }
  m = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i.exec(str);
  if (m) return { r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4] };
  return null;
}

/** Composite `fg` (possibly translucent) over an opaque `bg`. */
function over(fg, bg) {
  const a = fg.a;
  return { r: fg.r * a + bg.r * (1 - a), g: fg.g * a + bg.g * (1 - a), b: fg.b * a + bg.b * (1 - a), a: 1 };
}

function luminance({ r, g, b }) {
  const f = (c) => {
    c /= 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function contrast(fgStr, bgStr, table) {
  const bgRaw = parseColor(bgStr);
  if (!bgRaw) return null;
  const bg = over(bgRaw, { r: 255, g: 255, b: 255, a: 1 }); // surfaces here are opaque already
  const fgParsed = parseColor(fgStr);
  if (!fgParsed) return null;
  const fg = fgParsed.a < 1 ? over(fgParsed, bg) : fgParsed;
  const L1 = luminance(fg) + 0.05,
    L2 = luminance(bg) + 0.05;
  return L1 > L2 ? L1 / L2 : L2 / L1;
}

// The pairs the spec cares about: text ranks and semantic colours, against every
// background layer they are actually used on (panel bg-1 is the common case; bg-0
// canvas and bg-2 inset are checked too since labels and fills sit there as well).
const TEXT = ["fg", "fg-2", "fg-3", "accent", "ok", "warn", "alarm", "stale"];
const SURFACES = ["bg-0", "bg-1", "bg-2"];
const THRESHOLD = { fg: 4.5, "fg-2": 4.5, "fg-3": 3.0, accent: 4.3, ok: 4.3, warn: 4.3, alarm: 4.3, stale: 3.0 };

function report(mode, table) {
  console.log(`\n${mode}`);
  console.log("token".padEnd(10), "surface".padEnd(8), "ratio".padEnd(8), "need", "pass");
  let fails = 0;
  for (const t of TEXT) {
    const fgVal = resolve(t, table);
    if (fgVal === undefined) continue;
    for (const s of SURFACES) {
      const bgVal = resolve(s, table);
      const ratio = contrast(fgVal, bgVal, table);
      if (ratio === null) continue;
      const need = THRESHOLD[t] ?? 3.0;
      const pass = ratio >= need;
      if (!pass) fails++;
      console.log(
        `--fb-${t}`.padEnd(10),
        `--fb-${s}`.padEnd(8),
        ratio.toFixed(2).padEnd(8),
        String(need).padEnd(4),
        pass ? "PASS" : "FAIL",
      );
    }
  }
  return fails;
}

let fails = 0;
fails += report("light", light);
fails += report("dark", dark);

// Series-vs-panel, since charts always sit on bg-1 (DESIGN-SPEC.md §1.2 obligation: every slot >= 3:1).
function reportSeries(mode, table) {
  console.log(`\n${mode} series vs bg-1`);
  const bg1 = resolve("bg-1", table);
  let fails = 0;
  for (let i = 1; i <= 8; i++) {
    const c = resolve(`series-${i}`, table);
    if (c === undefined) continue;
    const ratio = contrast(c, bg1, table);
    const pass = ratio >= 3.0;
    if (!pass) fails++;
    console.log(`--fb-series-${i}`.padEnd(16), ratio.toFixed(2).padEnd(8), pass ? "PASS" : "FAIL");
  }
  return fails;
}
fails += reportSeries("light", light);
fails += reportSeries("dark", dark);

console.log(`\n${fails === 0 ? "all pairs pass" : `${fails} pair(s) below threshold`}`);
process.exit(fails === 0 ? 0 : 1);
