/**
 * Aurea — Captioned Demo Video v9
 *
 *  ✔ Branded intro slide (about:blank overlay, ARTIZENT + Aurea)
 *  ✔ Animated closing slide (about:blank, staggered reveal, ARTIZENT tagline)
 *  ✔ Login via "Adviser" persona button
 *  ✔ waitUntil: "domcontentloaded" on SSE pages
 *  ✔ Optimised timing ~4:45
 *
 *   node record-demo.mjs          (stack must be running: docker compose up)
 */
import { chromium } from "playwright";
import { fileURLToPath } from "url";
import path from "path";

const BASE = process.env.AUREA_URL || "http://localhost:3010";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const VIDEO_DIR = path.join(__dirname, "videos");
const SIZE = { width: 1366, height: 820 };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── Aurea mark SVG ────────────────────────────────────────────────────────────
const AUREA_MARK = (size = 52) => `
  <svg width="${size}" height="${size}" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="16" cy="16" r="15" stroke="#c8a35e" stroke-width="1.3"/>
    <path d="M16 7l6 11H10l6-11z" fill="#c8a35e" opacity="0.9"/>
    <circle cx="16" cy="20" r="2.4" fill="#0f2b3d"/>
  </svg>`;

// ── caption helpers ───────────────────────────────────────────────────────────

async function applyCaption(page, { brand, brandColor, text, bg, textColor, border }) {
  await page.evaluate(({ brand, brandColor, text, bg, textColor, border }) => {
    let bar = document.getElementById("demo-cap");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "demo-cap";
      Object.assign(bar.style, {
        position: "fixed", left: "0", right: "0", bottom: "0", zIndex: "2147483647",
        padding: "15px 28px", textAlign: "center", pointerEvents: "none",
        font: "500 18px/1.5 Inter, system-ui, sans-serif", letterSpacing: "0.2px",
        boxShadow: "0 -8px 24px rgba(0,0,0,0.3)",
      });
      const b = document.createElement("span");
      b.id = "demo-cap-brand";
      Object.assign(b.style, { fontWeight: "700", letterSpacing: "1.5px", marginRight: "10px" });
      bar.appendChild(b);
      const m = document.createElement("span");
      m.id = "demo-cap-text";
      bar.appendChild(m);
      document.body.appendChild(bar);
    }
    bar.style.background = bg;
    bar.style.color = textColor;
    bar.style.borderTop = border;
    document.getElementById("demo-cap-brand").textContent = brand;
    document.getElementById("demo-cap-brand").style.color = brandColor;
    document.getElementById("demo-cap-text").textContent = text;
  }, { brand, brandColor, text, bg, textColor, border });
}

const caption = (page, text) => applyCaption(page, {
  brand: "AUREA", brandColor: "#c8a35e", text,
  bg: "linear-gradient(to top, rgba(13,26,37,0.97), rgba(13,26,37,0.88))",
  textColor: "#f0f0f0", border: "2px solid #c8a35e",
});
const diffCaption = (page, text) => applyCaption(page, {
  brand: "★  AUREA", brandColor: "#f5c842", text: "  " + text,
  bg: "linear-gradient(to top, rgba(31,18,2,0.98), rgba(31,18,2,0.90))",
  textColor: "#fff8ed", border: "2px solid #f5c842",
});
const sectionCaption = (page, text) => applyCaption(page, {
  brand: "——", brandColor: "#c8a35e", text: "  " + text,
  bg: "rgba(22,58,82,0.98)", textColor: "#e8d9b8", border: "2px solid #c8a35e",
});

// ── helpers ───────────────────────────────────────────────────────────────────

function makeStep(page) {
  return async function step(cap, capFn, ms, fn) {
    await capFn(cap).catch(() => {});
    if (fn) {
      try { await fn(); } catch (e) { console.warn("  SKIP:", e.message.split("\n")[0]); }
    }
    await capFn(cap).catch(() => {});
    console.log(`  • [${String(ms).padStart(4)}ms] ${cap.slice(0, 72)}`);
    await sleep(ms);
  };
}

async function tryNav(page, url) {
  try {
    await page.goto(`${BASE}${url}`, { waitUntil: "domcontentloaded", timeout: 20000 });
    await sleep(900);
  } catch (e) {
    console.warn("  NAV WARN:", url, e.message.split("\n")[0]);
  }
}

async function tryClick(page, selectors) {
  const list = Array.isArray(selectors) ? selectors : [selectors];
  for (const sel of list) {
    try {
      await page.locator(sel).first().click({ timeout: 3000 });
      return true;
    } catch { /* next */ }
  }
  return false;
}

const scroll = (page, dy) => page.mouse.wheel(0, dy).then(() => sleep(500));

// ── INTRO SLIDE ───────────────────────────────────────────────────────────────
async function showIntroSlide(page, logoUri, aureaMark) {
  await page.goto("about:blank");
  await page.evaluate(({ logoUri, aureaMark }) => {
    document.documentElement.style.cssText = "height:100%;margin:0;";
    document.body.style.cssText = `
      margin:0;padding:0;height:100vh;overflow:hidden;
      background:linear-gradient(160deg,#080c14 60%,#0d1a2a 100%);
      display:flex;flex-direction:column;align-items:center;justify-content:center;
      font-family:system-ui,-apple-system,sans-serif;
    `;
    document.body.innerHTML = `
      <style>
        @keyframes az-up   { from{opacity:0;transform:translateY(18px)} to{opacity:1;transform:none} }
        @keyframes az-in   { from{opacity:0;transform:scale(0.88)}      to{opacity:1;transform:scale(1)} }
        @keyframes az-line { from{width:0}                              to{width:220px} }
        .az { animation:az-up var(--d,0.6s) var(--dl,0s) ease both }
        .ai { animation:az-in var(--d,0.6s) var(--dl,0s) ease both }
      </style>

      <!-- Presented by ARTIZENT (real logo — mix-blend-mode:screen drops the black bg) -->
      <div class="az" style="--dl:0.05s;display:flex;align-items:center;gap:12px;margin-bottom:52px;opacity:0">
        <img src="${logoUri}" style="height:44px;width:44px;mix-blend-mode:screen;border-radius:6px"/>
        <span style="font:300 12px/1 sans-serif;color:rgba(255,255,255,0.35);letter-spacing:3px">PRESENTS</span>
      </div>

      <!-- Aurea mark -->
      <div class="ai" style="--dl:0.25s;margin-bottom:18px;opacity:0">${aureaMark}</div>

      <!-- Aurea wordmark -->
      <div class="az" style="--dl:0.45s;font:700 60px/1 sans-serif;color:#fff;letter-spacing:-1px;margin-bottom:14px;opacity:0">
        Aurea
      </div>

      <!-- Tagline -->
      <div class="az" style="--dl:0.65s;font:300 15px/1 sans-serif;color:#c8a35e;letter-spacing:3.5px;text-transform:uppercase;margin-bottom:44px;opacity:0">
        Governed AI for Wealth Management
      </div>

      <!-- Gold divider -->
      <div style="height:1px;background:linear-gradient(to right,transparent,#c8a35e,transparent);animation:az-line 0.7s 0.95s ease both;width:0;opacity:0.7"></div>
    `;
  }, { logoUri, aureaMark });
}

// ── CLOSING SLIDE ─────────────────────────────────────────────────────────────
async function showClosingSlide(page, logoUri, aureaMark) {
  await page.goto("about:blank");
  await page.evaluate(({ logoUri, aureaMark }) => {
    document.documentElement.style.cssText = "height:100%;margin:0;";
    document.body.style.cssText = `
      margin:0;padding:0;height:100vh;overflow:hidden;
      background:linear-gradient(160deg,#080c14 60%,#0d1a2a 100%);
      display:flex;flex-direction:column;align-items:center;justify-content:center;
      font-family:system-ui,-apple-system,sans-serif;
    `;
    document.body.innerHTML = `
      <style>
        @keyframes fadeUp   { from{opacity:0;transform:translateY(18px)} to{opacity:1;transform:none} }
        @keyframes scaleIn  { from{opacity:0;transform:scale(0.82)} to{opacity:1;transform:scale(1)} }
        @keyframes expandW  { from{width:0;opacity:0} to{width:200px;opacity:0.35} }
        @keyframes spread   { from{opacity:0;letter-spacing:12px} to{opacity:1;letter-spacing:5px} }
        .fu { animation:fadeUp  var(--d,0.6s) var(--dl,0s) ease both }
        .si { animation:scaleIn var(--d,0.6s) var(--dl,0s) ease both }
      </style>

      <!-- Aurea section -->
      <div class="si" style="--dl:0.1s;margin-bottom:14px;opacity:0">${aureaMark}</div>
      <div class="fu" style="--dl:0.3s;font:700 38px/1 sans-serif;color:#fff;letter-spacing:-0.5px;margin-bottom:10px;opacity:0">
        Aurea
      </div>
      <div class="fu" style="--dl:0.5s;font:300 12px/1 sans-serif;color:#c8a35e;letter-spacing:3px;text-transform:uppercase;opacity:0">
        Governed AI for Wealth Management
      </div>

      <!-- Divider -->
      <div style="height:1px;background:linear-gradient(to right,transparent,#c8a35e,transparent);animation:expandW 0.7s 0.85s ease both;width:0;margin:38px 0 32px"></div>

      <!-- "Built by" label -->
      <div class="fu" style="--dl:1.0s;font:400 11px/1 sans-serif;letter-spacing:5px;color:rgba(255,255,255,0.38);text-transform:uppercase;margin-bottom:20px;opacity:0">
        Built by
      </div>

      <!-- ARTIZENT real logo — scale in, mix-blend-mode drops the black bg -->
      <div class="si" style="--dl:1.25s;--d:0.7s;margin-bottom:14px;opacity:0">
        <img src="${logoUri}" style="height:64px;width:64px;mix-blend-mode:screen;border-radius:8px"/>
      </div>

      <!-- ARTIZENT wordmark — letter-spread reveal -->
      <div style="font:700 22px/1 sans-serif;color:#fff;animation:spread 0.8s 1.8s ease both;opacity:0;margin-bottom:10px">
        ARTIZENT
      </div>

      <!-- Tagline from LinkedIn cover -->
      <div class="fu" style="--dl:2.2s;font:300 13px/1.5 sans-serif;color:rgba(245,200,66,0.65);letter-spacing:1px;opacity:0;text-align:center">
        Your Engineering Partner for Mission-Critical AI
      </div>
    `;
  }, { logoUri, aureaMark });
}

// ── main ──────────────────────────────────────────────────────────────────────

async function main() {
  const fsMod = await import("fs");
  const logoB64 = fsMod.readFileSync(path.join(__dirname, "assets", "arizent-logo.jpg"), "base64");
  const logoUri = `data:image/jpeg;base64,${logoB64}`;
  const aureaMark = AUREA_MARK(52);
  const aureaSm   = AUREA_MARK(44);

  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    viewport: SIZE,
    recordVideo: { dir: VIDEO_DIR, size: SIZE },
    deviceScaleFactor: 1,
  });
  const page = await ctx.newPage();
  const step = makeStep(page);
  const norm = (t, ms, fn) => step(t, (c) => caption(page, c), ms, fn);
  const diff = (t, ms, fn) => step(t, (c) => diffCaption(page, c), ms, fn);
  const sect = (t, ms) => step(t, (c) => sectionCaption(page, c), ms);

  console.log("\n═══════════════════════════════════════════════════════");
  console.log("  Aurea Demo Recording  (v9)");
  console.log("═══════════════════════════════════════════════════════\n");

  // ── INTRO SLIDE ────────────────────────────────────────────────────────────
  console.log("  INTRO SLIDE\n");
  await showIntroSlide(page, logoUri, aureaSm);
  await sleep(2500);  // hold through all intro animations

  // ── 1. LOGIN ───────────────────────────────────────────────────────────────
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await sleep(800);
  await norm("Sign in as Sophie, a senior wealth adviser", 1000, async () => {
    const btn = page.locator("button").filter({ hasText: "Studio cockpit" });
    await btn.click({ timeout: 8000 });
    await page.waitForURL((url) => !url.href.includes("/login"), { timeout: 20000 });
    await sleep(1500);
  });

  // ── 2. COCKPIT ─────────────────────────────────────────────────────────────
  await norm("The Adviser Cockpit — AI agents work in the background, 24/7", 3000);
  await norm("The activity rail shows what each agent is doing right now", 3000, async () => {
    await scroll(page, 400);
    await scroll(page, 400);
  });
  await norm("Recommendations arrive automatically — no need to go looking", 3000, async () => {
    await scroll(page, 400);
  });

  // ── 3. CLIENT LIST ─────────────────────────────────────────────────────────
  await norm("Every household Sophie looks after — in one place", 2000, async () => {
    await tryNav(page, "/studio/clients");
  });
  await norm("Click any household to see the full picture", 2500, async () => {
    await tryClick(page, ['a:has-text("Chen")', 'text=Chen Family', '[href*="clients/"]']);
    await sleep(1500);
  });
  await norm("Accounts, goals, family members, and portfolio value — all together", 3000, async () => {
    await scroll(page, 400);
    await sleep(600);
    await scroll(page, 400);
  });

  // ── 4. WORKFORCE ───────────────────────────────────────────────────────────
  await norm("The Workforce — Sophie's team of 9 AI agents", 2500, async () => {
    await tryNav(page, "/studio/workforce");
  });
  await norm("Each agent senses the data, reasons, checks compliance, then acts", 3000, async () => {
    await scroll(page, 400);
    await sleep(700);
  });

  // ── 5. RECOMMENDATION REVIEW ───────────────────────────────────────────────
  await norm("A drift recommendation has arrived — let's look inside", 2500, async () => {
    await tryNav(page, "/studio/review");
  });
  await norm("Some asset classes have drifted away from their targets", 3500, async () => {
    await scroll(page, 300);
    await sleep(700);
  });
  await norm("The agent chose the best tax lots to sell — losses first, then smallest gains", 4000, async () => {
    await scroll(page, 350);
    await sleep(900);
  });
  await norm("Every compliance rule is checked and explained clearly", 3500, async () => {
    await scroll(page, 350);
    await sleep(900);
  });
  await norm("A plain-English rationale, grounded in the firm's own research", 3500, async () => {
    await scroll(page, 350);
    await sleep(900);
  });

  // ── 6. REVISE & APPROVE ────────────────────────────────────────────────────
  await norm("Sophie adds a constraint — the agent re-runs with it", 3000, async () => {
    await page.mouse.wheel(0, -3000);
    await sleep(600);
    await tryClick(page, ['button:has-text("Revise")', 'button:has-text("Modify")']);
    await sleep(900);
    const ta = page.locator("textarea").first();
    await ta.fill("Do not sell AAPL. Keep capital gains under $8,000.").catch(() => {});
    await sleep(500);
  });
  await norm("Sophie approves — the decision is saved permanently to the ledger", 2500, async () => {
    await tryClick(page, ['button:has-text("Approve")']);
    await sleep(500);
    await tryClick(page, ['button:has-text("Confirm")']);
    await sleep(1500);
  });

  // ── 7. ANALYTICS ───────────────────────────────────────────────────────────
  await norm("Analytics — firm-wide numbers in one dashboard", 2500, async () => {
    await tryNav(page, "/studio/analytics");
  });
  await norm("AUM, wallet share, goals on track, firm margin — the full picture at once", 3000, async () => {
    await scroll(page, 400);
    await sleep(600);
  });
  await norm("Portfolio analytics — client returns, drift exposure, and tax-harvesting opportunities", 3500, async () => {
    await tryClick(page, ['button:has-text("Portfolio")', 'text=Portfolio']);
    await sleep(700);
    await scroll(page, 400);
    await sleep(500);
  });
  await norm("Practice economics — fee revenue, margin trends, and profitability by client segment", 3500, async () => {
    await tryClick(page, ['button:has-text("Practice")', 'text=Practice']);
    await sleep(700);
    await scroll(page, 400);
    await sleep(500);
  });

  // ── 8. BOOK SCAN ───────────────────────────────────────────────────────────
  await norm("Book Scan — one click to check every client in the book", 2000, async () => {
    await tryNav(page, "/studio/clients");
  });
  await norm("Book Scan running — scanning for drift, tax signals, and at-risk clients", 2500, async () => {
    await tryClick(page, ['button:has-text("Book scan")', 'button:has-text("Scan")', '[data-testid="book-scan"]']);
    await sleep(1200);
  });
  await norm("Urgent items surfaced across the full book — prioritised for the adviser", 3500, async () => {
    await scroll(page, 400);
    await sleep(900);
  });

  // ── 9. MEETING PREP ────────────────────────────────────────────────────────
  await norm("Meeting Prep — the agent reads the client file and writes the agenda", 2500, async () => {
    await tryNav(page, "/studio/meetings");
    await sleep(500);
    await tryClick(page, ['text=Prep', 'button:has-text("Prep")']);
    await sleep(1200);
  });
  await norm("Talking points, risk flags, next steps — ready before the meeting starts", 3000, async () => {
    await scroll(page, 400);
    await sleep(900);
  });

  // ── 10. CANVAS ─────────────────────────────────────────────────────────────
  await norm("Canvas — what the client sees: a clear view of their financial health", 2500, async () => {
    await tryNav(page, "/canvas");
  });
  await norm("'Am I on track?' — goals, progress, and projections in plain language", 3000, async () => {
    await scroll(page, 400);
    await sleep(900);
  });
  await norm("The client can ask questions and get answers from their own data", 2500, async () => {
    await scroll(page, 400);
    await sleep(700);
  });

  // ── 11. ROLE SWITCH ────────────────────────────────────────────────────────
  await norm("The same platform — a completely different view depending on your role", 2500, async () => {
    await tryClick(page, ['button:has-text("Switch role")', 'button:has-text("Switch")']);
    await sleep(700);
    await tryClick(page, ['text=Head of Compliance', 'text=Compliance Officer', 'text=Compliance']);
    await page.waitForLoadState("domcontentloaded").catch(() => {});
    await sleep(1200);
  });
  await norm("The compliance officer lands on Provenance — every decision and audit trail", 3500);

  // ── PART 2: DIFFERENTIATORS ───────────────────────────────────────────────
  await sect("— What makes Aurea different —", 3000);
  console.log("\n  PART 2 — Differentiators\n");

  // D1 — Governed AI Workforce
  await diff("A governed AI workforce — not just a chat assistant", 2500, async () => {
    await tryNav(page, "/studio/workforce");
  });
  await diff("9 agents run continuously — each with a defined autonomy level it cannot exceed", 3500, async () => {
    await scroll(page, 400);
    await sleep(700);
  });
  await diff("Tier 1 suggests, Tier 2 waits for approval, Tier 3 acts within hard limits", 3000);

  // D2 — Permanent Record
  await diff("Every AI decision is recorded permanently — nothing can be deleted or changed", 2500, async () => {
    await tryNav(page, "/provenance");
  });
  await diff("Open any past decision to see exactly what the agent knew and recommended", 3500, async () => {
    await scroll(page, 400);
    await sleep(700);
  });
  await diff("A cryptographic hash proves the record was never altered — full audit confidence", 3000, async () => {
    await tryClick(page, ['button:has-text("Verify chain")', 'button:has-text("Verify")']);
    await sleep(1200);
  });

  // D3 — Compliance Ontology
  await diff("Compliance rules are built in — not added on top as an afterthought", 2500, async () => {
    await tryNav(page, "/admin/regulatory");
  });
  await diff("NZ FMA · UK FCA · US SEC · EU MiFID II — switch regime with one setting", 3500, async () => {
    await scroll(page, 400);
    await sleep(900);
  });
  await diff("Each recommendation lists the exact rules it passed — by section and version", 3000);

  // D4 — Skill Builder
  await diff("Advisers can build their own AI skills — no coding required", 2500, async () => {
    await tryNav(page, "/studio/skills");
    await sleep(500);
    await tryClick(page, ['button:has-text("Firm library")', 'button:has-text("Public")']);
    await sleep(700);
  });
  await diff("Write a plain-English instruction. The AI runs it. Governance wraps it automatically.", 3500, async () => {
    await scroll(page, 300);
    await sleep(900);
  });
  await diff("Custom skills are audited, versioned, and recorded — just like every system agent", 3000);

  // D5 — Adaptive Autonomy
  await diff("The AI monitors its own quality and adjusts its own autonomy level", 2500, async () => {
    await tryNav(page, "/provenance");
    await sleep(500);
    await tryClick(page, ['[role="tab"]:has-text("Quality")', 'button:has-text("Quality")']);
    await sleep(700);
  });
  await diff("If approval rate drops, autonomy is automatically narrowed", 3000, async () => {
    await scroll(page, 400);
    await sleep(900);
  });
  await diff("It never self-widens. A human must unlock it. Every change is logged.", 3500);

  // D6 — Configurable Foundation
  await diff("Six governance controls — configurable by the firm, per agent if needed", 2500, async () => {
    await tryNav(page, "/admin/foundation");
  });
  await diff("Which AI model · PII redaction · cost limits · minimum confidence thresholds", 3000, async () => {
    await scroll(page, 500);
    await sleep(900);
  });
  await diff("Set firm-wide defaults, then override them per agent as needed", 3000, async () => {
    await scroll(page, 500);
    await sleep(900);
  });

  // D7 — Platform not Product
  await diff("A platform — not a fixed product. Any firm, any brand, any market.", 2500, async () => {
    await tryNav(page, "/admin");
    await sleep(500);
    await tryClick(page, ['button:has-text("Branding")', '[role="tab"]:has-text("Branding")']);
    await sleep(700);
  });
  await diff("The client Canvas carries the adviser's name and colours — not Aurea's", 3500, async () => {
    await scroll(page, 300);
    await sleep(900);
  });
  await diff("One codebase. Any jurisdiction. Any firm size. Any brand.", 3000);

  // ── CLOSING SLIDE ──────────────────────────────────────────────────────────
  console.log("\n  CLOSING SLIDE\n");
  await showClosingSlide(page, logoUri, aureaMark);
  await sleep(7500);  // hold through all staggered animations (last fires at ~2.2s)

  await ctx.close();
  await browser.close();

  // ── Save & convert ────────────────────────────────────────────────────────
  const fs = await import("fs");
  const vids = fs.readdirSync(VIDEO_DIR).filter((f) => f.endsWith(".webm"));
  vids.sort((a, b) =>
    fs.statSync(path.join(VIDEO_DIR, b)).mtimeMs - fs.statSync(path.join(VIDEO_DIR, a)).mtimeMs
  );
  const dest = path.join(VIDEO_DIR, "aurea-demo.webm");
  fs.copyFileSync(path.join(VIDEO_DIR, vids[0]), dest);
  console.log("\n  WEBM →", dest);

  const { execSync } = await import("child_process");
  const mp4 = dest.replace(".webm", ".mp4");
  try {
    execSync(
      `ffmpeg -y -i "${dest}" -c:v libx264 -preset fast -crf 18 -c:a aac -movflags +faststart "${mp4}"`,
      { stdio: "inherit" }
    );
    const sizeMB = (fs.statSync(mp4).size / (1024 * 1024)).toFixed(1);
    console.log(`  MP4  → ${mp4}  (${sizeMB} MB)\n`);
  } catch (e) {
    console.warn("  ffmpeg failed:", e.message.split("\n")[0]);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
