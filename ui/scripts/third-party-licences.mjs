#!/usr/bin/env node
// Collects the licence text of every package that ends up in the shipped
// dashboard bundle, into dist/third-party-licences.txt.
//
// MIT, BSD-3-Clause and Apache-2.0 all require their notice to travel with a
// *binary* redistribution, and the bundle is exactly that: minified, embedded
// into the flyball binary with go:embed, and served to whoever opens the UI.
// Only the `/*! ... */` banners a package happened to write survive
// minification, which is a handful of them, so the rest have to be collected
// here.
//
// Production dependencies only -- devDependencies are build-time and are not
// redistributed. `npm ls --omit=dev` resolves the real closure rather than
// guessing from package.json.
//
//   node scripts/third-party-licences.mjs <out-file>
//
// Run from apps/dashboard as part of its build.

import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, readdirSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

const out = resolve(process.argv[2] ?? "dist/third-party-licences.txt");

// --all so transitive dependencies are included: they ship too.
const tree = JSON.parse(
  execFileSync("npm", ["ls", "--omit=dev", "--all", "--json", "--long"], {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  }),
);

/** Every distinct package in the production closure, by name@version. */
const seen = new Map();
/** Listed by npm but with no directory on disk -- reported, never dropped. */
const unresolved = new Set();
const walk = (node) => {
  for (const [name, dep] of Object.entries(node.dependencies ?? {})) {
    // Our own workspace packages are covered by the root LICENSE, not here.
    // npm symlinks them into node_modules, so the path does not give them away.
    if (!name.startsWith("@flyball/")) {
      const key = `${name}@${dep.version ?? "?"}`;
      // No path means npm knows of it but it is not installed -- an unmet
      // optional peer, reported as a bare {} with no flag to tell it apart.
      // Rather than re-deriving which of those matter, lean on npm's own
      // verdict: `npm ls` exits non-zero when a *required* dependency is
      // missing, and execFileSync turns that into a throw that fails the
      // build. So anything still here is npm saying "known, not installed,
      // fine" -- and it is listed in the file rather than silently dropped,
      // because a notices file that looks complete and is not is worse than
      // no notices file.
      if (!dep.path) unresolved.add(key);
      else if (!seen.has(key)) seen.set(key, dep);
    }
    walk(dep);
  }
};
walk(tree);

/** The licence file a package ships, if it ships one. */
const licenceText = (path) => {
  if (!path || !existsSync(path)) return null;
  const file = readdirSync(path).find((f) => /^(licen[cs]e|copying)/i.test(f));
  if (!file) return null;
  try {
    return readFileSync(join(path, file), "utf8").trim();
  } catch {
    return null;
  }
};

const entries = [...seen.entries()].sort(([a], [b]) => a.localeCompare(b));
const missing = [];
const blocks = [];

for (const [key, dep] of entries) {
  const text = licenceText(dep.path);
  const declared = dep.license ?? dep.licenses ?? "not declared";
  if (!text) {
    missing.push(`${key} (${declared})`);
    continue;
  }
  blocks.push(`${"=".repeat(72)}\n${key}  --  ${declared}\n${"=".repeat(72)}\n\n${text}\n`);
}

const header = [
  "Third-party licences",
  "====================",
  "",
  "The flyball dashboard bundles the packages below. Their licence texts follow,",
  "reproduced as those licences require. flyball's own licence is in LICENSE at",
  "the repository root.",
  "",
  `${entries.length} packages, ${blocks.length} with a licence file included.`,
  "",
];

if (unresolved.size) {
  header.push(
    "Listed by npm but not installed here (unmet optional peers), so nothing of",
    "theirs is in the bundle and no licence of theirs is reproduced:",
    "",
    ...[...unresolved].sort().map((k) => `  ${k}`),
    "",
  );
}

if (missing.length) {
  header.push(
    "These ship no licence file of their own; their declared licence is noted and",
    "the canonical text of that licence applies:",
    "",
    ...missing.map((m) => `  ${m}`),
    "",
  );
}

writeFileSync(out, `${header.join("\n")}\n${blocks.join("\n")}`);
console.log(
  `third-party-licences.txt: ${entries.length} packages, ${blocks.length} texts, ` +
    `${missing.length} without a file, ${unresolved.size} not installed`,
);
