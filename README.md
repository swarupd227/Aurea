# Aurera — Governed AI for Wealth Management

> **An AI-native, agentic wealth-management platform** built by Nous Infosystems.
> Truly personal advice, at scale — with governance engineered in, not tested in after the fact.

![Stack](https://img.shields.io/badge/stack-FastAPI%20%7C%20Next.js%2014%20%7C%20PostgreSQL%20%7C%20Anthropic-blue)
![Docker](https://img.shields.io/badge/runs%20in-Docker%20Compose-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

---

## What is Aurera?

Aurera is a **governed AI workforce platform** for wealth-management firms. Unlike copilots that answer questions when asked, Aurera runs a **workforce of 9 governed AI agents continuously** — surfacing next-best-actions, monitoring drift, managing onboarding, and keeping advisers ahead of their clients, without waiting to be asked.

It ships as a fully self-contained Docker stack:

```
postgres (pgvector) · redis · backend (FastAPI) · worker (APScheduler) · frontend (Next.js)
```

---

## Quick Start

```bash
git clone https://github.com/swarupd227/Aurea.git
cd Aurea
cp .env.example .env        # optionally add ANTHROPIC_API_KEY
docker compose up --build   # brings up the entire platform
```

| Surface | URL | Default login |
|---|---|---|
| **Studio** (adviser cockpit) | http://localhost:3010 | `sophie.adviser@aurea.demo` / `aurea` |
| **Canvas** (client view) | http://localhost:3010/canvas | `client@aurea.demo` / `aurea` |
| **API docs** (Swagger) | http://localhost:8010/docs | — |

> With no `ANTHROPIC_API_KEY` the platform runs **deterministic fallbacks** for all narrative — every governed workflow still produces a fully auditable artefact. Add a key and rationale/commentary/assistant become LLM-authored.

---

## Demo Personas

All passwords: `aurea` — switchable in-app via the **role switcher** (sidebar → *Switch role*).

| Persona | Login | Lands on |
|---|---|---|
| Senior Adviser | `sophie.adviser@aurea.demo` | Studio cockpit |
| Paraplanner | `paraplanner@aurea.demo` | Meetings |
| Portfolio Manager | `portfolio@aurea.demo` | Recommendations |
| CIO / Research | `research@aurea.demo` | Reports |
| Compliance Officer | `compliance@aurea.demo` | Provenance |
| Operations | `operations@aurea.demo` | Onboarding |
| Branch Leader | `branch@aurea.demo` | Capacity & outcomes |
| Admin | `admin@aurea.demo` | Configuration |
| Client | `client@aurea.demo` | Canvas wealth view |
| Next-Gen Heir | `heir@aurea.demo` | Heir journey |

---

## What You Can Do in 5 Minutes

1. **Sign in as Sophie** → the **Cockpit** shows the live agent workforce and proactive next-best-action feed.
2. Open **The Chen Family** (couple + trust + foundation + heir) → see total-portfolio view valued against real market prices, goals, and whole-portfolio risk.
3. Click **Run drift agent** → the lighthouse produces a tax-aware draft order set (loss-harvesting, CGT-budget-aware) with a plain-language rationale grounded in the firm's house views, at **Tier 2 awaiting approval**.
4. **Approve / Modify / Dismiss** → decision is routed to the mock OMS and written to the **immutable, hash-chained decision ledger**.
5. Open **Provenance** → read the ledger, click **Verify chain** (SHA-256 tamper-evidence), see surveillance flags.
6. Open **Configuration** → rebrand the firm, change an agent's autonomy tier, **pause an agent** (kill-switch), or configure connectors.
7. Switch to **Canvas** → the same client's adviser-branded "am I okay?" experience.

---

## Nous Differentiators

These are the seven capabilities that set Aurera apart from any competing platform:

### 1. Governed AI Workforce — Not a Copilot

9 agents run continuously, each bound to a formal **autonomy tier per mandate type** (Assistive / Supervised / Bounded Autonomous). Every agent follows a 5-step governed loop:

```
Sense → Reason → Check → Decide → Act
```

Governance is **in the loop**, not bolted on after. The HITL gate at Tier 2 is enforced at the runtime layer — a proposal literally cannot execute without adviser action.

### 2. Hash-Chained Decision Ledger

Every AI decision is immutably recorded with: the data it used, the lineage of that data, the reasoning applied, the compliance checks passed/failed, and what the human did (approve / modify / dismiss / revise). Entries are **SHA-256 hash-chained** — you can cryptographically prove no entry was altered after the fact.

```
Provenance → Verify chain  ←  regulatory-grade, not just logging
```

### 3. Machine-Readable Compliance Ontology

Not tick-boxes — a formal ontology of actual legal obligations across four regimes:

| Regime | Coverage |
|---|---|
| **NZ FMA** | FMC Act, FAP Code Standards, CoFI |
| **UK FCA** | COBS 9A, Consumer Duty |
| **US SEC** | Reg BI, Form CRS |
| **EU MiFID II** | Arts 24 & 25 |

Switch a firm's jurisdiction and the entire compliance engine re-resolves. Every recommendation **cites the specific rules it passed or flagged**, with regime version and section.

### 4. Governed Skill Builder

Advisers create their own AI agents with **no code** — plain-English instruction → Claude executes → Atlas governs → ledger records. Custom agents run inside **the same compliance framework** as every system agent.

```
"Flag clients over 60 with >30% in equities and no income protection goal"
→ governed recommendation in the ledger, subject to suitability checks and HITL approval
```

### 5. Adaptive Autonomy — Self-Policing AI

An eval harness scores every agent continuously against outcomes (approval / dismiss / modify / rollback rates, surveillance flags). If a score drops, the platform **automatically narrows that agent's autonomy tier** — it never auto-widens. Widening requires governance sign-off.

### 6. Configurable Common Foundation

Six governance pillars, all wired to live behaviour and configurable per firm AND per agent:

| Pillar | Controls |
|---|---|
| **Model Gateway** | Model per task, cost cap, fallback policy |
| **PII Redaction** | Fields redacted before leaving the platform |
| **Cost Controls** | Monthly token budget, alert threshold |
| **Eval Gates** | Minimum quality score before autonomy grants |
| **Grounding** | RAG requirements, citation enforcement |
| **Security** | Audit logging, data-residency, retention policy |

### 7. Platform, Not Product

One codebase, configured for **any firm's brand, jurisdiction, and risk appetite** — zero code changes:

- Full white-label: firm name, logo, primary/accent colour, tagline
- The client Canvas carries **the adviser's name, not Aurera's**
- Jurisdiction switch changes the regulatory regime firm-wide
- Per-agent autonomy policies, per-segment fee schedules, per-mandate constraints

---

## Architecture

```
            ┌──────────────────── Experience ───────────────────────┐
            │   Aurera Studio (adviser)  ·  Aurera Canvas (client)   │  ← Next.js 14
            ├──────────────────── Agents ────────────────────────────┤
            │   9 governed agents — Drift lighthouse + 8 others       │  ← app/agents
            ├──────────────────── Runtime (Atlas) ───────────────────┤
            │   sense→reason→check→decide→act · HITL · autonomy       │  ← app/atlas
   Provenance─────────────────── Intelligence (Core) ────────────────── governance spine
   (ledger,  │  unified client graph · golden records · valuation ·   │  ← app/aurea_core
    policy,  │  planning/risk · Monte Carlo · firm research (RAG)      │
    surveil.)├──────────────────── Connectivity (Conduit) ─────────────│  ← app/provenance
            │   connector registry · mock providers · REAL market data │  ← app/conduit
            └──────────────────── Foundation ─────────────────────────┘
                 Postgres + pgvector · Redis · Anthropic-first · MCP-ready
```

| Component | Spec reference | Implementation |
|---|---|---|
| **Aurea Core** | §6 | `app/models` (client state graph) · `app/aurea_core` (valuation, planning/risk, RAG) |
| **Aurea Agents** | §7 | `app/agents` — Drift & Rebalancing (lighthouse) + 8 others |
| **Atlas Runtime** | §7 Table 6 | `app/atlas` — BaseAgent, run lifecycle, HITL gates, scheduler |
| **Provenance** | §10 | `app/provenance` — hash-chained ledger, autonomy engine, surveillance, kill-switch |
| **Conduit** | §11 | `app/conduit` — typed connector registry, mock providers, real Yahoo/Stooq market data |
| **Studio** | §8 | `frontend/app/(app)/studio` |
| **Canvas** | §9 | `frontend/app/(app)/canvas` |

**Stack:** FastAPI · SQLAlchemy 2 async · PostgreSQL + pgvector · Redis · APScheduler · Anthropic SDK · fastembed (local embeddings) · Next.js 14 · Tailwind · Recharts

---

## The Agent Workforce

| Agent | Tier | What it does |
|---|---|---|
| **Drift & Tax-Managed Rebalancing** | T2 | Detects drift vs target allocation; generates whole-portfolio, tax-aware order set with CGT budget, loss harvesting, ESG exclusions; grounded in house views |
| **Onboarding · KYC · AML** | T2 | Document intelligence, PEP/sanctions screening, suitability profiling; escalates exceptions, never auto-clears them |
| **Book Integration** | T2 | Fuzzy client matching, security mapping, conflict detection; commits on operations approval |
| **Meeting Prep** | T1 | Structured brief from the brain — portfolio, goals, watch-items, suggested agenda |
| **Meeting Companion** | T1/2 | Transcript → structured notes, action items, proposed goals; creates Tasks and Goals on approval |
| **Research & Reporting** | T1/2 | Drafts client-ready reports (performance, positioning, stress, values alignment); publishes on approval |
| **Next-Best-Action & Growth** | T1/2 | Scans for prioritised signals (concentration, harvestable losses, idle cash, off-track goals, intergenerational moments) |
| **Client Care & Retention** | T1/2 | Volatility outreach, milestone detection, heir-engagement gaps; approved outreach delivered into client's Canvas inbox |
| **Conduct Surveillance** | T2/3 | Reviews every recommendation and communication; auto-pauses agent on high-severity conduct outlier |

---

## Governance Features

- **Decision ledger** — append-only, SHA-256 hash-chained. Captures trigger, data + lineage + confidence, research cited, recommendation + rationale, tier, human action. Full reconstruction of any decision ever made.
- **Autonomy policy engine** — binds *(agent × mandate-type)* → tier + guardrails. Most-specific policy wins. Tier 1 & 2 require HITL before effect; Tier 3 is bounded with post-hoc review.
- **Conduct surveillance** — reviews every recommendation for suitability, conduct, fair-treatment risk. Auto-pauses agent on high-severity outlier via kill-switch.
- **Evaluation harness** — continuously scores agents against outcome metrics into a quality grade.
- **Adaptive autonomy** — quality regression auto-narrows tier; never auto-widens. Every change logged to ledger. Surface: *Provenance → Agent quality & autonomy*.
- **Rollback** (first-class) — any approved action can be reversed. Onboarding un-materialises the client; book integration removes committed records; companion deletes tasks/goals; report reverts to draft. Each writes a `rollback` ledger entry.
- **Kill-switch** — pause any agent immediately from Admin or Provenance. The paused agent's jobs queue but do not execute.

---

## Client Brain — Unified Graph

All data about a client — across custodians, accounts, mandates, family relationships, goals, suitability — unified in a single graph. Every value carries:
- **Lineage**: which connector sourced it, when, with what confidence
- **Golden-record resolution**: duplicate holdings merged across custodians

The **family graph** enables cross-generational views: BFS traversal over intergenerational / spouse / parent-child edges to aggregate AUM and goals across a whole family.

---

## Analytics — Five Layers, One Brain

Computed over the unified client brain — so the risk number, the tax number, and "am I okay?" all reconcile by design.

| Layer | What's delivered |
|---|---|
| **Client & Household** | Total-portfolio view · wallet-share & held-away · CLV & segmentation · life-stage |
| **Portfolio & Investment** | Performance & attribution · whole-portfolio risk · drift at book scale · tax-alpha · goals projections · ESG score |
| **Advice & NBA** | Opportunity detection · attrition/churn risk · goal-tracking probability · engagement score |
| **Practice & Business** | Capacity · cost-to-serve & profitability · growth/referral · fee & margin by segment |
| **Risk, Conduct & Data** | Conduct surveillance · AML/CFT · data-quality score (Provenance) · agent performance · audit/explainability |

---

## Wave I Features (I1–I8)

Recently shipped client-experience enhancements:

| # | Feature | Where |
|---|---|---|
| I1 | PDF wealth summary (branded, downloadable) | Canvas → *Download PDF* |
| I2 | Risk & suitability questionnaire | Canvas → *Questionnaire* |
| I3 | Fee transparency page | Canvas → *Fees* |
| I4 | Compliance rule-impact analysis | Admin → *Rule Impact* tab |
| I5 | Cross-household family aggregate | Studio → *Family aggregate* |
| I6 | Agent run history / calendar | Studio → *Agent history* |
| I7 | Firm branding editor (colour + logo text) | Admin → *Branding* tab |
| I8 | Holding concentration alerts (worker) | Auto-fires hourly; surfaces in Provenance surveillance |

---

## Real vs Mocked

| | What |
|---|---|
| **Real** | Market prices (Yahoo Finance, no key required) → live portfolio valuation; Anthropic LLM calls when key is set; local RAG embeddings over firm research |
| **Mocked / configurable** | Every integration: custody, portfolio accounting, OMS, CRM, AML/KYC, private markets, open finance, documents. Each exposes a real config schema in **Admin → Connectors** and is *ready to flip live*. Popular providers: Pershing, FNZ, Addepar, iCapital, Plaid, Aladdin, Dynamics, Salesforce, World-Check, DocuSign. |

---

## Configuration Reference

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | _(empty)_ | Enables LLM narration; deterministic fallbacks without it |
| `AUREA_MODEL_ADVICE` | `claude-opus-4-8` | Model for advice/rationale generation |
| `AUREA_MODEL_NARRATIVE` | `claude-opus-4-8` | Model for client-facing narrative |
| `AUREA_MODEL_CLASSIFY` | `claude-haiku-4-5` | Model for fast classification tasks |
| `AUREA_MARKETDATA_PROVIDER` | `yahoo` | `yahoo` / `stooq` / `alphavantage` |
| `AUREA_JWT_SECRET` | dev value | **Change in any real deployment** |
| `BACKEND_PORT` | `8010` | Backend host port |
| `FRONTEND_PORT` | `3010` | Frontend host port |

---

## Tests

```bash
# Unit tests — rebalancing engine, planning, ledger hash-chain invariants (no DB)
docker compose exec backend python -m pytest tests/test_engines.py

# Governance invariants — ledger append-only, hash-chain, mandate-aware permissions
docker compose exec backend python -m pytest tests/test_govern.py

# Full end-to-end smoke — login → drift → approve → provenance ledger
docker compose exec backend python -m tests.smoke

# Vertical smokes
docker compose exec backend python -m tests.smoke_onboarding
docker compose exec backend python -m tests.smoke_engage
docker compose exec backend python -m tests.smoke_analytics
docker compose exec backend python -m tests.smoke_canvas
```

---

## Demo Video

A Playwright script tours the platform end-to-end with a **captioned screen recording** (Part 1: core workflow · Part 2: Nous differentiators highlighted with distinct amber captions):

```bash
docker compose up -d
cd e2e && npm install && npx playwright install chromium
node record-demo.mjs          # → e2e/videos/aurera-demo.webm / .mp4
```

Covers: login → cockpit → Chen Family client brain → Drift lighthouse (run + Tier-2 approve + revise loop) → Analytics → Canvas client view → role switch → all 7 Nous differentiators.

---

## Project Layout

```
backend/
  app/
    core/          config · security (JWT/RBAC) · db (async SQLAlchemy) · logging
    models/        client state graph · governance · connector tables
    schemas/       Pydantic v2 DTOs
    api/           FastAPI routers (auth, studio, canvas, admin, provenance, conduit)
    aurea_core/    client brain: graph · golden records · valuation · planning/risk · analytics · RAG
    atlas/         agent runtime: BaseAgent · run lifecycle · HITL · autonomy · scheduler
    agents/        9 agents (Drift lighthouse deep + 8 configurable workflows)
    provenance/    hash-chained ledger · autonomy policy engine · surveillance · kill-switch · eval
    conduit/       connector framework + registry · mock providers · real market data
    llm/           Anthropic-first provider · tool-use · streaming · deterministic fallbacks
  alembic/         database migrations
  seed/            synthetic firm + advisers + households + portfolios + research
  tests/           pytest: unit + API + governance invariants

frontend/
  app/(app)/
    studio/        cockpit · clients · recommendations · analytics · provenance · meetings · skills
    canvas/        wealth view · questionnaire · fees · retirement planner · messages
    admin/         configuration · foundation · regulatory · connectors · branding

e2e/               Playwright demo recorder
docker-compose.yml postgres(+pgvector) · redis · backend · worker · frontend
```

---

## Notes

- Component names (Aurea Core / Studio / Canvas / Provenance / Conduit / Atlas) are the spec working names used throughout the codebase.
- The drift rebalancing prototype is an illustrative proof of concept on synthetic + real-priced data — **no live execution anywhere**.
- **Client data is never used to train external models.**
- Regulatory framing defaults to advice-led conduct regulation with multi-regime support (NZ FMA / UK FCA / US SEC / EU MiFID II).

---

*Built by Nous Infosystems*
