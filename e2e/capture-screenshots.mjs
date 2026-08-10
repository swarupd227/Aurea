/**
 * Capture key app screenshots for the deck.
 * Saves PNGs to scratchpad/shots/
 */
import { chromium } from "playwright";
import { fileURLToPath } from "url";
import path from "path";
import fs from "fs";

const BASE = "http://localhost:3010";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = "C:\\Users\\swarupd\\AppData\\Local\\Temp\\claude\\C--Users-swarupd-demo-Aurea\\addc3582-8cc8-4ab6-bdf4-d45c30ee0fbd\\scratchpad\\shots";
fs.mkdirSync(OUT, { recursive: true });

const SIZE = { width: 1440, height: 900 };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function shot(page, name) {
  await sleep(1200);
  const file = path.join(OUT, `${name}.png`);
  await page.screenshot({ path: file, type: "png" });
  console.log("  ✓", name);
  return file;
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: SIZE, deviceScaleFactor: 1.5 });
  const page = await ctx.newPage();

  // Login as adviser
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await sleep(800);
  const btn = page.locator("button").filter({ hasText: "Studio cockpit" });
  await btn.click({ timeout: 8000 });
  await page.waitForURL((u) => !u.href.includes("/login"), { timeout: 20000 });
  await sleep(2000);

  // 1. Cockpit
  await shot(page, "01-cockpit");

  // scroll down a bit to show activity rail
  await page.mouse.wheel(0, 500);
  await sleep(800);
  await shot(page, "01b-cockpit-rail");

  // 2. Clients list
  await page.goto(`${BASE}/studio/clients`, { waitUntil: "domcontentloaded" });
  await sleep(1500);
  await shot(page, "02-clients");

  // 3. Workforce
  await page.goto(`${BASE}/studio/workforce`, { waitUntil: "domcontentloaded" });
  await sleep(1500);
  await shot(page, "03-workforce");

  // 4. Recommendation review (drift)
  await page.goto(`${BASE}/studio/review`, { waitUntil: "domcontentloaded" });
  await sleep(1800);
  await shot(page, "04-review");
  await page.mouse.wheel(0, 600);
  await sleep(800);
  await shot(page, "04b-review-detail");

  // 5. Analytics overview
  await page.goto(`${BASE}/studio/analytics`, { waitUntil: "domcontentloaded" });
  await sleep(1800);
  await shot(page, "05-analytics");

  // 6. Analytics portfolio tab
  try {
    await page.locator('button:has-text("Portfolio")').first().click({ timeout: 3000 });
    await sleep(800);
    await shot(page, "05b-analytics-portfolio");
  } catch {}

  // 7. Canvas (client view)
  await page.goto(`${BASE}/canvas`, { waitUntil: "domcontentloaded" });
  await sleep(2000);
  await shot(page, "06-canvas");
  await page.mouse.wheel(0, 500);
  await sleep(800);
  await shot(page, "06b-canvas-goals");

  // 8. Provenance
  await page.goto(`${BASE}/provenance`, { waitUntil: "domcontentloaded" });
  await sleep(1800);
  await shot(page, "07-provenance");

  // 9. Skills
  await page.goto(`${BASE}/studio/skills`, { waitUntil: "domcontentloaded" });
  await sleep(1500);
  await shot(page, "08-skills");

  // 10. Meetings
  await page.goto(`${BASE}/studio/meetings`, { waitUntil: "domcontentloaded" });
  await sleep(1500);
  await shot(page, "09-meetings");

  await browser.close();
  console.log("\nDone. Files in:", OUT);
}

main().catch((e) => { console.error(e); process.exit(1); });
