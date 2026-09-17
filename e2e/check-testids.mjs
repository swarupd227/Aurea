// Fail if an e2e script targets a data-testid the frontend does not render.
//
//   node e2e/check-testids.mjs
//
// The demo recorder swallows click failures on purpose, so a selector that matches nothing
// does not stop the recording — it just leaves a step that quietly does nothing while its
// caption describes something happening. Three steps were already in that state: a "Book
// scan" click on a page with no scan button, a "Quality" tab that does not exist, and a
// revise note that was typed but never submitted.
//
// Needs no browser and no running app, so it can run anywhere: it reads the e2e scripts and
// the frontend source and compares the ids. Ids built from a template literal
// (data-testid={`analytics-tab-${t.id}`}) are matched on their static prefix.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(fileURLToPath(import.meta.url), "..", "..");
const E2E = join(ROOT, "e2e");
const FRONTEND = join(ROOT, "frontend");

function walk(dir, exts, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name === ".next" || name.startsWith(".")) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, exts, out);
    else if (exts.some((e) => name.endsWith(e))) out.push(p);
  }
  return out;
}

// What the frontend renders.
const exact = new Set();
const prefixes = new Set();
for (const file of walk(join(FRONTEND, "app"), [".tsx", ".ts"]).concat(walk(join(FRONTEND, "components"), [".tsx", ".ts"]))) {
  const src = readFileSync(file, "utf8");
  for (const m of src.matchAll(/data-testid="([^"]+)"/g)) exact.add(m[1]);
  for (const m of src.matchAll(/data-testid=\{`([^`$]*)\$\{/g)) prefixes.add(m[1]);
}

// What the e2e scripts expect.
const wanted = [];
for (const file of walk(E2E, [".mjs"])) {
  if (file.endsWith("check-testids.mjs")) continue;
  const src = readFileSync(file, "utf8");
  src.split("\n").forEach((line, i) => {
    for (const m of line.matchAll(/data-testid="([^"]+)"/g)) {
      wanted.push({ id: m[1], where: `${relative(ROOT, file)}:${i + 1}` });
    }
  });
}

const missing = wanted.filter(({ id }) =>
  !exact.has(id) && ![...prefixes].some((p) => p && id.startsWith(p)));

console.log(`frontend renders ${exact.size} fixed test id(s) and ${prefixes.size} templated prefix(es)`);
console.log(`e2e scripts reference ${new Set(wanted.map((w) => w.id)).size} distinct test id(s)`);

if (missing.length) {
  console.log(`\n${missing.length} reference(s) to a test id the frontend does not render:`);
  for (const { id, where } of missing) console.log(`  ${id.padEnd(32)} ${where}`);
  process.exit(1);
}
console.log("\nevery referenced test id exists");
