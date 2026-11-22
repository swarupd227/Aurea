# A new product, and who to offer it to

How the research team adds a financial product, and how the agents then tell each adviser which of their clients it
fits, why, and what stops it for the rest, without Astra ever recommending a product to a client by itself.

Raised 22 Nov 2026 by a prospective client: "a new financial product can be added in the platform, and then agents
help the advisers on which clients they should pitch it to". Backlog rows B-235 to B-240.

## The decision

**A product is offered only to clients it was made for, on facts a person recorded, and an adviser decides every
conversation.** Three things are added, and everything else is what the platform already does:

1. **A target market on the product**, recorded by the research team from the product's own documents: who it is
   for, who it is not for, and what a client must have on file to be considered.
2. **A fit check** (`aurea_core/product_fit.py`) that reads each client against that target market using only what is
   on file, and says *fits*, *needs a fact first* or *not for this client*, with the evidence for every line. No
   score is a guess, and no missing fact is filled in.
3. **The Product Match agent**, which turns the fit check into one list per adviser per product. Approving the list
   gives the adviser a task per client to discuss it. It contacts nobody, buys nothing and recommends nothing on its
   own, at any tier.

```
 Research team                Fit check (deterministic)              Adviser
 ─────────────                ─────────────────────────              ───────
 Add product        ──►  for each client of each adviser:          "Which of my clients does
 Shelf decision          gates      → not for this client           the Infra Fund suit?"
   (approved)            missing    → needs a fact first                 │
 Target market           signals    → fits, ranked, with evidence        ▼
   (from the PDS/KID)          │                                    List card ─► open beside
 Compliance sign-off           ▼                                          │
   (if complex or        Product Match agent ─► one proposal per    Approve: a task per client
    conflicted)          adviser per product (Tier 1, forced)        Remove: with a reason
                                                                          │
                                                                    Discuss ─► record the outcome
                                                                    Message ─► Confirm card, exact words
                                                                    Buy ─► rebalancer / direct order,
                                                                           guarded as today
```

## Why not let an agent pick clients freely

The obvious design asks a model to read the book and name likely buyers. It fails the rules this platform is held to:

| | What goes wrong |
|---|---|
| Suitability | Reg BI's care obligation, the FMC Act's fair dealing and the Code's suitability standard, the FCA's PROD 3 and Consumer Duty, and MiFID product governance all ask *why this product for this client*. A model's hunch is not a record. |
| Honesty | A client with no risk profile on file would be read as fitting or not fitting. Here "not on file" is never "no", and a figure is never worked out (CLAUDE.md, protection, net worth, the plan). |
| Conflicts | A ranking that a model shapes can drift toward what pays the firm. The fit check never reads fees, commission or margin. |
| Evidence | A pitch must show what the adviser saw on the day. A model's reasoning cannot be replayed; a fit snapshot can. |
| Volume | A product is checked against every mandate in the firm. Rules over the local tables do that in seconds; a model per client does not. |

So the matching is rules over recorded facts, and the model's job is the one it is good at: saying, in the adviser's
conversation, what the list shows.

## The parts

### 1. The product and its target market

**Adding the product.** A product is an `Instrument` (`models/portfolio.py`): symbol, name, asset class, currency,
market type, and its tags. It needs a real price before anything values it (Honesty: nothing fills on a synthetic
price). Adding one becomes a tool, `add_product`, for the research team and the portfolio team, behind a Confirm
card. The shelf decision stays `record_product_decision` (approved, watch, closed; minimum investor class; family and
share class; expense ratio; diversified), unchanged.

**The target market** is new: `product_target_market`, added and never edited, one version per change, the latest in
force. Every field is read from the product's own document (PDS in New Zealand, KID or EMT in the UK and EU,
prospectus or offering memorandum in the US, scheme information document in India), and the document is named.

| Field | Values | Read against |
|---|---|---|
| `objectives` | growth, income, preservation, diversification | the mandate's objective and the client's goals |
| `risk_profiles` | any of conservative, balanced, growth | the adopted profile (`suitability["risk_profile"]`) |
| `min_capacity` | low, medium, high | `capacity_for_loss` |
| `min_horizon_years` | number | the mandate's horizon, else the goal dates |
| `min_investment` | amount, currency | investable cash after holds (`cash_movement.held_back`) |
| `complexity` | simple, complex | complex needs compliance sign-off and the client's knowledge on file |
| `liquidity` | daily, weekly, monthly, quarterly, locked (years) | the horizon; the client's liquidity need |
| `markets` | NZ, US, UK, EU, IN | the household's market (`markets`) |
| `mandate_types` | discretionary, advisory, execution only | the mandate's type (accounts carry no registration type here) |
| `negative_market` | conditions it is *not* for | a hard stop |
| `distribution` | advised only, or also execution only | whether it may be offered without advice |
| `conflict` | none, proprietary, third-party payment | the conflicts inventory; disclosure on every message |
| `document`, `source`, `as_of` | the paper it came from | shown on every list |

The research team records it (`record_target_market`, research and portfolio team, Confirm card). A product that is
**complex or conflicted** is not offered until **compliance signs off its distribution** (`approve_distribution`), and
the person who recorded the target market may not sign it off. That is a second person, as for a true screening match.

Nothing is offered until the product is **approved on the shelf, has a target market in force, and (where needed) a
sign-off**. Watch or closed stops new lists at once; open lists are withdrawn.

### 2. The fit check: `aurea_core/product_fit.py`

`assess(session, firm, product, mandate) -> Fit` reads one mandate against one product, and `for_adviser` /
`for_firm` run it across a book. It reads the local tables only and writes nothing. Each line of the result is a
criterion, its outcome, and its evidence with the date it was recorded.

**Gates: any one makes it *not for this client*.** Each reuses the rule that already decides it:

| Gate | Source |
|---|---|
| Closed, gated above the client's certification, not approved while approved-only is on | `shelf.barred` |
| A plan the licence does not buy (India) | `shelf.plan_bars` |
| Restricted by the IPS, or a values exclusion | `ips.restricted_by`, `ips.exclusions_of` |
| Would put equity over the capacity ceiling at the minimum investment | `ips.book_after`, `risk_profile.CAPACITY_EQUITY_MAX` |
| Profile, capacity, horizon or market outside the target market | the target market |
| In the negative target market | the target market |
| The client is leaving, has died, or holds an exit hold | `exits` |
| Already holds it (shown as a top-up, not a new pitch) | holdings |
| Declined it in the last six months, and nothing on file has changed | `product_pitch` |

A client under transaction-monitoring investigation is left off **without a reason the adviser can read**, because
monitoring is never told to the client or the adviser. Compliance sees the count.

**Not assessed: *needs a fact first*.** A gate that needs a fact not on file is not failed. It is listed as the
fact to record: "no risk profile adopted", "no horizon recorded", "no investor certification for a gated product".
These clients are a separate list, because recording the fact is the adviser's real next step, and the answer may
then be no.

**Signals: why it may matter now, used only to order the clients who fit.** Each is a fact with its figure:

- Cash above the reserve the plan needs (`_signals` idle cash, `financial_plan` reserve).
- A goal off track whose objective the product serves (`goal_tradeoff.likelihoods`).
- A concentration it would diversify (`_signals` concentration).
- A dearer share class of the same family already held (`shelf` family): the cheaper class is what fair value asks.
- Similar assets held away (`balance_sheet` held away), for a consolidation conversation.
- A review due soon (`reviews`), so the conversation fits an existing meeting.

The order is points per signal, shown on the list, the same for every product. It never reads the product's fee,
the firm's revenue or the adviser's pay.

### 3. The Product Match agent

A new agent, `product_match` (`AgentKey`, `build_registry`, `CATALOGUE`), lifecycle stage "grow":

- **When it runs:** when a product first becomes offerable (approved, target market in force, signed off), monthly
  after that while it stays offerable, and when an adviser or the research team asks (`run_agent`).
- **What it raises:** one proposal per adviser per product: "Clients for {product}: {n} fit — {adviser}". The
  subject is the product, and the title carries the adviser, so `atlas/proposals.py` refreshes the open list rather
  than stacking a new one each month. The payload is the fit snapshot: who fits and why, who needs a fact, and how
  many are not for it.
- **Its tier:** Tier 1, and forced to a proposal (`agent_runs.forced_to_proposal`) whatever a firm sets, because
  choosing whom to offer a product to is advice.
- **Who decides:** the adviser it names, or a branch leader (`decision_rights.DISTRIBUTION_AGENTS`); compliance may
  dismiss or roll back a list but not approve it. `act()` refuses an adviser approving another's list.
- **Approving:** a task per client the adviser kept: "Discuss {product} with {household}", carrying the fit snapshot.
  **Modifying** removes clients, each with the adviser's reason, kept on record. **Rolling back** withdraws the open
  tasks. Nothing reaches a client.

Conduct surveillance reviews every list as it reviews every proposal, with two new rules: no client outside the
target market on a list, and no list for a product whose sign-off is missing.

### 4. The pitch, from conversation to outcome

A pitch is a record, `product_pitch`: household, product, adviser, the fit snapshot it rested on and the target-market
version, the status (to discuss, discussed, interested, declined, not suitable after all), the adviser's reason, and
any message sent. It is added to, never edited.

- **Discussing:** the adviser's task carries the fit lines and the document. The meeting brief does not read pitches
  yet (B-238).
- **Writing to the client:** only through `send_client_message`, in the adviser's name, behind the Confirm card with
  the exact words. The over-promising rule runs. A message that names a product with a target market has the
  product's document, and a conflicted product's disclosure word for word, added after the adviser's words
  (`product_distribution.with_disclosures`); the card shows the result, so what is confirmed is what is sent.
- **The outcome:** `record_pitch_outcome` (adviser, Confirm card). A decline suppresses the product for that client
  for six months unless something the fit check reads changes, the same rule protection uses so a standing offer is
  not raised monthly.
- **Buying:** unchanged. The rebalancer or a direct order places it, and `order_guard` holds it to the shelf, the
  IPS, the capacity ceiling and the tax window as today. **One addition:** a buy into a product's negative target
  market is refused, and a buy outside its target market (not negative) needs the adviser's reason, which goes on the
  ledger and into the distribution report. (Whether outside-target-market buys are refused or allowed with a reason
  is the firm's choice, below.)

### 5. Governance: what compliance and the research team see

- **The distribution report**, per product: lists raised, clients offered, interested, declined, bought, bought
  outside the target market (with each reason), and any complaints (none are recorded here, and the report says so).
  It is the evidence PROD 3 and Consumer Duty ask a distributor to send back to the manufacturer, and it feeds the
  product's `next_review_on`.
- **The fairness view:** who was offered what, by adviser, so a skew toward one product or one adviser shows.
- **Conflicts:** a conflicted product appears in the conflicts inventory (`compliance_program`), and its disclosure
  line is checked on every message.
- **The EU product-governance rule becomes real.** `eu.mifid.product_governance`'s evaluator (`rules.target_market`)
  passes whenever a mandate type exists today; it will read the target market in force.

### 6. What each market changes

| Market | What it adds |
|---|---|
| NZ (FMA) | Fair dealing (FMC Act s22) and the Code's suitability standard: the fit lines are the suitability record; the PDS is named on every message. |
| US (SEC) | Reg BI: the care obligation is the fit snapshot; the conflict obligation is the disclosure line. Accredited and qualified-purchaser products use the certifications already on file. |
| UK (FCA) | PROD 3: the firm's own target market and negative target market, and the distribution report back to the manufacturer. Consumer Duty: the cheaper share class signal, and price and value named on the product. |
| EU (MiFID) | Product governance as UK, with the EMT as the document the target market is read from. |
| IN (SEBI) | An investment adviser is fee-only: it offers no product it is paid to distribute, and the licence's plan rule (`plan_bars`) stands. A mutual fund distributor offers regular plans. Each buy still needs the client's consent on its track. |
| None | A firm with no market gets no lists (`NO_REGIME`), as it gets no tax report. |

### 7. In the conversation (Modern Agentic UX)

| Item | What it is |
|---|---|
| Tools | Read: `read_target_market`, `read_product_fit` (one product; mine, one adviser's or the firm's), `read_pitches`. Change, each behind a Confirm card: `add_product`, `record_target_market`, `approve_distribution`, `record_pitch_outcome`; `run_agent` for Product Match. |
| Cards | Target market (chips: objectives, profiles, horizon, minimum, complexity, liquidity, conflict); Fit list (client, verdict chip, signal chips, the gate or missing fact); Pitch outcome. Figures and chips only. |
| Pages | `/studio/products/{id}`: the product, its target market history, its sign-off, and a "Who it fits" tab. Opens beside the conversation. |
| Examples | Research: "Add the Pathfinder Global Infrastructure Fund, PDS attached." Adviser: "Which of my clients does it suit?", "Why not the Hartleys?", "Put it in my brief for the Morrisons", "Draft a note to the Chens about it". Compliance: "Who has been offered the Infra Fund outside its target market?" |

A client sees none of this. Canvas is unchanged, and a client's assistant holds no product tools.

## The data, in short

| Table | Holds | Written by |
|---|---|---|
| `product_target_market` | the target market, versioned | research, portfolio team |
| `product_distribution_signoff` | compliance's sign-off for a complex or conflicted product | compliance (not the recorder) |
| `product_pitch` | each step of offering a product to a household, with the fit it rested on as of that day (`fit`) | the adviser; the agent on approval |

All three are new tables, so `create_all` adds them; no column is added to an existing table. The fit snapshot is kept
on the pitch it supports rather than in a table of its own: nothing reads one without its pitch.

The negative target market is read in one place, `product_distribution.negative_findings`, and barred through
`shelf.barred`, so the rebalancer, a direct order and the fit check all refuse the same buys.

## Phases

| Phase | Delivers | Backlog |
|---|---|---|
| 1 | `add_product`; the target market and its tool, card and page tab; the EU rule made real | B-235 |
| 2 | `product_fit.py` with gates, missing facts and signals; `read_product_fit` and its card | B-236 |
| 3 | The Product Match agent, its decision rights and tasks; surveillance rules | B-237 |
| 4 | Pitches: brief, message checks, outcomes, suppression; the order-guard target-market check | B-238 |
| 5 | Compliance sign-off, the distribution report and the fairness view | B-239 |
| 6 | Market specifics: EMT import for UK/EU, the India licence path | B-240 |

Phases 1 and 2 give an adviser a useful answer in the conversation on their own; the agent and the reports build on
them.

## Decisions for the firm

Taken 22 Nov 2026 on the recommendations below, and built that way:

1. **Outside the target market:** a buy into the negative target market is refused, for the rebalancer and a direct
   order alike; a buy outside the target market otherwise goes ahead and is named on its Confirm card.
2. **Who records a target market:** the research team by hand from the document. An EMT import is later (B-240).
3. **Approving a list:** a task per client who still fits, and a pitch carrying the fit. The meeting brief does not
   read pitches yet (B-238).
4. **How often a client may be offered products:** at most two open pitches per household (`MAX_OPEN_PITCHES`).
5. **India:** lists run; a firm licensed only as an investment adviser is fee-only and offers no product that pays
   a third party; the licence's plan rule (`plan_bars`) stands.

Not modelled: a client's knowledge and experience of complex products is not recorded anywhere here, so complexity
is governed by compliance's sign-off, not read against the client.

## Proving it

`tests.verify_product_distribution` (71 checks): adding a product and its target market, with every refusal; the
second-person sign-off; offerable only when everything is on file, and never for a firm with no market's rules; each
gate refuses with its rule, a missing fact is "needs a fact" and never a no, signals order those who fit, a monitored
household is left off with no count shown to an adviser; the negative target market barred for the shelf and a direct
order, and a buy outside it named; Product Match raises one list per adviser per product, refreshed not stacked,
forced to a proposal, approved only by the adviser it names or a branch leader, approving gives tasks and pitches,
removing keeps the reason, rolling back withdraws, a closed product raises none; outcomes need reasons, a decline
keeps the product away, a message carries the document and the disclosure; the report; the fee-only rule; the rule
that reads a list. On the real model (22 Nov), research's "add the fund", an adviser's "which of my clients does it
suit?" and "why not the Chens?", and compliance's "has anyone bought it outside its target market?" each reached the
right tool, two in two.
