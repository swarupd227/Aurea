# CLAUDE.md

**Astra for Wealth.** "Aurea" survives only as the internal codename — the Python package (`aurea_core`),
the Azure resources (`aurea-backend`, `aurea-worker`, `aurea-frontend`) and the database. Nothing a user
sees should say Aurea or Aurera.

## The product model (read before touching any UI or agent code)

**You talk to a team of agents, and they talk back.** The conversation workspace is the primary surface;
pages are artifact views the agents open. Full guideline, including what is built today and what is not:
**`docs/02_UX/agentic-ux-philosophy.md`**. The non-negotiables:

1. Natural language is the primary way to do anything; buttons inside pages are the same actions as tools.
2. Every fact Astra states comes from a tool called after the user's latest message. Tool calls stay on
   the message as Sources; results become cards. The orchestrator prompt carries no client or firm facts.
3. State-changing tools pause on a Confirm / Not now card, held **server-side** and executed exactly once.
4. Three controls, never merged: confirmation ("did you mean this"), decision rights ("may this role"),
   autonomy tier ("may an agent do this on its own").
5. Sentences live only in agent messages. Cards and pages are figures, tables and chips — no prose.
6. Dark by default. `#FFDD00` yellow means only: agent working, primary action, focus.
7. Routes and test ids are stable. New capability → a tool → maybe a card → maybe a page.
8. Clients see their own household only, hold no state-changing tools, and advice goes to their adviser.

## Rules this codebase already enforces — keep them true

- **Trade decisions.** Proposals that move money on a book (`BOOK_MOVING_AGENTS`) are approved, modified or
  revised only by an adviser or portfolio team; compliance may dismiss or roll back but not approve;
  admins hold neither. Single source: `backend/app/core/decision_rights.py`. Do not reintroduce
  `require_roles(*STAFF_ROLES)` on a decision route.
- **Exactly-once decisions.** `runtime.decide` and `runtime.rollback` lock the recommendation and re-read
  its status. Callers must not re-implement the check instead.
- **One open proposal per subject.** A scheduled agent that finds the same thing again refreshes its open
  proposal instead of raising another; `run_agent` does this through `atlas/proposals.py`, so an agent
  must not dedupe for itself. Identity is agent + subject + title with its numbers stripped, so put
  changing numbers in the title and anything that makes a finding distinct in the words. An agent whose
  every run is its own event sets `refreshes_open_proposals = False`.
- **Reconciliation reports, it never repairs.** A custodian statement is supplied by a person from a real
  document, never invented (the custody connectors are mocks). It is compared with the book as it stood on
  the statement's date; what cannot be rolled back is reported "not comparable", not guessed. A break
  carries a likely cause and its evidence, and is cleared by a later statement or by a person - nothing in
  `aurea_core/reconciliation.py` writes to holdings, cash or lots. The reconciliation agent proposes which
  breaks to escalate or accept; it records nothing until operations or the portfolio team approves
  (`decision_rights.RECON_AGENTS`), whatever its tier.
- **The rebalancer does not propose a wash sale.** `aurea_core/wash_sale.py` reads the household's trades in
  the 30-day window, every account included, and `optimise` works around them. It binds a household taxed
  in the US (its own `values.jurisdiction`, else the firm's) and nobody else. `act()` re-checks at approval
  and places nothing if the window has changed. "Substantially identical" means the same instrument.
- **Firm scope.** `household_brain` and every `for_household` take a required keyword-only `firm_id`. Never
  make it optional. Agents pass `ctx.firm.id`; routes pass `firm.id`.
- **Honesty.** Nothing fills on a synthetic price, and a valuation that uses one carries
  `price_source: "synthetic"`; the paper venue never claims a market was reached; a filled order is
  offset, never reversed.

## Working in this repo

- **Backend** runs locally with uvicorn against the Azure database. Never use Docker for code changes.
  Azure Postgres is IP-allowlisted and usually unreachable from a dev machine, so verification here relies
  on the SQLite-backed suites below; verify live behaviour through the deployed API.
- **Checks**, from `backend/` with `.venv\Scripts\python.exe -m tests.<name>`:
  `verify_decision_rights`, `verify_tenant_scope`, `verify_orders`, `verify_funding`,
  `verify_model_instruments`, `verify_capacity`, `verify_schedules`, `verify_proposals`, `verify_price_freshness`, `verify_reconciliation`, `verify_reconciliation_agent`, `verify_wash_sale_guard`. From the repo root:
  `node e2e/check-testids.mjs`.
- **Seed changes need a backfill.** `seed/run.py` skips a firm that already exists, so changing what it
  writes does nothing to a deployed database. Ship a `seed/<name>_backfill.py` alongside, and say so.
- **`create_all` adds tables, never columns.** A new column on an existing table needs an explicit
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `app/db_bootstrap.py`.
- **Agent context** is `ctx.session` and `ctx.firm` — there is no `ctx.db` or `ctx.firm_id`. A wrong
  attribute raises only when the agent runs, and scheduled runs log the failure and move on.
- **Frontend typecheck** has four known pre-existing errors (admin data-quality tuples, agent-history,
  capacity). Add no new ones.

## Azure deployment

`az` is not available locally. Give the user copy-pasteable commands for a **brand-new** Cloud Shell,
every time, starting with the subscription:

```bash
az account set --subscription b017b8b9-0911-43e7-96e9-4e38dd4c06f7
az account show --output table
[ -d ~/aurea ] || git clone https://github.com/swarupd227/Aurea.git ~/aurea
cd ~/aurea && git pull && bash scripts/deploy.sh
```

`scripts/deploy.sh backend` deploys backend and worker (they share an image); `frontend` deploys the
frontend; no argument deploys both. Commands run inside a container start from
`az containerapp exec --name aurea-backend --resource-group aurea-rg --command "/bin/bash"`.
