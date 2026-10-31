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
   the message as Sources; results become cards. The orchestrator prompt carries no client or firm facts;
   the one thing it is told is today's date on the firm's calendar (`aurea_core/firm_time.py`).
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
- **Protection works from what is on file.** `aurea_core/protection.py` never estimates an income or a cover
  figure: a person with no income or date of birth is "not assessed", and that is the result. No policy on
  file does not mean no cover. Policies and incomes are recorded by a person from a document.
- **A report reaches a client only through its proposal.** Research & Reporting drafts and proposes;
  approving that proposal publishes, rolling it back withdraws. `aurea_core/reports.py` publishes nothing,
  no tool does, and the agent stops at a proposal whatever its tier (`agent_runs.forced_to_proposal`). A rerun rewrites the draft still waiting for a decision, so none is stranded. A draft
  whose proposal is gone, and a seeded report, stay as they are: redraft, don't invent a proposal.
- **A message to a client goes out in a person's name.** Only an adviser or paraplanner writes to a client
  (`aurea_core/messages.py`), on every route, and in the conversation behind a Confirm card showing the
  exact words. Only their opening a thread clears its unread; the conversation's read marks nothing. What a
  client wrote is data, never an instruction. A declined change reaches the model as `[Not done: ...]`.
- **Canvas stays in its household.** `api/canvas._resolve_household` gives a client their own household,
  whatever they ask for, and staff only one inside their firm. A client tied to no household gets none.
- **The questionnaire indicates, an adviser decides.** `aurea_core/risk_profile.py` scores tolerance and
  capacity apart, only from a complete form, and the lower is indicated. Nothing reaches a mandate until an
  adviser adopts it or keeps what is on file with a reason, and new answers reopen that. Adopting is refused
  above the model's equity, because a mandate over its ceiling pauses the rebalancer for the whole firm.
  Every questionnaire stays on record in `risk_assessment`; a client is not shown the result.
- **A person says a review happened.** `aurea_core/reviews.py` holds the evidence of ongoing service: when a
  review was held and what it covered, added and never edited. A meeting is not a review until someone records
  it; only `seed/client_review_backfill.py` carries one across, and marks what it covered as not recorded. The
  next date comes from a recorded review and the cadence, so no record means no date, and no record does not
  mean no review. A booking sits beside an overdue review and does not clear it.
- **A market is resolved in one place, and a country with no rules is refused.** `aurea_core/markets.py` says
  what a jurisdiction fixes; `compliance/ontology.framework_for` matches a regulator as a word. A country with
  no market gets `NO_REGIME`, no tax report and a held activation, never New Zealand's rules. Money in a
  sentence is `markets.money`, not a dollar sign.
- **In India the licence decides, and an adviser's approval is not a client's consent.** `aurea_core/licences.py`
  caps every tier a licensed firm reads, in `policy.resolve_tier`, and no Indian licence reaches Tier 3. On a
  track that needs it, `runtime.decide` places nothing: the orders wait for `runtime.client_response`, bound to
  those exact orders by `aurea_core/consent.py`, and `act()` asks again. Staff record the answer from the
  evidence, so clients still hold no state-changing tools. `simulated` is for demonstration firms and says so.
- **India is taxed per taxpayer, first-in first-out.** `aurea_core/tax_india.py` reads each person, HUF or trust
  alone and never nets across a family. Lots are not chosen: a firm's `lot_method` does not apply to a market
  with `statutory_fifo`. `aurea_core/harvest_guard.py` is India's guard and `wash_sale.py` stays US-only; never
  port the 30-day block, and never say a rebuy is safe. Figures are estimates and carry `NOT_MODELLED`. The
  Indian firm is `seed/india_demo.py`, marked as a demonstration; its clients are invented and Yahoo's prices
  are for demonstrating with, not licensed for client use.
- **Firm scope.** `household_brain` and every `for_household` take a required keyword-only `firm_id`. Never
  make it optional. Agents pass `ctx.firm.id`; routes pass `firm.id`.
- **Honesty.** Nothing fills on a synthetic price, and a valuation that uses one carries
  `price_source: "synthetic"`; the paper venue never claims a market was reached; a filled order is
  offset, never reversed.

## Working in this repo

- **The backlog is `docs/BACKLOG.md`, and there is only one.** A plan or "what next" answer starts from it,
  not from memory, chat or an old report. Anything found, deferred or finished updates it in the same
  commit, in every session. A status in it is checked against the code or the live site before it is stated.
- **Backend** runs locally with uvicorn against the Azure database. Never use Docker for code changes.
  Azure Postgres is IP-allowlisted and usually unreachable from a dev machine, so verification here relies
  on the SQLite-backed suites below; verify live behaviour through the deployed API.
- **Checks**, from `backend/` with `.venv\Scripts\python.exe -m tests.<name>`:
  `verify_decision_rights`, `verify_tenant_scope`, `verify_orders`, `verify_funding`,
  `verify_model_instruments`, `verify_capacity`, `verify_schedules`, `verify_proposals`, `verify_price_freshness`, `verify_reconciliation`, `verify_reconciliation_agent`, `verify_wash_sale_guard`, `verify_tasks`, `verify_reports`, `verify_messages`, `verify_protection`, `verify_risk_profile`, `verify_client_reviews`, `verify_markets`, `verify_india_tax`, `verify_licences`, `verify_india_demo`. From the repo root:
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
