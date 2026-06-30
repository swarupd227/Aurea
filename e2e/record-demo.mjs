/**
 * Aurera — Captioned Demo Video (Revised)
 *
 * PART 1: Core workflow  — login → cockpit → client brain → drift agent → HITL → canvas
 * PART 2: Nous Differentiators 1–7 — each introduced with a gold "★ DIFFERENTIATOR" caption
 *
 *   node record-demo.mjs          (stack must be running: docker compose up)
 *
 * Output: e2e/videos/aurera-demo.webm  (~3 min 30 s)
 */
import { chromium } from "playwright";
import { fileURLToPath } from "url";
import path from "path";

const BASE = process.env.AURERA_URL || "http://localhost:3010";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const VIDEO_DIR = path.join(__dirname, "videos");
const SIZE = { width: 1366, height: 820 };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── caption helpers ───────────────────────────────────────────────────────────

/**
 * Single shared renderer — creates the bar from scratch if navigation wiped it,
 * then applies the given theme. This avoids the `if (!bar) return` trap.
 */
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
    bar.style.background  = bg;
    bar.style.color       = textColor;
    bar.style.borderTop   = border;
    document.getElementById("demo-cap-brand").textContent = brand;
    document.getElementById("demo-cap-brand").style.color = brandColor;
    document.getElementById("demo-cap-text").textContent  = text;
  }, { brand, brandColor, text, bg, textColor, border });
}

/** Normal caption — dark navy bar, gold AURERA prefix */
const caption = (page, text) => applyCaption(page, {
  brand: "AURERA", brandColor: "#c8a35e", text,
  bg: "linear-gradient(to top, rgba(13,26,37,0.97), rgba(13,26,37,0.88))",
  textColor: "#f0f0f0", border: "2px solid #c8a35e",
});

/** Differentiator caption — warm amber bar, numbered ★ label */
const diffCaption = (page, n, text) => applyCaption(page, {
  brand: `★  NOUS DIFFERENTIATOR ${n}/7`, brandColor: "#f5c842", text: "  " + text,
  bg: "linear-gradient(to top, rgba(31,18,2,0.98), rgba(31,18,2,0.90))",
  textColor: "#fff8ed", border: "2px solid #f5c842",
});

/** Section-break caption — slate-blue divider between Part 1 and Part 2 */
const sectionCaption = (page, text) => applyCaption(page, {
  brand: "——", brandColor: "#c8a35e", text: "  " + text,
  bg: "rgba(22,58,82,0.98)", textColor: "#e8d9b8", border: "2px solid #c8a35e",
});

// ── step runner ───────────────────────────────────────────────────────────────

function makeStep(page) {
  return async function step(cap, capFn, ms, fn) {
    await capFn(cap);
    if (fn) {
      try { await fn(); } catch (e) { console.warn("  SKIP:", e.message.split("\n")[0]); }
    }
    await capFn(cap);            // re-apply after any navigation wipes the DOM
    console.log(`  • [${String(ms).padStart(4)}ms] ${cap.slice(0, 68)}`);
    await sleep(ms);
  };
}

/**
 * Try each selector in turn; click the first one that resolves within 6 s.
 * Accepts a single selector string or an array of fallback selectors.
 */
async function tryClick(page, selectors, opts = {}) {
  const list = Array.isArray(selectors) ? selectors : [selectors];
  for (const sel of list) {
    try {
      await page.locator(sel).first().click({ timeout: 6000, ...opts });
      return;
    } catch { /* try next */ }
  }
}

const tryNav = (page, url) =>
  page.goto(`${BASE}${url}`, { waitUntil: "networkidle", timeout: 15000 }).catch(() => {});

// ── main ──────────────────────────────────────────────────────────────────────

async function main() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    viewport: SIZE,
    recordVideo: { dir: VIDEO_DIR, size: SIZE },
    deviceScaleFactor: 1,
  });
  const page = await ctx.newPage();
  const step = makeStep(page);
  const norm = (t, ms, fn) => step(t, (c) => caption(page, c), ms, fn);
  const diff = (n, t, ms, fn) => step(t, (c) => diffCaption(page, n, c), ms, fn);
  const sect = (t, ms) => step(t, (c) => sectionCaption(page, c), ms);

  console.log("\n═══════════════════════════════════════════════════════");
  console.log("  Aurera Demo Recording");
  console.log("═══════════════════════════════════════════════════════\n");

  // ── TITLE ─────────────────────────────────────────────────────────────────
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await norm("Aurera — Wealth Intelligence Platform  |  Built by Nous Infosystems", 4000);

  // ══════════════════════════════════════════════════════════════════════════
  console.log("\n  PART 1 — Core Workflow\n");
  // ══════════════════════════════════════════════════════════════════════════

  // 1. Login
  await norm("Step 1 of 8 — Sign in as Sophie, Senior Wealth Adviser", 2500, async () => {
    const inputs = page.locator("input");
    await inputs.nth(0).fill("sophie.adviser@aurea.demo");
    await inputs.nth(1).fill("aurea").catch(() => {});
    await page.getByRole("button", { name: "Sign in" }).click({ timeout: 8000 });
    await sleep(2800);
    await tryNav(page, "/studio");
  });

  // 2. Cockpit
  await norm("Step 2 of 8 — The Adviser Cockpit: AI agents are already working, proactively", 3500);
  await norm("Next-best-actions surface across the book without Sophie asking for them", 4000, async () => {
    await page.mouse.wheel(0, 300);
    await sleep(1200);
    await page.mouse.wheel(0, 300);
  });

  // 3. Client Brain
  await norm("Step 3 of 8 — The Unified Client Brain: open The Chen Family", 2500, async () => {
    await tryNav(page, "/studio/clients");
  });
  await norm("All relationships, accounts, goals and mandates unified — not siloed across systems", 4000, async () => {
    await tryClick(page, ['text=The Chen Family', 'a:has-text("Chen")', 'tr:has-text("Chen") td a']);
    await sleep(2500);
  });
  await norm("A multi-entity household: couple + family trust + foundation + next-gen heir", 3500, async () => {
    await page.mouse.wheel(0, 400);
    await sleep(1500);
  });

  // 4. Drift Agent — sense→reason→check→decide→act
  await norm("Step 4 of 8 — Run the Drift & Tax-Managed Rebalancing agent", 2500, async () => {
    await page.mouse.wheel(0, -2000);
    await sleep(800);
    await tryClick(page, ['text=Run drift agent', 'button:has-text("Rebalance")', 'button:has-text("Run drift")']);
    await sleep(1500);
  });
  await norm("Sense → Reason → Check → Decide → Act: the governed five-step AI loop in motion", 5000, async () => {
    await sleep(4000);
  });
  await norm("Whole-portfolio rebalance: CGT budget honoured, house views cited, ESG exclusions respected", 5500, async () => {
    await page.mouse.wheel(0, 600);
    await sleep(2000);
  });

  // 5. HITL + Revise
  await norm("Step 5 of 8 — Review, revise, or approve at the Tier-2 human checkpoint", 3000);
  await norm("The adviser constrains the rebalance — agent re-runs and returns a better proposal", 4500, async () => {
    await tryClick(page, 'button:has-text("Revise")');
    await sleep(1500);
    const ta = page.locator("textarea").first();
    await ta.fill("Do not sell any tech holdings. Keep capital gains under $8,000.").catch(() => {});
    await sleep(1200);
    await tryClick(page, ['button:has-text("Run revision")', 'button:has-text("Submit revision")', 'button:has-text("Resubmit")']);
    await sleep(3000);
  });
  await norm("Approve — proposal accepted, routed to the mock OMS, written to the decision ledger", 4000, async () => {
    await tryClick(page, 'button:has-text("Approve")');
    await sleep(900);
    await tryClick(page, 'button:has-text("Confirm")');
    await sleep(2500);
  });

  // 6. Analytics
  await norm("Step 6 of 8 — Five-layer analytics: all figures trace to the one governed brain", 3000, async () => {
    await tryNav(page, "/studio/analytics");
    await sleep(1800);
  });
  await norm("AUM, wallet-share, attribution, attrition, cost-to-serve — every number reconciles by design", 4500, async () => {
    await page.mouse.wheel(0, 600); await sleep(1500);
    await page.mouse.wheel(0, 600); await sleep(1200);
  });

  // 7. Canvas — client view
  await norm("Step 7 of 8 — Switch to the Canvas: the client's plain-language wealth view", 3000, async () => {
    await tryNav(page, "/canvas");
    await sleep(2000);
  });
  await norm("'Am I on track?' — goals-based, adviser-branded, on any device", 3500, async () => {
    await page.mouse.wheel(0, 500);
    await sleep(1800);
  });
  await norm("Fee transparency, suitability questionnaire, PDF snapshot — all client-facing in Wave I", 3500, async () => {
    await page.mouse.wheel(0, 500);
    await sleep(1500);
  });

  // 8. Role switch
  await norm("Step 8 of 8 — Switch roles: one platform serves every persona", 3000, async () => {
    await tryClick(page, ['button:has-text("Switch role")', 'button:has-text("Switch")', '[aria-label*="role"]']);
    await sleep(1400);
    await tryClick(page, ['text=Head of Compliance', 'text=Compliance Officer', 'text=Compliance']);
    await page.waitForLoadState("networkidle").catch(() => {});
    await sleep(2200);
  });
  await norm("Compliance lands on Provenance — governance, surveillance flags and agent quality", 3500);

  // ══════════════════════════════════════════════════════════════════════════
  await sect("— Nous Differentiators —  What sets Aurera apart from every other platform", 4500);
  console.log("\n  PART 2 — Nous Differentiators\n");
  // ══════════════════════════════════════════════════════════════════════════

  // D1 — Governed AI Workforce (not a copilot)
  await diff(1, "Governed AI Workforce — not a copilot, not a dashboard. A workforce.", 3000, async () => {
    await tryNav(page, "/studio/workforce");
    await sleep(2000);
  });
  await diff(1, "9 agents running continuously, each with a formal autonomy tier per mandate type", 4500, async () => {
    await page.mouse.wheel(0, 400);
    await sleep(1800);
  });
  await diff(1, "Sense → Reason → Check → Decide → Act — governance is in the loop, not bolted on after", 4000);

  // D2 — Hash-Chained Provenance
  await diff(2, "Hash-Chained Decision Ledger — every AI decision is immutably recorded", 3000, async () => {
    await tryNav(page, "/provenance");
    await sleep(2000);
  });
  await diff(2, "Full reconstruction of any recommendation: what it knew, what it concluded, who approved it", 4500, async () => {
    await page.mouse.wheel(0, 400);
    await sleep(1500);
  });
  await diff(2, "Verify the chain — cryptographic proof the record was never altered, ever", 4000, async () => {
    await tryClick(page, ['button:has-text("Verify chain")', 'button:has-text("Verify")']);
    await sleep(2500);
  });

  // D3 — Machine-Readable Compliance Ontology
  await diff(3, "Machine-Readable Compliance Ontology — not tick-boxes, actual legal obligations", 3000, async () => {
    await tryNav(page, "/admin/regulatory");
    await sleep(2000);
  });
  await diff(3, "NZ-FMA, UK-FCA, US-SEC, EU-MiFID in one platform — switch jurisdiction, engine re-resolves", 4500, async () => {
    await page.mouse.wheel(0, 400);
    await sleep(2000);
  });
  await diff(3, "Every recommendation cites the rules it passed or flagged, with regime version and section", 4000);

  // D4 — Governed Skill Builder
  await diff(4, "Governed Skill Builder — advisers build their own AI agents, no code required", 3000, async () => {
    await tryNav(page, "/studio/skills");
    await sleep(2000);
  });
  await diff(4, "Plain-English instruction → Claude executes → Atlas governs → Ledger records", 4000, async () => {
    await page.mouse.wheel(0, 400);
    await sleep(1800);
  });
  await diff(4, "Custom agents run inside the same compliance framework as every system agent", 4000, async () => {
    await tryClick(page, ['button:has-text("Test")', 'button:has-text("Run skill")', 'button:has-text("Run")']);
    await sleep(2500);
  });

  // D5 — Adaptive Autonomy (self-policing AI)
  await diff(5, "Adaptive Autonomy — the AI workforce monitors its own quality and self-polices", 3000, async () => {
    await tryNav(page, "/provenance");
    await sleep(1800);
    await tryClick(page, ['button:has-text("Agent quality")', '[role="tab"]:has-text("Quality")', '[role="tab"]:has-text("quality")']);
    await sleep(1500);
  });
  await diff(5, "Eval harness scores every agent continuously against golden benchmarks", 4000, async () => {
    await page.mouse.wheel(0, 400);
    await sleep(1800);
  });
  await diff(5, "If an agent's score drops, its autonomy tier narrows automatically — it never auto-widens", 4500);

  // D6 — Configurable Foundation
  await diff(6, "Configurable Foundation — 6 governance pillars wired to live behaviour", 3000, async () => {
    await tryNav(page, "/admin/foundation");
    await sleep(2000);
  });
  await diff(6, "Model gateway · PII redaction · cost caps · eval gates · grounding · security", 4000, async () => {
    await page.mouse.wheel(0, 500);
    await sleep(1800);
  });
  await diff(6, "Configurable per firm AND per agent — a different confidence floor for every role in the workforce", 4500, async () => {
    await page.mouse.wheel(0, 500);
    await sleep(1800);
  });

  // D7 — Multi-Tenant White-Label Platform
  await diff(7, "Platform, not product — white-label, multi-regime, multi-tenant, zero code to deploy", 3000, async () => {
    await tryNav(page, "/admin");
    await sleep(2000);
    await tryClick(page, ['button:has-text("Branding")', '[role="tab"]:has-text("Branding")']);
    await sleep(1500);
  });
  await diff(7, "Firm branding, colours, logo and tagline — the client Canvas carries the adviser's name, not Aurera's", 4500, async () => {
    await page.mouse.wheel(0, 300);
    await sleep(1800);
  });
  await diff(7, "One codebase, configured for any firm's jurisdiction, risk appetite and brand identity", 4000);

  // ── CLOSE ─────────────────────────────────────────────────────────────────
  await sect("Aurera — Governed AI for Wealth Management", 3000);
  await sect("Built by Nous Infosystems", 5000);

  await ctx.close();
  await browser.close();

  const fs = await import("fs");
  const vids = fs.readdirSync(VIDEO_DIR).filter((f) => f.endsWith(".webm"));
  vids.sort((a, b) =>
    fs.statSync(path.join(VIDEO_DIR, b)).mtimeMs - fs.statSync(path.join(VIDEO_DIR, a)).mtimeMs
  );
  const dest = path.join(VIDEO_DIR, "aurera-demo.webm");
  fs.copyFileSync(path.join(VIDEO_DIR, vids[0]), dest);
  console.log("\n  VIDEO →", dest, "\n");
}

main().catch((e) => { console.error(e); process.exit(1); });
