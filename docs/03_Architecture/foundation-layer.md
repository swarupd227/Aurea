# Astra over a Foundation layer

How Astra becomes the experience layer over a firm's established products (a wealth platform such as WealthSmart,
Dynamics 365 as the CRM, a custodian), without disturbing the firms, agents and tests that run on Astra's own data
today.

Raised 17 Nov 2026 by a prospective client's target architecture: a Foundation layer of established products, an
experience layer of pages and agents on top, the agents reaching the Foundation through MCP, and Dynamics 365 as the
CRM. Backlog rows B-161 to B-166.

## The decision

**Astra keeps its own tables as a local copy of Foundation data, fed through a versioned contract with swappable
adapters. Nothing that reads client data changes.** A "native" adapter is today's setup: a firm configured to it
behaves exactly as now. A firm whose book lives in its wealth platform is configured to that adapter, per domain, and
the same agents, cards and rules run on what it supplies.

```
   Wealth platform        Dynamics 365          Custodian / others
        │ MCP / API             │ Dataverse            │ files / API
        ▼                       ▼                      ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ Adapters (native · wealth platform · d365 · mock)            │  swappable, per firm and domain
 ├──────────────────────────────────────────────────────────────┤
 │ Canonical contract v1 (Party, Household, Account, Mandate,   │  the fixed contract
 │ Holding, TaxLot, Transaction, Cash, Price, Contact,          │
 │ Opportunity, Activity)                                       │
 ├──────────────────────────────────────────────────────────────┤
 │ Sync (conduit): scheduled pull + events, as_of, idempotent   │
 │ upsert by (source, external_id), field ownership, breaks     │
 │ reported never overwritten                                   │
 └──────────────────────────────────────────────────────────────┘
                              ▼
  Astra's tables: a local copy of what the Foundation owns, the system of record for what Astra owns.
  Unchanged readers: household_brain, the rebalancer, the guards, valuation, reconciliation, agents, cards.
                              │
  Commands (orders, CRM activities) ──► outbound ports ──► Foundation
  The copy changes only when the Foundation's confirmation comes back through sync.
```

## Why not abstract every read

The obvious design puts an interface in front of each read and points it at the Foundation live. Measured on 17 Nov:
`household_brain` (`aurea_core/graph.py`) is one way in of many. 62 files query holdings, accounts, lots,
transactions or mandates directly and 116 issue their own queries: the rebalancer, `wash_sale`, `order_guard`,
valuation, reconciliation, the tax modules. Reading through to a remote system breaks five things:

| | What breaks |
|---|---|
| Volume | Next-best-action scans the whole book; the wash-sale guard reads 30 days of trades across every account. Per-call MCP latency and rate limits make that minutes, or a failure. |
| Exactly-once | `runtime.decide` locks the recommendation and re-reads it inside one database transaction. That cannot span a remote API. |
| Evidence | A proposal records the data it saw, as of a date. A live read can change under it, and the ledger could not show what the decision was based on. |
| Tests | Every `tests.verify_*` suite runs on SQLite with no network. |
| Size | Rewriting 62 readers is a rewrite, and each one is a chance to lose a rule CLAUDE.md lists. |

A local copy keeps all five, and the abstraction sits where it pays: at the boundary, once per domain.

## The parts

### 1. Canonical contract, versioned, one per domain

Pydantic schemas in a new `app/foundation/contract/` package, one module per domain, so a firm can take its book from
one product and its CRM from another:

| Domain | Records | Usual source |
|---|---|---|
| parties | Household, Person, LegalEntity, RelationshipEdge | Wealth platform, CRM |
| accounts | Account, Mandate, AccountBeneficiary | Wealth platform |
| positions | Holding, TaxLot | Wealth platform, custodian |
| activity | Transaction, CashBalance | Wealth platform, custodian |
| prices | Price, Instrument | Market data (already a live connector) |
| crm | Contact, Opportunity, Activity | Dynamics 365 |

Every record carries `source` (the connector), `external_id`, `as_of`, and `contract_version`. A contract change is a
new version; adapters say which they speak.

### 2. Capabilities, so a gap says "not assessed"

Each adapter declares what it can supply, for example `{"tax_lots": false, "transactions_days": 90, "events": false}`.
The modules that need a missing capability report "not assessed", as protection and net worth already do for a
missing income or debt. A wealth platform with no tax lots means the CGT budget and the wash-sale guard read "not
assessed" and the rebalancer proposes nothing that depends on them. Nothing is estimated to fill a gap.

### 3. Adapters

- **native**: maps Astra's own tables to the contract and back. The default for every firm, and a no-op at run time:
  sync does nothing because the tables are the source.
- **mock**: the conduit mocks as they are, now speaking the contract, for demonstration firms.
- **d365**: Dynamics 365 through the Dataverse Web API (or Microsoft's Dataverse MCP server where the client runs it).
- **wealth platform**: the client's API, data feed or MCP tools, whichever it offers; the adapter hides which.

An adapter is a conduit connector. `conduit/registry.py` already lists providers per `ConnectorDomain` (custody,
portfolio accounting, CRM with `crm.dynamics`, held away) and `conduit/service.run_sync` already records each sync in
`ConnectorSync`. Today every domain but market data records a no-op "mock" sync; this work gives those syncs a body.

### 4. Field ownership, per firm and domain

`firm.settings["foundation"]` says, per domain, which system owns it. A sensible starting split:

| Foundation owns | Astra owns, and stays the system of record for |
|---|---|
| Parties and households, accounts and mandates, holdings and lots, transactions and cash, prices, CRM | The decision ledger, proposals and decisions, Confirm cards, conversations, tasks, supervision flags, consents, risk questionnaires, IPS records, research, agent controls and tiers |

Where the Foundation owns a domain, Astra's tools that record a fact in it either send a command to the Foundation
or are refused with the owner named, the rulebook's pattern (`aurea_core/rulebook.py`). A difference between the copy
and the source is a break, reported with its evidence, never overwritten in either direction: the rule
`aurea_core/reconciliation.py` already follows.

### 5. Writes go out as commands

Orders, CRM activities, and anything else the Foundation owns leave through outbound ports
(`OrderSink`, `CrmActivitySink`). The local copy changes only when the Foundation's confirmation syncs back. That is
already the rule for money (a custodian's paper moves it) and for fills (offset, never reversed); it now covers every
Foundation-owned record.

### 6. Sync

- Pulled on a schedule by the worker, which already runs per-agent schedules (`agents/schedules.py`); taken as events
  where the source offers them.
- Upserted by `(source, external_id)` through an `external_ref` table mapping each to Astra's UUID, so the ids every
  route, card and test id uses stay stable.
- `as_of` on every record. A brain and a card show how fresh their data is, and an agent declines to propose a trade on
  data older than the firm's limit, as `aurea_core/price_freshness.py` does for prices.
- Idempotent: running a sync twice changes nothing the second time.

### 7. Conformance kit

A golden household set goes out through the native adapter into the contract and back in through the adapter under
test. `household_brain`, account valuation, the rebalancer's proposal and the rule outputs must match the native
store's. An adapter is done when it passes. The kit is a `tests.verify_foundation_*` suite like the others.

## Where MCP fits

| Use | Through |
|---|---|
| Bulk sync of the book and the CRM | The product's API or feed, or its MCP tools if that is all it has. The adapter decides. |
| Live context while an agent prepares (the latest CRM activities, today's news) | MCP at the time, read-only, cited with when it was read. Never the basis of a decision or an order. |
| Other agents using Astra's tools | Astra as an MCP server. The opposite direction and a separate, smaller piece of work (B-166). |

## Order of work

Each step is safe on its own: no existing firm or test changes until a firm is configured to a new adapter.

| Step | Row | What | Changes for existing firms |
|---|---|---|---|
| 1 | B-161 | `source`, `external_id`, `as_of` columns on the Foundation-owned tables (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `db_bootstrap`), the `external_ref` table, `firm.settings["foundation"]` read with native as the default | None |
| 2 | B-162 | The v1 contract, the native adapter, capabilities, and the conformance kit: native out and back in gives the same brain | None |
| 3 | B-163 | Dynamics 365 CRM adapter: contacts, opportunities and activities in; activities out. Smallest domain, and asked for | None until a firm selects it |
| 4 | B-164 | Wealth platform, read side: parties, accounts and positions into a demonstration firm marked as sourced from the Foundation, beside the native firms | None |
| 5 | B-165 | Outbound orders through the wealth platform, the copy changing on its confirmation | None |

## Open questions for the client

- What the wealth platform exposes today: API, data feed, events, MCP.
- Whether it holds tax lots and how much transaction history. That decides how much of the tax and trading logic works
  on its data on day one.
- Which system owns IPS and risk-profile records, if the platform keeps them.
- Their markets. A South African firm resolves to `NO_REGIME` today (`aurea_core/markets.py`): no rules, no tax report (B-206).

The agents the client asked for on top of this (meeting preparation, introductions, personas, learning) are B-198 to
B-205 in the backlog's experience-layer section.

## As built (20 Nov 2026, B-161 to B-163)

- The connector column is `source_system` on every Foundation-owned table, not `source`: `crm_contact.source` already
  says how a prospect came to the firm and `price.source` names the feed.
- Owners live in `firm.settings["foundation"]["sources"]`, beside the agent policy the `foundation` key already held;
  `core/foundation.policy` ignores `sources`.
- The sync compares three values per field: here, the source, and what the last sync applied (`external_ref.synced`).
  The copy follows the source only where it is untouched since the last sync; a field changed on both sides is a break.
- Instruments and prices are matched by symbol and by (instrument, day); a model portfolio is named, not synced, because
  it is the portfolio team's record here. Goals, adviser edges and beneficiaries are not in contract v1.
- Code: `backend/app/foundation/`; suites `tests.verify_foundation_contract` and `tests.verify_foundation_dynamics`.
## As built (21 Nov 2026, B-164 and B-165, with B-221 and B-222 simulated)

- The client's target is the wealth platform as the Foundation, reached through MCP. `foundation/adapters/mcp.py`
  (`foundation.mcp`) is an MCP client written to the wire protocol (JSON-RPC 2.0 over Streamable HTTP, 2025-06-18; no
  `mcp` package), reading the **Foundation MCP profile v1** (`foundation-mcp-profile.md`): `foundation_capabilities`,
  `foundation_read` (paged contract records) and `foundation_command`. It owns parties, accounts, positions and activity;
  prices stay the firm's own feed.
- Its capabilities are what the server declares, recorded in `firm.settings["foundation_declared"]` at each read (a key
  of its own, so the agent policy is untouched) and read through `Adapter.capabilities_for(firm)`; before the server has
  said anything, nothing is claimed.
- Orders out (`foundation/orders.py`): with the positions owned by a product that takes `orders.place` (and owns the
  activity too), `venue_for_firm` is the platform. An approved proposal's orders and a direct order leave as a command
  and wait as placed; holdings, cash and the trade arrive through the sync, and the trade's `lineage.order_ref`
  confirms the command and records the fill on the order, already booked, so `orders.settle` never runs. A product that
  takes no orders refuses the card naming itself. `order_guard` does not relieve lots a product does not keep: a sale's
  gain reads not assessed instead of being refused.
- The Foundation read and card show each owner's platform, a `simulated` chip when the owner's server or connector says
  so, and waiting orders as figures (`buy 10 VTI`).
- Simulated (thrown away when WealthSmart exposes its server): `app/simulations/wealth_platform/` is an in-process MCP
  server speaking the profile for a demonstration firm (`platform-sim`), and `app/simulations/dynamics_demo/` puts the
  Dynamics 365 mock on that firm's CRM. Suites `tests.verify_foundation_mcp` (the real adapter, against a test server in
  the suite) and `tests.verify_sim_wealth_platform` (the simulation, deleted with it).
