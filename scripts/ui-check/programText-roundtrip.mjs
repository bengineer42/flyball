#!/usr/bin/env node
/**
 * Round-trip check for the yaml/smol-toml-backed programText.ts.
 *
 * For every example program (examples/simulated/programs/*.yaml and
 * examples/stress/programs/*.yaml, if present): parse the source, dump it in
 * each of yaml/toml/json, parse each dump again, and assert deep equality
 * with the first parse. Also cross-checks the first parse against the
 * Python side (`uv run flyball program check <file>` and a plain
 * `python -c "import yaml, json"` load) so the browser and the daemon are
 * reading the same document.
 *
 * Usage: node programText-roundtrip.mjs
 */
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const REPO = "/home/ben/flyball";
const UI = path.join(REPO, "ui");
const SRC = path.join(UI, "apps/dashboard/src/programText.ts");
const ENGINE = path.join(REPO, "engine");

// --- build a self-contained CJS bundle of programText.ts (yaml/smol-toml inlined) ---
// CJS (not ESM) because yaml's compose module has a conditional `require("process")`
// that esbuild can only leave as a real `require` call, not shim for ESM output.
const buildDir = mkdtempSync(path.join(tmpdir(), "programText-roundtrip-"));
const bundlePath = path.join(buildDir, "programText.bundle.cjs");
execFileSync(path.join(UI, "node_modules/.bin/esbuild"), [SRC, "--bundle", "--platform=node", "--format=cjs", "--target=node18", `--outfile=${bundlePath}`], { cwd: UI, stdio: "inherit" });
const { parseText, dumpText } = createRequire(import.meta.url)(bundlePath);

let failures = 0;
let checked = 0;
const log = (...args) => console.log(...args);

function findPrograms() {
  const dirs = [path.join(REPO, "examples/simulated/programs"), path.join(REPO, "examples/stress/programs")];
  const files = [];
  for (const dir of dirs) {
    if (!existsSync(dir)) continue;
    for (const f of readdirSync(dir)) if (f.endsWith(".yaml") || f.endsWith(".yml")) files.push(path.join(dir, f));
  }
  return files;
}

/** Deep equality that treats -0/NaN sanely (JSON.stringify already normalises key order concerns since we compare parsed structures, not text). */
function deepEqual(a, b) {
  if (Object.is(a, b)) return true;
  if (typeof a === "number" && typeof b === "number" && Number.isNaN(a) && Number.isNaN(b)) return true;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((x, i) => deepEqual(x, b[i]));
  }
  if (a && b && typeof a === "object" && typeof b === "object") {
    const ak = Object.keys(a),
      bk = Object.keys(b);
    if (ak.length !== bk.length) return false;
    return ak.every((k) => Object.prototype.hasOwnProperty.call(b, k) && deepEqual(a[k], b[k]));
  }
  return false;
}

function pythonLoad(file) {
  // Cross-check against the daemon's own YAML loader (PyYAML) so the browser
  // and the daemon agree on what the document means, not just that our own
  // dump/parse round-trips.
  const out = execFileSync("uv", ["run", "python", "-c", "import sys, yaml, json; print(json.dumps(yaml.safe_load(open(sys.argv[1]).read())))", file], { cwd: ENGINE, encoding: "utf8" });
  return JSON.parse(out);
}

function pythonCheck(file) {
  try {
    execFileSync("uv", ["run", "flyball", "program", "check", "--local", file], { cwd: ENGINE, stdio: "pipe" });
    return { ok: true };
  } catch (e) {
    return { ok: false, output: `${e.stdout ?? ""}${e.stderr ?? ""}` };
  }
}

for (const file of findPrograms()) {
  const rel = path.relative(REPO, file);
  const source = readFileSync(file, "utf8");
  let first;
  try {
    first = parseText(source, "yaml");
  } catch (e) {
    failures++;
    log(`FAIL ${rel}: our yaml parser rejected the source: ${e instanceof Error ? e.message : e}`);
    continue;
  }
  checked++;

  for (const format of ["yaml", "toml", "json"]) {
    try {
      const dumped = dumpText(first, format);
      const reparsed = parseText(dumped, format);
      if (!deepEqual(first, reparsed)) {
        failures++;
        log(`FAIL ${rel} [${format}]: round-trip changed the tree`);
        log(`  first:    ${JSON.stringify(first).slice(0, 300)}`);
        log(`  reparsed: ${JSON.stringify(reparsed).slice(0, 300)}`);
      }
    } catch (e) {
      failures++;
      log(`FAIL ${rel} [${format}]: ${e instanceof Error ? e.message : e}`);
    }
  }

  // cross-check against the Python side: the daemon's PyYAML load must see the same document
  try {
    const py = pythonLoad(file);
    if (!deepEqual(first, py)) {
      failures++;
      log(`FAIL ${rel}: PyYAML disagrees with our parse`);
      log(`  ours:   ${JSON.stringify(first).slice(0, 300)}`);
      log(`  python: ${JSON.stringify(py).slice(0, 300)}`);
    }
  } catch (e) {
    failures++;
    log(`FAIL ${rel}: could not run the python cross-check: ${e.message}`);
  }

  const check = pythonCheck(file);
  if (!check.ok) {
    failures++;
    log(`FAIL ${rel}: 'flyball program check' rejected the file:\n${check.output}`);
  }
}

log(`\n${checked} program(s) checked, ${failures} failure(s).`);
rmSync(buildDir, { recursive: true, force: true });
process.exit(failures ? 1 : 0);
