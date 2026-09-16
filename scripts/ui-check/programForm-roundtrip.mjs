#!/usr/bin/env node
/**
 * Text ⇄ form fidelity of the program builder's document model (programDoc.ts).
 *
 * For every example program (examples/{*}/programs/*.yaml) and every step:
 *   - the form's data handed straight back (`fromForm(toForm(step))`, the time
 *     field carried over as the builder does) rebuilds the step's arguments
 *     unchanged, so an untouched form never rewrites the file;
 *   - every saved argument, every `set: values` key and every `command: args`
 *     key has a field of its own on the form, under three rig models: no rig
 *     known yet, the daemon's own rig (`GET /api/schema`), and a rig with no
 *     devices at all -- a device, command or signal the rig lacks is still
 *     shown, never dropped;
 *   - a saved `device` / `device_command` is one of the pick's options, and
 *     the options never offer a simulation-only command that is not the saved one.
 *
 * Needs a daemon for the dialect (`GET /api/programs/schema`) and its rig.
 * Usage: node programForm-roundtrip.mjs [api-url]   (default http://127.0.0.1:8145)
 */
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const REPO = "/home/ben/flyball";
const UI = path.join(REPO, "ui");
const SRC = path.join(UI, "apps/dashboard/src");
const api = process.argv[2] ?? "http://127.0.0.1:8145";

// CJS bundles, as programText-roundtrip.mjs does (yaml's conditional `require`).
const buildDir = mkdtempSync(path.join(tmpdir(), "programForm-roundtrip-"));
const bundle = (name) => {
  const out = path.join(buildDir, `${name}.cjs`);
  execFileSync(path.join(UI, "node_modules/.bin/esbuild"), [path.join(SRC, `${name}.ts`), "--bundle", "--platform=node", "--format=cjs", "--target=node18", `--outfile=${out}`], { cwd: UI, stdio: "pipe" });
  return createRequire(import.meta.url)(out);
};
const { parseText } = bundle("programText");
const { argsOf, asProgram, commandsOf, formShape, fromForm, inOrder, splitStep, toForm } = bundle("programDoc");

const programSchema = await (await fetch(`${api}/api/programs/schema`)).json();
const rigSchema = await (await fetch(`${api}/api/schema`)).json();
const commands = commandsOf(programSchema);
const byTag = Object.fromEntries(commands.map((c) => [c.tag, c]));

// The same DevicePicks Programs.tsx builds, from the daemon's rig.
const all = Object.values(rigSchema.devices);
const isWritable = (s) => s.access.includes("w");
const rigPicks = {
  names: all.map((d) => d.name),
  commands: Object.fromEntries(all.filter((d) => Object.keys(d.commands).length > 0).map((d) => [d.name, d.commands])),
  writable: Object.fromEntries(all.map((d) => [d.name, Object.fromEntries(Object.entries(d.signals).filter(([, s]) => isWritable(s)))]).filter(([, s]) => Object.keys(s).length > 0)),
};
const models = { "no rig yet": undefined, [`rig ${rigSchema.name ?? ""}`.trim()]: rigPicks, "empty rig": { names: [], commands: {}, writable: {} } };
const simulationCommands = new Set(all.flatMap((d) => Object.entries(d.commands).filter(([, c]) => c.simulation).map(([name]) => `${d.name}.${name}`)));

let failures = 0;
let steps = 0;
let programs = 0;
const fail = (where, what) => {
  failures++;
  console.log(`FAIL ${where}: ${what}`);
};

const canon = (v) => (Array.isArray(v) ? v.map(canon) : v && typeof v === "object" ? Object.fromEntries(Object.keys(v).map((k) => [k, canon(v[k])])) : v);
/** `loop: "x"` and `loop: ["x"]` say the same; the form holds the list. */
const loopAsList = (args) => (typeof args.loop === "string" ? { ...args, loop: [args.loop] } : args);
const same = (a, b) => JSON.stringify(canon(a)) === JSON.stringify(canon(b));
const options = (field) => field.enum ?? (field.oneOf ? field.oneOf.map((o) => o.const) : null);

function findPrograms() {
  const files = [];
  for (const example of readdirSync(path.join(REPO, "examples"))) {
    const dir = path.join(REPO, "examples", example, "programs");
    if (!existsSync(dir)) continue;
    for (const f of readdirSync(dir)) if (f.endsWith(".yaml") || f.endsWith(".yml")) files.push(path.join(dir, f));
  }
  return files;
}

for (const file of findPrograms()) {
  const rel = path.relative(REPO, file);
  const tree = asProgram(parseText(readFileSync(file, "utf8"), "yaml"));
  if (!tree) {
    fail(rel, "not a program");
    continue;
  }
  programs++;
  tree.steps.forEach((step, i) => {
    steps++;
    const where = `${rel} step ${i + 1}`;
    const { tag, value } = splitStep(step, commands);
    const command = tag ? byTag[tag] : undefined;
    if (!command) return fail(where, `no command for ${JSON.stringify(step)}`);
    const current = argsOf(value, command);
    const timeKeys = command.time?.keys ?? [];

    // 1. an untouched form rebuilds the arguments as the builder's onArgs would
    const form = toForm(value, command);
    const time = Object.fromEntries(Object.entries(current).filter(([k]) => timeKeys.includes(k)));
    const rebuilt = inOrder({ ...fromForm(form, command), ...time }, current);
    if (!same(rebuilt, loopAsList(current))) fail(where, `untouched form rewrites the step: ${JSON.stringify(current)} -> ${JSON.stringify(rebuilt)}`);

    // 2. every saved value is on the form, whatever the rig has
    for (const [model, devices] of Object.entries(models)) {
      const { schema } = formShape(command, programSchema, devices ? ["a.controller"] : undefined, devices, form);
      const props = schema.properties ?? {};
      for (const key of Object.keys(form)) if (!(key in props)) fail(`${where} [${model}]`, `saved argument '${key}' has no field`);
      const nested = tag === "set" ? "values" : tag === "command" ? "args" : null;
      if (nested && form[nested] && typeof form[nested] === "object") {
        const inner = props[nested]?.properties ?? {};
        const hasAll = props[nested]?.additionalProperties !== undefined && props[nested]?.additionalProperties !== false;
        for (const key of Object.keys(form[nested])) if (!(key in inner) && !hasAll) fail(`${where} [${model}]`, `saved ${nested}.${key} has no field`);
      }
      for (const key of ["device", "device_command"]) {
        const opts = props[key] ? options(props[key]) : null;
        if (typeof form[key] === "string" && opts && !opts.includes(form[key])) fail(`${where} [${model}]`, `saved ${key} '${form[key]}' is not among the pick's options ${JSON.stringify(opts)}`);
        if (key === "device_command" && opts && typeof form.device === "string") {
          for (const o of opts) if (o !== form[key] && simulationCommands.has(`${form.device}.${o}`)) fail(`${where} [${model}]`, `simulation command '${o}' is offered`);
        }
      }
    }
  });
}

console.log(`\n${programs} program(s), ${steps} step(s) checked against ${Object.keys(models).length} rig models, ${failures} failure(s).`);
rmSync(buildDir, { recursive: true, force: true });
process.exit(failures ? 1 : 0);
