// Sign in as each demo persona and open every page in the navigation, recording what breaks: an uncaught error,
// a server error, a console error that is not an expected refusal, or an error panel on screen.
//
//   BASE=http://localhost:3014 node smoke-views.mjs [persona ...]
//
// Writes smoke-views.json beside it and prints one line per persona. Refusals (401, 403, 404) are how the
// platform says no, so they are counted apart, not as failures.
import { chromium } from "playwright";
import { readFileSync, writeFileSync } from "node:fs";

const BASE = process.env.BASE || "http://localhost:3014";
const STAFF = ["adviser", "operations", "compliance", "branch", "admin", "in-adviser", "in-portfolio-manager", "in-compliance"];
const CLIENTS = ["client", "in-client"];
const CANVAS = ["/canvas", "/canvas/goals", "/canvas/retirement", "/canvas/questionnaire", "/canvas/next-gen"];
const ON_SCREEN = /Something went wrong|Application error|Unhandled Runtime Error|Couldn.t load|Failed to load/i;

const nav = readFileSync(new URL("../frontend/lib/nav.ts", import.meta.url), "utf8");
const routes = [...new Set([...nav.matchAll(/href: "([^"]+)"/g)].map((m) => m[1]))];
// Each persona opens the pages its role is offered, as nav.ts says; a page with no roles is everyone's.
const LETTER = { A: "adviser", P: "paraplanner", PT: "portfolio_team", R: "research_cio", C: "compliance", O: "operations", B: "branch_leader" };
const ROLE = { adviser: "adviser", "in-adviser": "adviser", operations: "operations", compliance: "compliance", "in-compliance": "compliance",
  branch: "branch_leader", "in-portfolio-manager": "portfolio_team", admin: "admin" };
const offered = {};
for (const m of nav.matchAll(/href: "([^"]+)"([^\n]*)/g)) {
  const roles = (m[2].match(/roles: \[([^\]]*)\]/)?.[1] ?? "").split(",").map((x) => LETTER[x.trim()]).filter(Boolean);
  offered[m[1]] = roles;
}
const pagesFor = (persona) => routes.filter((r) => !offered[r]?.length || offered[r].includes(ROLE[persona]) || ROLE[persona] === "admin" && r.startsWith("/admin"));
const wanted = process.argv.slice(2);
const personas = [...STAFF, ...CLIENTS].filter((p) => !wanted.length || wanted.includes(p));

const browser = await chromium.launch();
const report = {};
for (const persona of personas) {
  const context = await browser.newContext();
  const page = await context.newPage();
  let current = "";
  const found = [];
  page.on("pageerror", (e) => found.push({ route: current, kind: "uncaught", text: String(e.message).slice(0, 300) }));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    if (/status of (401|403|404)|Download the React DevTools|favicon/.test(text)) return;
    found.push({ route: current, kind: "console", text: text.slice(0, 300) });
  });
  page.on("response", (r) => {
    if (r.status() >= 500) found.push({ route: current, kind: "server", text: `${r.status()} ${r.url()}` });
  });
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle", timeout: 120000 });
  await page.click(`[data-testid="login-persona-${persona}"]`);
  // A dev server compiles a page the first time it is opened, which can take minutes.
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 300000 });
  const list = CLIENTS.includes(persona) ? CANVAS : pagesFor(persona);
  let refused = 0;
  for (const route of list) {
    current = route;
    // Some pages poll, so the network never goes quiet: wait for the load and then a moment for data.
    const res = await page.goto(`${BASE}${route}`, { waitUntil: "load", timeout: 180000 }).catch((e) => {
      found.push({ route, kind: "navigation", text: String(e.message).slice(0, 200) });
      return null;
    });
    if (res && res.status() >= 400) refused++;
    await page.waitForTimeout(3000);
    if (process.env.VERBOSE) console.log(`  ${persona} ${route}`);
    const text = await page.locator("body").innerText().catch(() => "");
    const hit = text.match(ON_SCREEN);
    if (hit) found.push({ route, kind: "on_screen", text: hit[0] });
  }
  report[persona] = { pages: list.length, problems: found };
  console.log(`${persona.padEnd(22)} pages=${list.length} problems=${found.length}`);
  await context.close();
}
await browser.close();
writeFileSync(new URL("./smoke-views.json", import.meta.url), JSON.stringify(report, null, 2));
