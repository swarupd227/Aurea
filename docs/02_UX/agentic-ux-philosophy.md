# Astra for Wealth — the agentic UX philosophy

*Adapted from the Astra for Managed Services guideline to wealth management. `CLAUDE.md` carries the
short version. This document is the target the platform is being rebuilt toward; the **Status** section
at the end says what exists today, so nothing here should be read as already built.*

## The one-sentence model

**You talk to a team of agents, and they talk back.** The conversation is the primary surface. Every
action is an intent in plain language, typed or a suggestion chip phrased as one. Every result arrives as
an agent message with a card attached. Pages are artifact views the agents open, not the navigation model.

## The agents

**Astra** is the orchestrator. The firm's workforce speaks through its tools — Drift & Rebalancing,
Next-Best-Action, Client Care, Conduct Surveillance, Onboarding · KYC · AML, Meeting Prep, Research &
Reporting, Tax Intelligence and the rest of the catalogue in `backend/app/agents/catalogue.py`. A reply is
attributed to the one agent whose tools produced it, or to Astra when several did. The workforce is data:
another firm's catalogue and configuration produce its own.

Humans keep the gates, and an agent never crosses one on its own:

- **Proposal decisions** — approve, modify, dismiss, revise, roll back.
- **Trade approval** — a proposal that moves money on a client's book is approved only by an adviser or
  the portfolio team. Compliance may veto it but not approve it. Platform administrators hold neither
  power. The rule lives in `backend/app/core/decision_rights.py`.
- **Autonomy** — the tier each agent may act at, per mandate type. Quality regressions narrow it
  automatically; nothing ever widens it automatically.
- **The kill switch** — pausing an agent, by hand or by conduct surveillance on a high-severity breach.
- **Onboarding gates** — the case cannot activate while a gate is open.

## Rules

1. **Natural language everywhere.** Buttons inside artifact views are the same actions as the tools,
   never different ones.

2. **Every fact comes from a tool.** Astra's prompt carries no client, portfolio or firm facts. A figure,
   status, count or identifier must come from a tool called after the user's latest message. Tool calls
   stay on the message as **Sources**; tool results become the message's **cards**. A failed tool is
   reported with its reason. Stuffing a snapshot of the book into a prompt — as `studio/ask` does today —
   is exactly what this rule forbids.

3. **State-changing tools pause for confirmation, and the server holds the pause.** The loop stops on a
   **Confirm / Not now** card naming the tool and its input. In wealth the confirmation can settle a
   trade, so — unlike the Managed Services implementation, which trusts the browser's word — the pending
   action lives on the server, tied to the thread, and executes exactly once under a row lock. The
   gateway refuses a transcript carrying a tool the role does not hold, or a state-changing result nobody
   confirmed, and rewrites a declined result to fixed text.

4. **Three controls, never merged.**
   - *Confirmation* — "did you mean this".
   - *Decision rights* — "may this role take this decision".
   - *Autonomy* — "may an agent do this on its own".

   Running an agent hands an intent to the runtime; the autonomy tier still decides whether it proposes
   or acts. A confirmation card for a run shows the tier it will resolve to *before* the user confirms,
   because one confirmation on a Tier 3 agent can mean execution.

5. **Agents narrate.** A run shows its beats live in its card — sense, reason, check, decide, act — and a
   gated run settles with a message from the agent that ran it. Replies end with two to four suggestions
   phrased as the next thing the user would type.

6. **Explained, then shown.** One or two plain sentences with the numbers, then the card. Sentences live
   only in agent messages: cards and pages stay figures, tables and chips, with no explanatory prose. An
   agent's rationale is a message, not a paragraph inside a card.

7. **Honesty over polish.** Anything not measured says so. In wealth that means, specifically:
   - A declared figure is called declared — held-away assets a client told us about are not custodied.
   - A synthetic price is named as synthetic. Nothing is filled on one, and a valuation that uses one —
     as private holdings with no market feed must — shows its price source.
   - The paper venue says it reached no market. A fill is never described as a market execution.
   - A filled trade is offset, never "reversed". A rollback that cancelled nothing says so. (Today the
     rollback confirmation reads "effects will be reversed" and the status becomes `rolled_back` even when
     orders had already filled — both break this rule and are due in Phase 5.)
   - A rebalance that could not be fully funded says how far short it fell and why.

8. **One accent.** Dark by default. Yellow `#FFDD00` means exactly three things — an agent is working, the
   primary action, focus. Status uses ok / warn / crit / info. Agents have no colour of their own, and
   neither do autonomy tiers.

9. **Motion tells the truth.** Motion only for live work and arriving messages; a "live" indicator does
   not pulse while nothing is happening; `prefers-reduced-motion` collapses it.

10. **Nothing breaks what exists.** Routes stay. New surfaces add test ids; they never rename old ones.
    `e2e/check-testids.mjs` fails if a script references an id the frontend does not render.

11. **Clients see their own household, and nothing that changes it.** A client may ask about their own
    household only, holds no state-changing tools, and anything shaped like advice is routed to their
    adviser rather than answered. A client never reaches another household's data by passing its id.

12. **Every read is scoped to the caller's firm.** Tools take ids from a language model, so an id is never
    trusted on its own. Household reads require a `firm_id`; `backend/tests/verify_tenant_scope.py` walks
    every call in the backend and fails on one that omits it.

## Surfaces

- **Workspace** — `/workspace` and `/workspace/[id]` (built; the earlier `/w/[threadId]` plan was not used).
  It is the staff home: every staff role lands on it; administrators, the platform owner and clients keep
  their own page. Threads:
  - **Ask Astra** — the firm-wide thread, opening with the day's brief.
  - **One per household** — the natural unit of a client relationship.
  - **One per open onboarding case.**
  - **One per auto-paused agent or open surveillance incident.**

  The left rail reads, top to bottom: **Needs you** (confirmations paused in a thread, plus the agents'
  open proposals this role may decide), **Conversations**, **Views**, then the agents the role can
  mention. *Views* are the existing pages: a role's four or so everyday pages are pinned and the rest sit
  under *More views*, grouped by intent (Decide, Clients & advice, Run the business, Govern the agents,
  Configure). The list lives in `frontend/lib/nav.ts` and is shared by the rail, the classic sidebar and
  the palette. An empty thread teaches by example — *Try saying* shows role-specific sentences, each one
  answerable by a tool the role holds.

- **Artifact views** — the existing pages, at their existing URLs. Their sidebar is the same shape as the
  rail: *Talk to Astra*, *Search or ask*, the role's pinned views, *More views*.

- **A conversation docked on every staff page** (`PageAsk`) — an inline composer and chips phrased for
  the route (Billing: "Prepare this quarter's billing run"). It is the same Astra with the same tools; a
  Confirm / Not now card in it is the server-held pause, and a confirmed change tells the page to reload
  (`astra:changed`). It folds away; it never replaces the page. Not shown on administrator pages.

- **⌘K / Ctrl+K** — global, on the workspace and on every classic page. A typed sentence goes to Astra
  first and opens a new conversation that streams in view; a word or two that names a view or a client
  goes there first with Astra second; conversations are the fallback.

## How it will be built

- **One tool catalogue** — every tool (name, speaking agent, input schema, whether it changes state,
  whether it needs approval rights, the confirmation sentence) and every role's rights. The gateway
  enforces it and the browser reads the same file, so they cannot disagree about a role.
- **The gateway** runs the loop on the server: role checks, transcript validation, the tool loop, the
  pause on state-changing tools, streaming. It goes through the existing model gateway, so PII redaction,
  cost caps and telemetry apply to tool inputs and outputs as well as text.
- **Executors** call the same domain functions the pages already call (`aurea_core`, `atlas.runtime`) and
  return a compact payload plus a card. A tool never has its own copy of domain logic.
- **Threads, messages, tool calls and pending actions** are persisted server-side.

## Extending it

- **New capability → a tool, not a page.** Add it to the catalogue with its rights and whether it
  changes state; add an executor over the existing domain function. Then, if the result deserves it, a
  card kind; then, if the card deserves it, a page.
- **New long run → narrate it.** Publish its card as soon as it starts and settle it into the thread.
- **Anything new must be demonstrable as a sentence typed into a thread.**

## Status

| Phase | What | State |
|---|---|---|
| 0 | Trade decision rights, exactly-once decisions, firm-scoped reads, client document scoping, stable test ids, this document | Done |
| 1 | Visual system: dark default, one yellow accent, ok/warn/crit/info, palette cleanup, honest motion | Partly: dark is pinned on `<html>` and the workspace is on the tokens; most classic pages still use retired `navy-*` / `gold` classes |
| 2 | Tool catalogue, tool-capable model gateway, thread persistence, transcript validation | Done — `core/tools.py` (44 tools), `llm/gateway.py`, `llm/executors.py` |
| 3 | Workspace: threads, rail, messages with Sources and suggestions, Confirm / Not now, ⌘K | Done, except the right-hand detail pane ("Open full view") |
| 4 | Runs narrate into threads; `run_agent` and the streaming path merged; proposal supersession | Mostly: `run_agent` starts a run from a sentence through the Delegate bar's own streaming run (`atlas/streaming.stream_run`), behind a Confirm card that names the agent, what it will do and its tier; the run's card is on the thread from the moment it starts and the agent settles it with a message. Not done: pause / resume (the kill switch is only an administrator route, no domain function to wrap), proposal supersession, and the Cockpit's Delegate bar still exists and is not held to a proposal at Tier 3 |
| 5 | Pages become artifact views: prose out of cards, page-scoped conversations, Ask and Canvas merged | Started — a docked conversation on every staff page, role-pinned views; classic cards still carry prose, Ask-your-book (`studio/ask`) still stuffs a book snapshot into a prompt |
| 6 | The rules as tests, run in CI | Started — decision rights, tenant scope and test-id checks exist |

Staff now land in the conversation. Pages remain, at the same URLs, as views the conversation and the
sidebar open. Agent history, capacity, analytics, onboarding cases and provenance can now be asked for
in a sentence (`read_agent_history`, `read_capacity`, `read_analytics`, `read_onboarding_cases`,
`read_provenance`): each is a thin tool over the function its page's route calls, gated to that page's
nav roles. What is still page-first: meetings, reports, messages, tasks, estate, tax, behavioural
finance, regulatory countdown, skills, book integration and every administrator tab have no
conversation tool yet. Each is a tool to add (see "Extending it"), not a page to redesign.
