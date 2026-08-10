/**
 * Re-capture pages that need a longer load wait.
 */
import { chromium } from "playwright";
import { fileURLToPath } from "url";
import path from "path";

const BASE = "http://localhost:3010";
const OUT = "C:/Users/swarupd/AppData/Local/Temp/claude/C--Users-swarupd-demo-Aurea/addc3582-8cc8-4ab6-bdf4-d45c30ee0fbd/scratchpad/shots";
const SIZE = { width: 1440, height: 900 };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function shot(page, name) {
  const file = `${OUT}/${name}.png`;
  await page.screenshot({ path: file, type: "png" });
  console.log("  ✓", name);
}

async function waitForSelector(page, sel, timeout = 8000) {
  try { await page.waitForSelector(sel, { state: "visible", timeout }); } catch {}
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: SIZE, deviceScaleFactor: 1.5 });
  const page = await ctx.newPage();

  // Login
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await sleep(800);
  await page.locator("button").filter({ hasText: "Studio cockpit" }).click({ timeout: 8000 });
  await page.waitForURL((u) => !u.href.includes("/login"), { timeout: 20000 });
  await sleep(3000);

  // Cockpit — wait for NBA feed to load (look for a real card, not skeleton)
  await page.goto(`${BASE}/studio`, { waitUntil: "domcontentloaded" });
  await sleep(2500);
  // Scroll down to open the first NBA card
  await page.mouse.wheel(0, 300);
  await sleep(1500);
  await shot(page, "01-cockpit");

  // Cockpit with expanded recommendation
  await page.mouse.wheel(0, 300);
  await sleep(1000);
  await shot(page, "01b-cockpit-rail");

  // Analytics — wait much longer for the heavy data load
  await page.goto(`${BASE}/studio/analytics`, { waitUntil: "domcontentloaded" });
  // Wait for the loading spinner to disappear
  await sleep(1000);
  try {
    await page.waitForSelector("text=Loading analytics", { state: "detached", timeout: 15000 });
  } catch {}
  await sleep(3000);
  await shot(page, "05-analytics");

  // Analytics portfolio tab
  try {
    await page.locator('button:has-text("Portfolio")').first().click({ timeout: 3000 });
    await sleep(1500);
    await shot(page, "05b-analytics-portfolio");
  } catch { console.log("  - no Portfolio tab found"); }

  await browser.close();
  console.log("\nDone.");
}

main().catch((e) => { console.error(e); process.exit(1); });
