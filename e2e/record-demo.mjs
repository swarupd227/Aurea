/**
 * Aurera — Captioned Demo Video v3
 *
 * Root-cause fixes vs v2:
 *  • No live agent runs — use pre-seeded recommendations at /studio/review
 *    (auto-open on page load, full drift/compliance/rationale UI instantly visible)
 *  • Skills: click "Firm library" filter first (seeded skills are public, not "mine")
 *  • tryClick timeout reduced from 6 s → 2.5 s (no more 18 s frozen screens)
 *  • Cockpit and Workforce shown first to establish the agentic feel
 *
 * PART 1 — Core workflow:  login → cockpit → workforce → client brain →
 *           recommendation deep-view → revise + approve → canvas → role switch
 * PART 2 — Nous Differentiators 1–7 (amber bar)
 *
 *   node record-demo.mjs          (stack must be running: docker compose up)
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
  brand: "AURERA", brandColor: "#c8a35e", text,
  bg: "linear-gradient(to top, rgba(13,26,37,0.97), rgba(13,26,37,0.88))",
  textColor: "#f0f0f0", border: "2px solid #c8a35e",
});
const diffCaption = (page, n, text) => applyCaption(page, {
  brand: `★  NOUS DIFFERENTIATOR ${n}/7`, brandColor: "#f5c842", text: "  " + text,
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
    await capFn(cap);
    if (fn) {
      try { await fn(); } catch (e) { console.warn("  SKIP:", e.message.split("\n")[0]); }
    }
    await capFn(cap);   // re-inject after navigation wipes the DOM
    console.log(`  • [${String(ms).padStart(4)}ms] ${cap.slice(0, 72)}`);
    await sleep(ms);
  };
}

/** Try each selector in order; first hit wins; 2.5 s timeout each */
async function tryClick(page, selectors, opts = {}) {
  const list = Array.isArray(selectors) ? selectors : [selectors];
  for (const sel of list) {
    try {
      await page.locator(sel).first().click({ timeout: 2500, ...opts });
      return true;
    } catch { /* next */ }
  }
  return false;
}

const tryNav = (page, url) =>
  page.goto(`${BASE}${url}`, { waitUntil: "networkidle", timeout: 15000 }).catch(() => {});

const scroll = (page, dy) => page.mouse.wheel(0, dy).then(() => sleep(900));

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
  console.log("  Aurera Demo Recording  (v3 — fixed)");
  console.log("═══════════════════════════════════════════════════════\n");
  console.log("  PART 1 — Core Workflow\n");

  // ── TITLE ─────────────────────────────────────────────────────────────────
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await norm("Aurera — Wealth Intelligence Platform  |  Built by Nous Infosystems", 3500);

  // 1. Login
  await norm("Sign in as Sophie — Senior Wealth Adviser", 2500, async () => {
    await page.locator("input").nth(0).fill("sophie.adviser@aurea.demo");
    await page.locator("input").nth(1).fill("aurea").catch(() => {});
    await page.getByRole("button", { name: "Sign in" }).click({ timeout: 8000 });
    await sleep(3000);
    await tryNav(page, "/studio");
  });

  // 2. Cockpit — live activity rail (agentic feel)
  await norm("The Adviser Cockpit — AI agents are working proactively, around the clock", 3500);
  await norm("Live activity rail: each agent announces what it is monitoring, every 40 seconds", 4000, async () => {
    // Scroll down to show the activity rail
    await scroll(page, 350);
    await scroll(page, 350);
  });
  await norm("Recommendations arrive without Sophie asking — drift proposals, outreach cues, risk flags", 4000, async () => {
    await scroll(page, 350);
    await sleep(1000);
  });

  // 3. Workforce — see the governed agents in one view
  await norm("The Workforce — 9 AI agents, each with a defined autonomy tier and a governed loop", 3000, async () => {
    await tryNav(page, "/studio/workforce");
    await sleep(2000);
  });
  await norm("Sense → Reason → Check → Decide → Act: the five-step loop every agent follows", 4000, async () => {
    await scroll(page, 400);
    await sleep(1500);
  });

  // 4. Client Brain
  await norm("Open The Chen Family — couple, trust, foundation, and a next-gen heir in one view", 2500, async () => {
    await tryNav(page, "/studio/clients");
    await sleep(1500);
    await tryClick(page, ['text=The Chen Family', 'a:has-text("Chen")']);
    await sleep(2500);
  });
  await norm("Total portfolio valued against live market prices — goals, accounts, and risk in one graph", 4000, async () => {
    await scroll(page, 400);
    await sleep(1500);
    await scroll(page, 400);
  });

  // 5. Recommendation deep-view (the richest agentic surface)
  await norm("A proposal has arrived — the full anatomy of an AI recommendation", 3000, async () => {
    await tryNav(page, "/studio/review");
    await sleep(2200);
  });
  await norm("Why now: max drift vs band, asset classes breaching tolerance — drift detected automatically", 4500, async () => {
    // Proposed cards auto-open — scroll to the drift analysis panel
    await scroll(page, 300);
    await sleep(1500);
  });
  await norm("Tax managed: CGT budget enforced, loss-harvesting lots selected first — not just rebalancing", 5000, async () => {
    await scroll(page, 350);
    await sleep(1800);
  });
  await norm("Regulatory compliance: rules cited by code, section, regime and version — not tick-boxes", 4500, async () => {
    await scroll(page, 350);
    await sleep(1800);
  });
  await norm("LLM rationale grounded in the firm's house views — every claim traces to a cited source", 4500, async () => {
    await scroll(page, 350);
    await sleep(1800);
  });

  // 6. Revise + Approve
  await norm("Adviser constrains the rebalance — agent re-runs with the instruction baked in", 4000, async () => {
    await page.mouse.wheel(0, -3000);   // scroll back to top of card for the action buttons
    await sleep(1200);
    await tryClick(page, ['button:has-text("Revise")']);
    await sleep(1500);
    const ta = page.locator("textarea").first();
    await ta.fill("Do not sell AAPL. Keep capital gains under $8,000.").catch(() => {});
    await sleep(1000);
  });
  await norm("Tier-2 gate — adviser approves. Routed to mock OMS, written to the decision ledger", 3500, async () => {
    await tryClick(page, ['button:has-text("Approve")']);
    await sleep(900);
    await tryClick(page, ['button:has-text("Confirm")']);
    await sleep(2500);
  });

  // 7. Analytics
  await norm("Five-layer analytics — all figures computed over the one governed client brain", 3000, async () => {
    await tryNav(page, "/studio/analytics");
    await sleep(2000);
  });
  await norm("AUM · wallet-share · attribution · attrition · cost-to-serve — every number reconciles by design", 4000, async () => {
    await scroll(page, 500);
    await sleep(1500);
    await scroll(page, 500);
  });

  // 8. Canvas — client view
  await norm("Canvas — the client's plain-language 'am I okay?' view, in Sophie's name", 3000, async () => {
    await tryNav(page, "/canvas");
    await sleep(2200);
  });
  await norm("Goals-based wealth view: confidence scores, projected outcomes, values alignment", 3500, async () => {
    await scroll(page, 400);
    await sleep(1800);
  });
  await norm("Fee transparency · suitability questionnaire · PDF snapshot — all available to the client", 3500, async () => {
    await scroll(page, 400);
    await sleep(1500);
  });

  // 9. Role switch → Compliance
  await norm("One platform — switch roles to see how every persona lands", 3000, async () => {
    await tryClick(page, ['button:has-text("Switch role")', 'button:has-text("Switch")']);
    await sleep(1400);
    await tryClick(page, ['text=Head of Compliance', 'text=Compliance Officer', 'text=Compliance']);
    await page.waitForLoadState("networkidle").catch(() => {});
    await sleep(2000);
  });
  await norm("Compliance lands on Provenance — every decision, flag, and agent quality score in one view", 4000);

  // ── PART 2: DIFFERENTIATORS ───────────────────────────────────────────────
  await sect("— Nous Differentiators —  What no other platform delivers", 4500);
  console.log("\n  PART 2 — Nous Differentiators\n");

  // D1 — Governed AI Workforce
  await diff(1, "Governed AI Workforce — not a copilot. A continuously running, governed workforce.", 3000, async () => {
    await tryNav(page, "/studio/workforce");
    await sleep(2000);
  });
  await diff(1, "9 agents, each bound to a formal autonomy tier. None can act without passing governance.", 4500, async () => {
    await scroll(page, 400);
    await sleep(1500);
  });
  await diff(1, "Tier 1 assists, Tier 2 waits for HITL approval, Tier 3 acts within hard guardrails", 4000);

  // D2 — Hash-Chained Provenance
  await diff(2, "Hash-Chained Decision Ledger — every AI decision immutably recorded", 3000, async () => {
    await tryNav(page, "/provenance");
    await sleep(2000);
  });
  await diff(2, "Full reconstruction: what the agent knew, reasoned, checked — and what the human decided", 4500, async () => {
    await scroll(page, 400);
    await sleep(1500);
  });
  await diff(2, "Verify the chain — SHA-256 cryptographic proof the record was never altered, ever", 4000, async () => {
    await tryClick(page, ['button:has-text("Verify chain")', 'button:has-text("Verify")']);
    await sleep(2500);
  });

  // D3 — Compliance Ontology
  await diff(3, "Machine-Readable Compliance Ontology — actual legal obligations, not check-boxes", 3000, async () => {
    await tryNav(page, "/admin/regulatory");
    await sleep(2000);
  });
  await diff(3, "NZ FMA · UK FCA · US SEC · EU MiFID II — one platform, four regimes, switch by jurisdiction", 4500, async () => {
    await scroll(page, 400);
    await sleep(2000);
  });
  await diff(3, "Every recommendation cites the rules it passed or flagged — regime, section, version", 4000);

  // D4 — Governed Skill Builder (firm library visible!)
  await diff(4, "Governed Skill Builder — advisers write AI agents in plain English, zero code", 3000, async () => {
    await tryNav(page, "/studio/skills");
    await sleep(2000);
    // Switch to Firm library filter — seeded skills live here
    await tryClick(page, ['button:has-text("Firm library")', 'button:has-text("Public")']);
    await sleep(1500);
  });
  await diff(4, "Cash drag finder · Concentration watch · Next-gen outreach — all running under full governance", 4500, async () => {
    await scroll(page, 300);
    await sleep(1800);
  });
  await diff(4, "Plain-English instruction → Claude executes → Atlas governs → Ledger records — same rules as every system agent", 4500);

  // D5 — Adaptive Autonomy
  await diff(5, "Adaptive Autonomy — the AI workforce monitors its own quality and self-polices", 3000, async () => {
    await tryNav(page, "/provenance");
    await sleep(1800);
    await tryClick(page, [
      '[role="tab"]:has-text("Quality")',
      'button:has-text("Agent quality")',
      'button:has-text("Quality")',
    ]);
    await sleep(1500);
  });
  await diff(5, "Eval harness scores every agent against outcome rates — approvals, dismissals, rollbacks, flags", 4000, async () => {
    await scroll(page, 400);
    await sleep(1800);
  });
  await diff(5, "Quality drops → autonomy tier narrows automatically. It never auto-widens. Every change logged.", 4500);

  // D6 — Configurable Foundation
  await diff(6, "Configurable Foundation — 6 governance pillars wired to live platform behaviour", 3000, async () => {
    await tryNav(page, "/admin/foundation");
    await sleep(2000);
  });
  await diff(6, "Model gateway · PII redaction · cost caps · eval gates · grounding · security", 4000, async () => {
    await scroll(page, 500);
    await sleep(1800);
  });
  await diff(6, "Configurable per firm AND per agent — a different confidence floor for every agent in the workforce", 4500, async () => {
    await scroll(page, 500);
    await sleep(1800);
  });

  // D7 — Platform not Product
  await diff(7, "Platform, not product — white-label, multi-regime, multi-tenant, zero code to deploy", 3000, async () => {
    await tryNav(page, "/admin");
    await sleep(2000);
    await tryClick(page, ['button:has-text("Branding")', '[role="tab"]:has-text("Branding")']);
    await sleep(1500);
  });
  await diff(7, "Firm colours, logo, tagline — the client Canvas carries the adviser's name, not Aurera's", 4500, async () => {
    await scroll(page, 300);
    await sleep(1800);
  });
  await diff(7, "One codebase, configured for any firm's jurisdiction, risk appetite, and brand identity", 4000);

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
  console.log("\n  WEBM →", dest);

  // Convert to mp4
  const { execSync } = await import("child_process");
  const mp4 = dest.replace(".webm", ".mp4");
  try {
    execSync(
      `ffmpeg -y -i "${dest}" -c:v libx264 -preset fast -crf 18 -c:a aac -movflags +faststart "${mp4}"`,
      { stdio: "inherit" }
    );
    const size = (fs.statSync(mp4).size / (1024 * 1024)).toFixed(1);
    console.log(`  MP4  → ${mp4}  (${size} MB)\n`);
  } catch (e) {
    console.warn("  ffmpeg conversion failed:", e.message.split("\n")[0]);
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
