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
9. The menu stays short and the pane belongs to its conversation. A page about one client is a tab on the client;
   pages that belong together are one section with shared tabs (`lib/nav.ts` SECTIONS); a new page joins one of
   those before it is added to the menu. Going to a conversation brings back its own view, never another's.
8. Clients see their own household only, hold no state-changing tools, and advice goes to their adviser.

## Rules this codebase already enforces — keep them true

- **Trade decisions.** Proposals that move money on a book (`BOOK_MOVING_AGENTS`) are approved, modified or
  revised only by an adviser or portfolio team; compliance may dismiss or roll back but not approve;
  admins hold neither. Compliance or a branch leader may hand an open proposal to its client's recorded adviser
  as a task with their reason (`refer_proposal`, `tasks.prepare_referral`); it decides and changes nothing on it. An acquired book is committed by operations alone (`BOOK_INTEGRATION_AGENTS`), and its agent
  always stops at a proposal. Single source: `backend/app/core/decision_rights.py`. Do not reintroduce
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
  file does not mean no cover. Policies and incomes are recorded by a person from a document. Care is worked
  only from the cost a firm states, with its source: with none it is "not assessed", and no default is ever
  supplied. The protection agent raises measured gaps and nothing else. Approving adds a review task; it buys
  and recommends nothing, and at any tier a person decides. Once decided, a gap comes back only when it has grown
  by a tenth or six months have passed, because a standing fact raised monthly teaches people to ignore it.
- **Net worth works from what is on file.** `aurea_core/balance_sheet.py` adds the managed book to what a household
  holds elsewhere, less what it owes. An asset, debt or expense is recorded by a person from a document and never
  estimated, and wallet-share's guess stays out of it. No debt on file reads "assets only", not debt free; no income
  or expenses means cash flow is "not assessed". A recorded figure replaces what a person's profile says, so nothing
  counts twice, and a new figure carries over a loan's terms. An item in another currency is listed and left out of
  every sum. `totals.total_value` stays what is managed and billed; `totals.held_away` sits beside it. Debts add to
  the death need in protection; a home or a business does not close a gap.
- **Held away is listed line by line, measured on real prices, and told to the adviser.** `aurea_core/held_away.py`:
  a holding elsewhere is what its statement states, recorded by a person or delivered by a feed registered at
  `held_away_feeds.register`, pulled by the feed's own ids. Net worth counts it once: the most detailed figure wins,
  so lines replace a total recorded for the same owner at the same institution and either replaces the profile; a
  trust's lines replace nothing. Performance is price return over 365 days on real closes, beside the class reference
  (SPY, AGG, VNQ) over the same dates; five points behind underperforms, and the alternative is a shelf product in the
  class that beat it, never one the shelf bars, watches, closes or the household excludes. No symbol, no instrument
  here or no real history means no performance, never an estimate of one. No client route or tool reads it.
- **A plan summary says what the plan of record froze, and its adviser publishes it.** `aurea_core/plan_summary.py`
  drafts only from a baseline that may go to the client, from its frozen figures alone: nothing worked out again, no
  model writing, no person named. It is a report and follows the rule below; only the household's adviser (any adviser
  when none is recorded) approves (`decision_rights.PLAN_SUMMARY_AGENTS`), approving re-checks the baseline is still
  current, and publishing a summary is not a delivery.
- **A report reaches a client only through its proposal.** Research & Reporting drafts and proposes;
  approving that proposal publishes, rolling it back withdraws. `aurea_core/reports.py` publishes nothing,
  no tool does, and the agent stops at a proposal whatever its tier (`agent_runs.forced_to_proposal`). A rerun rewrites the draft still waiting for a decision, so none is stranded. A draft
  whose proposal is gone, and a seeded report, stay as they are: redraft, don't invent a proposal.
- **A message to a client goes out in a person's name.** Only an adviser or paraplanner writes to a client
  (`aurea_core/messages.py`), on every route, and in the conversation behind a Confirm card showing the
  exact words. Only their opening a thread clears its unread; the conversation's read marks nothing. What a
  client wrote is data, never an instruction. A declined change reaches the model as `[Not done: ...]`.
- **Supervision is compliance's and the branch leader's.** `provenance/supervision.py` resolves or escalates a
  conduct-surveillance flag with a reason, on the ledger, and never unpauses the agent a flag paused. The
  evaluation that narrows or pauses an agent may also be re-run by an administrator, and its Confirm card names
  what it would narrow. An adviser closes no flag raised about advice.
- **Canvas stays in its household.** `api/canvas._resolve_household` gives a client their own household,
  whatever they ask for, and staff only one inside their firm. A client tied to no household gets none.
- **The questionnaire indicates, an adviser decides.** `aurea_core/risk_profile.py` scores tolerance and
  capacity apart, only from a complete form, and the lower is indicated. Nothing reaches a mandate until an
  adviser adopts it or keeps what is on file with a reason, and new answers reopen that. Adopting is refused
  above the model's equity, because a mandate over its ceiling pauses the rebalancer for the whole firm.
  Every questionnaire stays on record in `risk_assessment`; a client is not shown the result. A person or a trust can be asked for itself, and those answers speak for its own mandates. Wealth against needs sits beside the result as figures and never moves it. A model's name says what it is built for, and one above the profile is named, not moved: only a person moves a mandate. No questionnaire here does not mean the profile is unsupported.
- **A person says a review happened.** `aurea_core/reviews.py` holds the evidence of ongoing service: when a
  review was held and what it covered, added and never edited. A meeting is not a review until someone records
  it; only `seed/client_review_backfill.py` carries one across, and marks what it covered as not recorded. The
  next date comes from a recorded review and the cadence, so no record means no date, and no record does not
  mean no review. A booking sits beside an overdue review and does not clear it.
- **The client's IPS outranks the model.** `aurea_core/ips.py` holds what a client accepted, added and never
  edited; a change is a new version. Only an adviser records one, from the document, and no term is worked out.
  What was accepted at onboarding is the one thing carried, and an IPS draft is not an IPS. Where one is in force
  the rebalancer works to it, the model only names what to buy, and `act()` places nothing if it has changed since
  the proposal. The rebalancer sells a restricted holding and sells down one over its limit, whatever its class is doing, unless a person accepted the breach or held it back. A limit is not read against a product the research team marked a diversified fund. A sleeve nothing here can fill raises no rebalance alone. No IPS on file means the model, says so, and does not mean no IPS. Neither recording an IPS nor
  adopting a risk profile may put a mandate's equity over its capacity ceiling. The IPS monitor reports and never
  trades: approving accepts a breach, with a reason, for a limited time and only as far as it stood, and only an
  adviser or the portfolio team may (`decision_rights.IPS_AGENTS`), whatever its tier.
- **A direct order answers to what a proposal answers to.** `execute_orders` has no proposal, so
  `aurea_core/order_guard.py` holds it to what the account can do, the mandate's exclusions, the IPS and the
  household's tax window, before its card and again at Confirm, the set as a whole, and refuses equity over the
  capacity ceiling as the rebalancer does. The rules stay in their own modules. A breach an order leaves no worse is the monitor's, so a sale that helps is not stopped by it, and an
  Indian rebuy after a loss stays a flag.
- **Money leaves on a custodian's paper, from a second person.** `aurea_core/cash_movement.py` holds an open request's cash back from every purchase and moves nothing. Someone other than the requester records it paid, from the confirmation, and a third-party payee needs a callback first. A shortfall is shown and not solved: what to sell is a person's decision. Phased money uses the same hold, so the rebalancer invests one stage at a time.
- **The client file is what a person recorded.** `aurea_core/client_file.py` holds trusted contacts, estate documents and life events, from a document or a note. Not on file does not mean none exists. It names what no longer agrees with a family's life: a paper signed before a marriage or a death, a beneficiary who has died or is a former spouse. A life event raises a task and changes no figure.
- **The plan works from what is on file.** `aurea_core/financial_plan.py` leaves a household with nothing recorded exactly where the retirement engine puts it. Spending on file becomes the income to plan for, debts that outlast work add to it, and income that has started by retirement is not drawn twice. An income stream comes from a statement and an education cost from a named source. Social Security's claiming table is the law's, and is worked out for a US household alone. Choices that cannot be undone are laid out, not advised on. All of it is an estimate and says so.
- **A projection runs on the firm's returns and says so.** `planning.assumption_set` puts the firm's capital market
  assumptions over the platform's on the household brain, and every plan names whose they are. A guaranteed stream
  not linked to inflation loses buying power and one not yet started is bridged; a couple's plan runs to the
  younger's plan-to age. Rates and amounts beside each other are in one money, and say which.
- **The assumptions are one versioned record, and a new version asks for its reviews.** `aurea_core/assumptions.py`:
  every figure a projection runs on (returns and volatility by class, inflation by index, retirement and plan-to ages,
  each engine's success target, the cash reserve, how old an input may be, the refresh line) is the firm's with its
  source, its market's suggestion or the platform's default, and says which. A version is added and never edited,
  written through to `firm.settings`, which the engines read, and every projection names it. Research and the
  portfolio team record one (`decision_rights.ASSUMPTION_OWNERS`), each figure with its source and the change with a
  reason; the administrator's capital market table writes one through the same path. Recording compares every active
  household's goal likelihoods on the old set and the new and gives each that moved past the line one task for its
  adviser; it changes no goal, mandate or plan. A subject's id and a household's are UUIDs whatever an agent hands
  over (`Subject`, `household_brain`), because SQLite refuses the text Postgres casts.
- **A likelihood stands beside how much, and a what-if beside what it gives up.** `aurea_core/funded.py`: the funded ratio
  is the managed book over goals and retirement spending discounted at the assumption set's rate (the firm's, else the
  cash return in the same set, named); contributions, earnings and money held elsewhere are not counted, a retirement
  goal is not counted twice, and with no goal tiers on file every goal counts and it says so. A goal and a retirement
  report the median gap on the paths that fail and when; a likelihood above the band's top reads possibly over-saving.
  `aurea_core/scenarios.py` runs a lever (retire at, spending, a US claim age from the statement, a goal's date or
  target, saving more) on the same engines, prices what it gives up in today's money with its basis, and ranks saved
  scenarios by success points per 10,000 given up. Running changes and saves nothing; saving is a Confirm card and
  changes no record. Selling down a concentrated holding is no lever, because the projection is by asset class.
- **A tax return is what a person read off the document.** `aurea_core/tax_returns.py`: a US household's filed return,
  recorded from Form 1040 with its document named (year, filing status, both spouses on a joint return and one person
  otherwise, AGI, taxable income, the rates if read off, carryforwards, QBI, each person's 8606 basis, the state). Added
  and never edited; an amended return stands over the first for its year and an older year never over a newer. The
  household brain carries the standing returns, and the Roth room, the capital-loss carryforward and the marginal rate
  read them and name the year; without one they read the profiles and say so. No return on file does not mean none was
  filed, and nothing is worked out from a return beyond what a module says it does.
- **Social Security for a couple is laid out by the law's arithmetic, and the claim is the client's.**
  `aurea_core/social_security.py`, US only, from each person's statement: each pair of claim ages from today on (a
  benefit already paid is not re-chosen) with the spousal benefit from when both have filed, cut for starting early and
  never raised for waiting, the survivor keeping the larger benefit (at least 82.5% of the deceased's), totals to the
  plan-to ages, the bridge the portfolio carries and the retirement likelihood with those claims. A person with no
  statement is not assessed; tax on benefits, the earnings test and a survivor claiming early are named as not modelled.
- **An RMD is the law's.** `aurea_core/rmd.py` holds the Uniform Lifetime Table with its source, the start age from the
  birth year and the value at the previous 31 December at that day's real price; what cannot be valued is "not
  valued", and an owner's IRAs are totalled. Roth conversion room is per return, in its tax year, and laid out.
- **The Roth window is each year's room, laid out.** `aurea_core/roth_window.py`, US only: from retirement to the year
  before an IRA owner's first RMD, in today's money, each year's income on file, Social Security taxed by §86, the law's
  deductions (the senior deduction only to 2028), the room to each bracket's top and to the next IRMAA line two years on,
  each worked as the conversion that reaches it. A year past the law's last table holds it in today's money and says so;
  a figure fixed in the statute shrinks. A filing status the law does not know, a missing birth date or no IRA is not
  assessed. The US withdrawal order is that room, and the usual order says why when there is none. It recommends
  nothing; ACA credits and state tax are named as not modelled. A firm-wide tax scan does not run it.
- **Discovery is what a person heard, and a plan's tier is what a person said.** `aurea_core/client_file.py` holds meeting
  notes (goals, money history, who else weighs in, whether an adult is engaged) as the person who held the meeting wrote
  them, named, added and never edited; the latest engagement note on an adult stands, none is "not recorded" and never
  "engaged", and the ledger keeps the note's kind and subject, not its words. `aurea_core/plan_triggers.py`: a life event
  or a recorded equity grant is a trigger (a grant raises one review task, as a life event does); the plan is touched by
  the next event on its record at or after the day the trigger was recorded, never one before it, and no target is
  supplied. A household's tier (a full rebuild or a review and refresh) is set by an adviser or paraplanner with a reason,
  added and never edited, and is never worked out from the household's size.
- **An employer benefit is what the plan document says, and moves no figure.** `aurea_core/employer_benefits.py`: a
  retirement match (rate, the share of pay it matches up to, vesting) or a deferred compensation election (a year's
  deferral, the age and form of payout), recorded from the document by an adviser or paraplanner; what a match could add
  is shown only with an income on file and is never assumed received, and a deferred payout counts as income only when
  recorded as an income stream. An income stream may end at an age (`end_age`); the engines stop paying it, Social
  Security has none. Umbrella and property cover are listed beside the rest and never counted against a gap; a rider is
  listed under its base policy.
- **The return the needs ask for sits beside the questionnaire, and changes nothing.** `aurea_core/return_need.py`: the
  one compound rate at which the managed book and the contributions recorded on the needs goals pay each goal's target at
  its date, pooled, beside what the allocation is expected to compound at on the firm's assumptions. With no tiers on file
  every goal counts and it says so; with no goal it is not assessed. `risk_profile.people_compared` sets two adults who
  answered for themselves side by side with how far apart they are; one alone is not compared. Neither moves a profile, a
  mandate or an IPS, and the IPS draft states them without resolving them.
- **Guardrail spending is a run beside fixed spending, and its terms are the client's.** `aurea_core/guardrails.py` and
  `retirement.simulate_guardrails`: a start rate, an upper and lower guardrail, a cut and a raise, and whether inflation is
  skipped after a down year, run on the plan's own random returns: the first draw, the likelihood, how often and how deep
  the cuts come. The four of upper, lower, cut and raise are never filled in; a start rate left out is the plan's own and
  says so; none given and none on the IPS is not assessed. An adviser records the terms on the IPS from the document as a
  new version that carries every other term over, and they carry to the next version. Nothing changes spending by itself.
- **Giving is laid out, and the client chooses.** `aurea_core/giving.py`, US only: an IRA owner may give from the IRA
  from the day they reach 70 and a half, up to the year's limit, and what it would satisfy of this year's RMD; stock held
  over a year in an account the household owns, largest gain first, capped at 30% of a recorded return's AGI; and a
  donor-advised fund bunching year against giving each year, with the 0.5% floor, the non-itemizer deduction a fund does
  not get and the 35% cap. The yearly giving is what the user says, never worked out; no return means no bunching. What
  the client decides goes on the advice file. A firm-wide tax scan does not run it.
- **Healthcare is what the firm states, plus what the law sets.** `aurea_core/healthcare.py`, US only: before 65 the
  firm's cost a person a year, from 65 its cost beyond Part B plus Part B (the law's, by year) and the IRMAA tier the
  income on file two years before sets, or a recorded return's AGI against the table as published. The costs are the
  assumption set's `healthcare.pre65` and `healthcare.medicare`, research's and the portfolio team's with a source, and
  have no default: unset, healthcare is not assessed and a retirement plan is exactly what it was. The plan funds it on
  top of the income. IRMAA stops where it would read an RMD year's income, which is not on file, and says so.
- **One goal, one likelihood.** `aurea_core/goal_tradeoff.likelihoods` is the only answer to whether a goal will be
  met: the Canvas, the goals page, the staff planning view and the agents read it. A goal's horizon is its date, else
  the horizon recorded on it, else assumed and named. A goal with a share of the wealth recorded is funded from that
  share; the rest share the pool in priority order. Career earnings come from a recorded income and date of birth,
  sit beside the goals and never fund them, because a salary is not savings. The retirement engine answers whether
  the income lasts, a different question, and names each stand-in it used in `assumed`.
- **A goal's tier is what a person said.** A goal is a need, a want or a wish only when someone recorded it
  (`update_goal`); with none on file it is "not tiered", never a need. A household's success is every needs goal met
  together (`goal_tradeoff`'s `tiers`); tiers change no goal's own likelihood and no funding order.
- **An adviser's own advice is on file, added and never edited.** `aurea_core/advice_file.py` keeps each recommendation
  with its facts, assumption version and the alternatives rejected; where it stands is a new outcome with its evidence.
  Only an adviser or paraplanner records; a client never reads the file, which reaches them through their adviser.
- **A plan of record is what people did with it.** `aurea_core/plan_record.py` works a household's plan state out from
  the evidence, never sets it. A baseline freezes the figures and the assumption version; a second person reviews it above
  the firm's complexity line (every plan, until a line is set); a delivery delivers that frozen baseline, with its
  evidence, and raises its tasks. Each is added and never edited, and nothing here changes a figure, goal or mandate.
- **Equity compensation is read from the plan statement.** `aurea_core/equity_comp.py` takes a grant as a person
  recorded it, works out what has vested from its schedule, and values it only on a real price. Grants sit beside net
  worth and are not added to it. Selling paths hold the price flat and stop at the gain: tax, AMT and withholding are
  named as not modelled. A concentration plan is what an adviser and client agreed, and it places no order.
- **The shelf says what may be bought, and a person says what is on it.** `aurea_core/shelf.py` holds the research
  team's decisions, added and never edited. Closed stops every purchase at once, and a gated product needs a
  certification from a document, never worked out from wealth. "Approved products only" is a separate switch a person
  throws, because a shelf is built one product at a time. The rebalancer and a direct order read the same `barred`, and it stops purchases only: it is not an exclusion, because the rebalancer divests what is excluded.
  Sales are never stopped. No decision on file does not mean never reviewed.
- **A product is offered only to clients it was made for, and an adviser decides every offer.**
  `aurea_core/product_distribution.py` holds a product's target market, read by the research team from its document,
  added and never edited; a complex or conflicted one is offered only after compliance signs it off, never the person
  who recorded it; a firm with no market offers nothing, and a fee-only Indian adviser nothing that pays a third party.
  `aurea_core/product_fit.py` reads each client from what is on file: a gate reuses the rule that decides it, a missing
  fact is "needs a fact first" and never a no, signals order who fits and never read a fee, and a household under
  monitoring is left off with no reason an adviser reads. The negative target market is barred through `shelf.barred`,
  so the rebalancer and a direct order refuse the same buys; outside it otherwise a direct order goes ahead, named.
  Product Match stops at a proposal whatever its tier, one list per adviser per product, approved only by the adviser it
  names or a branch leader (`decision_rights.DISTRIBUTION_AGENTS`, `named_decider`); approving gives tasks and pitches and
  contacts nobody. A message that names a product carries its document and, if conflicted, its disclosure.
- **A person says a client left or died, and the custodian's paper moves the money.** `aurea_core/exits.py` keeps the
  book as it stood on the date, at that day's real prices; what has none is listed with no value, never estimated. The
  hold sits on the mandate: a departing client may sell and not buy, and nothing trades on a dead client's accounts
  until an executor is on file. Nothing moves until operations records what a custodian moved, from its confirmation,
  and only then do holdings, lots and cash change. A named beneficiary is paid outside the estate, with no executor;
  an account with none goes through it. Basis steps up only where `markets.basis_on_death` says and never in a
  retirement account. Closing is refused while anything is held, and a household that has left leaves every scan.
- **Repapering takes the consent its change needs.** `aurea_core/repapering.py`: silence after the notice period is consent only for a change that allows it, and an objection or a returned notice keeps a household out. Following an adviser or a program conversion always needs a signature, whatever a firm sets. Every answer is a person's record of its evidence.
- **A principal decides a feature, and a hold protects a specified adult.** `aurea_core/account_features.py`: margin, options and checkwriting are asked for on a signed agreement and decided by compliance or a branch leader who did not ask; a retirement or custodial account takes no margin. A Rule 2165 hold is US only, is placed by compliance or a branch leader for someone 65 or older or recorded as impaired, stops new payments out, and is extended only as the rule allows.
- **Money comes in from a verified account, and a large deposit waits.** `aurea_core/money_in.py` takes an ACH only from a bank account verified from its evidence, and one in a name outside the household only after a callback. A deposit at or over the threshold is held, and `cash_movement.held_back` keeps purchases off it. A return carries its NACHA code and an unauthorized one revokes the link. Nothing here writes cash, and a shortfall from a return is shown, not solved.
- **Monitoring is never told to the client, and a person decides every alert.** `aurea_core/txn_monitoring.py` works alerts out from the money movements against the baseline recorded at onboarding; with no baseline those scenarios are not run, and it says so. A person closes or escalates each alert with a reason, once. The report clock is the market's and a market with none gets no clock. Only compliance records a report, with the filing's reference, and an exit goes through a forum of two or more. No client or adviser reads it, and a message to a client under investigation that mentions it is refused.
- **A transfer's file is what the documents say, and a rollover is laid out, not advised on.** `aurea_core/transfer_file.py` adds an entry per event, from its document, and moves no holdings or cash. Cost basis is due fifteen days after the assets arrive. A private fund interest moves by assignment and is re-registered only after the general partner consents; a capital call across the transfer stays open until someone says who funds it; a restricted legend comes off only on counsel's opinion. A rollover's points come from the plan statement as a person recorded it; a point with no fact is not assessed, the adviser's reason is theirs, and the PTE documenter drafts nothing until both are on file.
- **One document matrix, and a package goes out in good order.** `aurea_core/document_matrix.py` is the only matrix: the tracks, the activation gate and the NIGO agent read it. A custodian's forms are what a person recorded from its guide; none recorded adds none. Registration detail and documents received are recorded from the paperwork, never filled in, and an open account's registration changes through the custodian. The gate blocks what a custodian rejects; a tenancy is read against the state, and law that varies by state and is not encoded is recorded from the state's act.
- **A consent is what a person recorded from its evidence.** `aurea_core/consents.py` keeps every consent a client gave or withdrew, added and never edited; the latest stands. A disclosure sent by email or portal is not delivered without e-delivery consent, and a fee is not debited without its authority: both are gates before activation. A US account's engagement decides its disclosures, and a brochure or privacy notice falls due again a year after delivery. Not on file does not mean not given, and nothing here is backfilled.
- **A trade says what happened and when it settles.** `aurea_core/blotter.py` reads the orders and writes nothing. The book takes a trade on its trade date; settlement is worked from `markets.settle_days`, on weekdays, with holidays not modelled, and a firm with no market gets no date. Orders in one instrument on one side on one day across accounts show as a block, so a fair allocation can be seen. A direct order gives the person's own reason, and goes on the ledger, because it has no proposal to carry its record.
- **A tax summary is never the form.** `aurea_core/tax_documents.py` reads the book, on the market's own tax year, and says it is not the official document. A document is expected only where the book shows a reason, and a person records one as received, from having it. Not on file does not mean not issued. A firm with no market is told nothing is expected.
- **A client asks the conversation, with tools that read their own household.** `CLIENT_TOOLS` read only and take no household
  id: each reads the signed-in client's household (`aurea_core/client_view.py`), and the gateway gives a client its own prompt,
  which sends advice to the adviser. A client's thread names no subject. Their requests, messages, questionnaire and next-gen
  steps stay buttons on their pages. Staff take no next-gen step for a member and send no message from those pages; beside the
  conversation, recording a questionnaire for a client (adviser or paraplanner) goes through `record_questionnaire` and its Confirm card.
- **A client's language is what a person recorded, a translation is the person's words, and WhatsApp is a channel the client opted in to.**
  `aurea_core/client_language.py`, `whatsapp.py`, `india_stack.py` (B-301, B-302), Indian firms. A language comes from the client's own request,
  recorded by an adviser or paraplanner with its evidence, added and never edited; none on file means English, never that the client reads it.
  A message to a client carries two texts on its Confirm card, the exact words in their language and the English original, and the over-promising,
  advertising and tipping-off checks run on both. No translation service is connected: `translate` is a seam whose stub returns the English marked
  "translation not connected", so a message then goes in English and says so. The client prompt is unchanged in every language: advice goes to the adviser.
  WhatsApp needs a `whatsapp` consent in the registry from the client's opt-in, a connected provider and, outside the client's 24-hour window, an approved
  template that compliance recorded with its exact wording and the provider's approval reference; free text there is refused. A message is never called
  delivered without a status the channel reported (`MessageDelivery`, added and never edited). The WhatsApp channel and the DigiLocker and eSign provider are
  simulations (`app/simulations/whatsapp_channel`, `india_stack`): a DigiLocker document is an offer a person confirms into the KYC (mode `digilocker`),
  a signed envelope an offer a person confirms into the consent registry (`advisory_agreement`, e-signature), each behind a Confirm card; a whole Aadhaar
  number is cut to its last four or refused; the KRA and CKYC lookup is not connected (B-272). Nothing here says a firm's use of either complies with a rule.
- **A client reads what is on file about them.** Canvas shows a client their net worth and the IPS terms they accepted, and the assistant may state both, from `aurea_core/balance_sheet.py` and `ips.py`. Not who recorded it, and not what the monitor found, which goes through their adviser. A client says a figure is wrong and it becomes a task: nothing changes until a person records it from a document.
- **Reverse churning measures what a person recorded.** `agents/reverse_churning.py` counts a recorded review or a
  meeting held, never a task or a proposal, so its own proposal cannot reset its clock. A household with neither on
  record is not measured, because no record does not mean no review. It keeps one open flag per household, in the
  flag's attributes, until a person resolves it.
- **Tax Intelligence reads the household's own market.** NZ, US and UK each have their report and their findings; India
  has its path; a market with no report raises nothing. Every figure is an estimate for a qualified person to check.
- **A person decides a screening hit, and a second confirms a true match.** `aurea_core/aml_review.py` keeps decisions
  apart from the vendor's result, so a rescreen keeps them and a new hit is open. A party counts as screened only when
  every hit on it is decided: the screener running is not enough. Operations or compliance disposition a hit with a
  reason; a true match stands once a second person in compliance confirms it; QA samples one false positive in ten and
  an overturn reopens it. A confirmed PEP opens EDD, a confirmed sanctions match stops the case. An EDD file is signed
  off against the checklist for its wealth, a source for every item, by compliance when the risk is high. Nothing here
  is told to the client.
- **An onboarding account's state is worked out, never set.** `aurea_core/account_states.py` reads the evidence into ten
  states, from draft to invested, each with an owner and an SLA, and keeps exceptions in queues by kind (NIGO, KYC,
  custodian reject, transfer reject, funding fail). A household is its least advanced account. The paperwork, money
  and invested clocks are reported apart. Nothing is transferred into an account the custodian has not opened.
  A client starts as a case (`aurea_core/onboarding_start.py`), from the conversation or the page alike: a draft that
  records only what was said, one open case per name, an account type only in its own market, and the adviser who
  opened it as the client's adviser.
  An account is late only past its state's SLA: Abandonment Recovery reads the same board, names the state, owner, SLA
  and days over, and approving adds a task for the owner; it changes nothing on the account.
- **A client's AML risk is rated by a documented model, from facts a person records.** `aurea_core/aml_risk.py`
  scores geography, client type, wealth, confirmed screening matches, expected activity and channel into low, medium
  or high; the band sets due diligence, the approver and the review cycle. Missing facts are refused, not read as low.
  A designed path (PEP, a call-for-action country, non-resident, layered structure, crypto, cash business) sets a
  floor and says what more is needed. An override keeps its reason beside the model's band and never goes below a
  floor. Every rating stays on record. Identity is verified for every party the CIP rule reaches, a party is screened
  when it is recorded, and the daily screen covers clients already onboarded.
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
- **Residence is what a person recorded, and the days test is worked from it.** `aurea_core/residency.py`, India: a client's days
  in India for a tax year and the years before, their citizenship, Indian income and whether they are taxed elsewhere, read off
  a passport, visa or return and added, never edited (`residency_record`; a new record carries over what the last said). The
  Income-tax residence test is worked from those days alone, branch by branch (182 days, the 60-day test with 365 in four
  years, 182 or 120 days for a citizen abroad by Indian income, deemed residence, not ordinarily resident), each figure from
  `law_figures` with its source; a fact the answer turns on that nobody recorded is named and the status is "not assessed", and
  no record does not mean resident. FEMA status (NRI or OCI) is a different question with no days test and can differ. Beside
  them, laid out and never advised on: the NRE, NRO and FCNR accounts, the NRO remittance and portfolio investment limits, tax at
  source on a non-resident's gains with treaty relief not modelled, and the client's own FATCA and CRS self-certification and US
  indicia (an indicium is never read as a US person). A non-resident in an adviser's household needs that self-certification
  before advice (`pre_advice`), the Indian tax scan names a non-resident, and the statute and RBI text were not re-read: a
  qualified person checks each figure. An adviser, paraplanner or operations records, behind a Confirm card.
- **A minor's account is run by a guardian a person recorded, and the minor never trades.** `aurea_core/guardianship.py`
  (`nri_nre`, `nri_nro` and `minor_guardian` are registration types, India only, with their rows in `document_matrix`): a minor is
  worked from the date of birth on file and the Majority Act's age (21 where a court appointed the guardian), "not assessed"
  with no date of birth; a guardian is added and never edited; `order_guard` refuses an order on a minor's mandate until one is
  on file; majority flags the account and converts nothing.
- **A PMS client is measured against what its agreement states and what the rules cap.** `aurea_core/pms.py`, for a firm
  holding a Portfolio Manager licence: the agreement a person recorded from the signed document (strategy, discretionary,
  non-discretionary or advisory, opening corpus, segregation, benchmarks, fee terms; added, never edited) is checked against the
  Rs 50 lakh minimum, its track, three benchmarks at most, operating expenses at most 0.50% of average daily AUM, a performance
  fee on the client's own high-water mark and the exit load caps, each from `law_figures`. A check with no fact is "not
  assessed" and holds nothing; only a recorded breach holds an approval (`pms.refusal`, beside `pre_advice.refusal`). The
  billing preview carries the agreement and a performance fee estimate on securities-only, gross, monthly-linked returns, shown
  and never added to the bill; the quarter's report is a draft for the manager and is not publishable, because a return is stated
  to a client only on APMI's TWRR or a verified figure. Recording is the portfolio team's, operations' or an adviser's.
- **Money held elsewhere comes through a consent a person recorded, or a statement a person supplied.** `aurea_core/account_aggregator.py`,
  India: an Account Aggregator consent artefact (its purpose, the kinds of information, the data range, when it lapses) is recorded from
  the artefact as filed, added once and ended once, never edited; a client makes no change here, staff record a withdrawal from its
  evidence. `permitted` and `permits` are the one gate any source reads: nothing is fetched under a consent that is lapsed, withdrawn or
  not started, nor outside its kinds, its person or its dates, and what a source delivered earlier ends, kept on file, at the next pull
  (deleting it is the firm's retention decision). A source registers at `held_away_feeds.register` with `account_aggregator = True`;
  none is real yet, so `app/simulations/aa_feed` stands in (opt-in per firm, tagged `simulated:aa_feed`, a chip on every card, its
  consents and lines discarded together). An e-CAS (CAMS or KFintech, NSDL, CDSL) is read by a person from the document into lines
  (account, ISIN, name, units, value); a bad ISIN or a value that is not a number is refused and listed, an asset class is never guessed
  from an ISIN, an ISIN resolves only to the firm's own instrument, no PAN is asked for or kept, a line the aggregator already delivers is
  left to it and a newer statement replaces what it supersedes. Both end as held-away lines, so most-detailed-wins, counted once in net
  worth and performance on real prices only all hold. It is not a statement that the firm's data handling is compliant.
- **A complaint is a record, and what was done about it is the next.** `aurea_core/complaints.py`, India: the register is a complaint added
  once as received (the firm's reference, a SCORES number where it came that way, source, category, the complainant's words as data) and
  dated steps added after (acknowledged, Action Taken Report, resolved with an outcome, taken to SCORES or ODR, withdrawn), never edited.
  Its state is worked from the steps and the firm's day against the 21-day limit in `law_figures`; the acknowledgement period was not found
  in the sources read, so the firm's own (`settings["complaints"]["ack_days"]`) is used, else a figure marked assumed that says so. Anyone
  who looks after clients records a complaint; only compliance or a branch leader resolves, withdraws or escalates one (the adviser it may
  be about does not close it); the register is read by compliance, a branch leader, operations and an administrator. A complaint about advice
  is a task for compliance the day it is recorded and closes with the complaint. The calendar's monthly complaint data carries the
  register's Annexure C counts; no complaint on file reads "no register", which does not mean none was made. It files nothing and does not say
  the firm handles complaints in good order.
- **Client-facing words are read, never approved.** `aurea_core/advertising.py`, India: `check(text, kind)` reads a message, a report or
  marketing against encoded patterns (a promise of return or safety, an unverified stated return, a superlative, the missing disclaimers and
  registration number in marketing, and, from 8 April 2027 when the Common Advertisement Code is operative, testimonials and pressure) through
  the over-promising rule's denial-aware reading (`compliance.rules.asserted`), each finding naming its rule and source. It says its
  patterns are not exhaustive and never calls a text compliant. A message to a client of an Indian firm that promises a return is refused
  before its Confirm card and again at Confirm, on the conversation and on Canvas; every other finding is shown on the card for the person to
  weigh, and a stated return is flagged because only a PaRRVA-verified or APMI TWRR figure may be claimed and nothing here can verify one.
- **The Indian tax engine reads what a person recorded, per taxpayer, and says it is an estimate.** `aurea_core/tax_india_full.py`
  beside `tax_india.py` (which still works each taxpayer's gains first in first out and nets nothing across a family): every rate,
  exemption, slab, surcharge band, cess and holding period is one `law_figures` row with its source and day, and the ones the research did
  not state (the old regime's standard deduction, the advance-tax floor) say they are assumed. Asset classes carry their own holding
  period (12 months listed, 24 for unlisted and foreign shares, gold, land; a specified fund, a market-linked debenture and unlisted debt
  never). Capital losses are carried forward only from a return a person recorded (`TaxLossEntry`, added never edited, the latest per
  year and kind stands): eight tax years, none from a return filed after its due day or not on file, and the ledger, where there is
  one, replaces the profile's single figure so nothing counts twice. Grandfathering to 31 Jan 2018 (higher of cost and the lower of
  that day's value and the sale price) applies only to an instrument whose value a person recorded (`GrandfatherValue`); without it the
  cost stands and the lot says what it needs. The new regime against the old is worked only where income is recorded, and without the
  old regime's deductions on file it names the deductions that would make it win and does not pick. Advance tax is the four
  instalments of the year's tax; what was paid is not on file, so a date that has passed reads "passed, payment not recorded", never
  overdue. A harvest is flagged, never cleared: it says a long-term loss can use gains the exemption covers, that a rebuy is a review
  item and a gain harvest raises the GAAR question; `harvest_guard.py` is unchanged, India has no wash-sale rule, and nothing says a
  rebuy is safe. Not modelled and named: marginal relief, the rebate on gains, basic exemption shortfall, TDS, dividends and interest,
  and the enacted Finance Act 2026 and Rules, which were not read. Recording is an adviser's, paraplanner's, operations' or the
  portfolio team's, behind a Confirm card.
- **KYC in India is what a person recorded from the document, and its state is worked out.** `aurea_core/kyc_india.py`: the CKYC
  number, the KRA and its status (validated, registered, on hold, rejected), the day and mode of verification, the Aadhaar's last four
  only (a 12-digit number is refused everywhere, even in a note, because an intermediary may not keep it), PAN-Aadhaar linkage, the
  proofs and when they expire, political exposure, and for an entity its structure and owners; added never edited, the latest per party
  stands. The state is never set: not on file (which is not none), blocked (On Hold or Rejected at the KRA), overdue (the re-KYC date
  for the party's band has passed, or a proof has expired), due (inside 60 days, Registered only, a refresh trigger since, or a fact short
  of the circular), verified, or not assessed (no date, or no band for the clock). The refresh period is the firm's
  (`settings["kyc_review_months"]`); SEBI sets no limit, so the default of 2, 8 and 10 years for high, medium and low is the RBI's for
  banks, borrowed and named as assumed. The band is the record's, else the account's latest AML rating (`aml_risk`), else none and
  never low; a politically exposed person is never below high. A beneficial owner is more than 10% of a company or partnership, more than
  15% of an unincorporated body, and in a trust a beneficiary with 10% or more and always the author, trustee and protector, listed
  apart from CRS controlling persons (more than 25% of a company). An overdue, blocked or due KYC is a line in the onboarding board's
  KYC queue and the `kyc_current` activation gate (fail for overdue or blocked, warn for due or none on file); nothing stops an order.
  Real KRA and CKYC lookups are not connected, and this is not a statement that the firm's KYC is compliant. Recording is an adviser's,
  paraplanner's, operations' or compliance's.
- **An Indian date raises one task, a lapsed KYC holds the book, and a person confirms what a case or an estimate says.** `aurea_core/india_reminders.py`: a filing, the registration's expiry or a policy review (compliance's), and a client's periodic KYC review, a KYC that is overdue or blocked and a minor now an adult (the household's recorded adviser, else unassigned and saying so) become one task each when overdue or within 14 days, found again by title whatever its state (a task a person closed is never raised twice), by the worker every six hours or the `raise_india_reminders` card; advance tax and the return are not raised because whether they apply is not assessed, and no record changes. `kyc_india.household_refusal` holds a household's book while any party's KYC is overdue or blocked, in `order_guard.review` and `runtime.decide` beside `pre_advice`, naming the KYC and who records it; due or not on file only warns, and dismissing stays open. A PMS performance fee reaches a bill only as its own line, after `confirm_pms_fee_line` (`pms.record_fee_line`, one per client and period, never edited): the invoice's amount, and so the fee cap, stays the fixed fee, and nothing debits the line. Residence and a guardian an onboarding case holds are read by `case_facts.py` and recorded only behind `record_case_facts`'s card, naming no FEMA status or day count a registration does not state. A minor with a guardian who has reached majority is "now an adult: convert": a task, the guardian's authority ended and orders refused until a person records the conversion from the document (`guardianship.record_conversion`); nothing converts by itself. A segment's minimum is in `FirmSegment.currency` (empty reads USD, which the column always held), the firm's own money for any a person states now.
- **India gates by ticket size, an accredited investor is a certificate, and the adviser's fee terms are documents.**
  `aurea_core/product_gates.py`: SIF (Rs 10 lakh), PMS (Rs 50 lakh) and AIF Category I, II and III (Rs 1 crore) are `law_figures` rows that start
  "not reviewed" in the counsel tracker; the research team gives a product its category on its shelf decision, and `product_fit` and the direct-order
  guard read it against the household's recorded investable assets (managed book, held away, recorded cash; never estimated). No asset figure is "needs a
  fact first", never a no; a sale is never stopped; a category that opens is never a recommendation. `aurea_core/accredited.py`: accredited-investor
  status is what a person recorded from the certificate (added never edited; a lapsed or withdrawn one reads not accredited; the criteria are laid out
  with the caution to confirm them and never worked out from wealth); it takes an individual's own mandates outside the fee cap and waives the SIF
  minimum, and nothing else. `aurea_core/adviser_fees.py`: advance collection (an invoice period past a year, or the months the agreement records, is
  held), the mode elected and each switch (a switch within the year lifts the cap to the higher of the two), a second-opinion fee within 2.5% that counts
  in what the family is charged, and the IAASB deposit with where it is held (on the registration record), each from its document; none says the adviser
  complies. `aurea_core/rate_card.py`: every Indian figure the Budget or a SEBI circular sets, with its last counsel review; the worker raises one task for
  compliance each February (once per year), and compliance may mark a Budget figure awaiting the Budget (`counsel_review.record_watch`) until a newer
  value is recorded in code. It changes no figure.
- **A SIP is the client's standing instruction, and the licence picks the plan.** `aurea_core/sip.py` records a SIP from
  the client's signed registration (simulated only on a demonstration firm), and the worker buys each instalment once a
  month at the real NAV if the cash left after holds covers it; a missed one is recorded, not retried. A fund's NAV is
  AMFI's, by scheme code, and is never invented. `shelf.plan_bars` bars a regular plan under an adviser or a portfolio
  manager. A PAN is recorded by a person from a document and shown masked everywhere (`aurea_core/pan.py`); it is not
  encrypted, and one typed into the conversation reaches the model.
- **A remittance abroad is what the bank's advice says, and a pledged unit is not the account's to sell.** `aurea_core/lrs.py`: a person
  records each remittance under the Liberalised Remittance Scheme from the bank's advice (USD counted, rupees and tax collected only if it
  shows them), added and never edited, a correction a reversal. The year's headroom against USD 250,000 and the rupees toward the tax-at-source
  threshold are laid out as figures; none on file is "not assessed", never nothing remitted, and no remittance is estimated. A direct order for
  an overseas product (an instrument priced outside rupees, or tagged `lrs`) is refused over the headroom naming the figures, else shown with the
  tax as an estimate (`order_guard`); GIFT City funds and overseas mutual fund schemes' limits are named as not modelled. `aurea_core/pledges.py`:
  units a lender holds a lien on, recorded from its confirmation (a release a second row), are excluded from a direct sale (`order_guard`), from
  the rebalancer's lots and from settlement, shown as pledged, and still counted in what the client owns; the loan is not recorded there.
- **A SIP's debit rests on a mandate a person recorded.** `aurea_core/sip_mandates.py`: a NACH or UPI Autopay mandate from its registration
  (reference, per-debit limit, start, expiry, status) on a bank account `money_in` verified, added and never edited, a cancellation a second row.
  A lapsed, paused, cancelled or rejected mandate, or an instalment above its limit, is the reason `sip.run_due` gives for a missed instalment;
  a SIP with none on file reads "mandate not on file", is warned and is still tried. Nothing registers a mandate or presents a debit.
- **The Indian shelf's other classes are held-away lines with their rule beside them, and tax habits are laid out, never advised.**
  `aurea_core/india_savings.py` (PPF, Sukanya, SCSS, NSC, NPS, EPF, G-secs, listed bonds, MLDs, REITs and InvITs, Sovereign Gold Bonds, ULIPs):
  the rate is the statement's and an administered rate is never assumed, an NPS exit split is the rule on the corpus as stated, and net worth
  counts the line once. `aurea_core/tax_habits.py`: gain harvesting is `tax_india`'s first-in first-out lots against the Rs 1.25 lakh exemption
  (`harvest_guard` unchanged, no rebuy is said to be safe); dividends on file by payer with tax deducted above the threshold and the tax at the
  recorded slab; a gift between people in the household with the relationship a person states, never inferred (a date of birth that contradicts
  it refuses), exempt from a specified relative, and income from a gifted asset added back to a spouse or a minor child's giver. Every figure is a
  `law_figures` row with its source and starts unreviewed in the counsel tracker.
- **A market's planning figures are suggestions with their source, and cover says who is paid.** `aurea_core/market_assumptions.py`
  holds a market's suggested planning figures, dated and sourced and never a regulatory value; `planning.assumption_set`
  takes the firm's own first, then the market's, then the platform's, and says which. Accident-only cover and critical
  illness are listed and never counted against a gap, and a policy says who is paid: in India a parent, spouse or child
  nominee on a life policy is beneficially entitled (s.39(7)), any other holds for the legal heirs, and an MWP policy is
  outside the estate. A demat takes three nominees, and a nominee the will does not name is flagged, never changed
  (`aurea_core/nominations.py`). Gratuity is recorded from a statement and never estimated (`india_estate.py`).
- **An Indian adviser advises nobody before the paperwork, bills within the cap, and files on time.** `aurea_core/pre_advice.py`:
  a household on the adviser's track needs the signed agreement with the MITC, the AI use disclosure, the investor charter,
  a complete risk profile consented by registered email or a signed document after the answers, and the non-SEBI
  declaration where something outside SEBI is on file, each from an onboarding case or the consent registry's India kinds.
  Until then `runtime.decide` refuses a book-moving approval and the billing run holds the fee. `fee_cap.py` holds a
  family's year to Rs 1,51,000 or 2.5% of its securities (`law_figures`), and lowers no fee: the bill is held. A family
  is SEBI's family of client as a person recorded it (`sebi_family.py`); the household stands in where none is, and says so. `compliance_calendar.py` works each licence's filings from its
  circular, names a day it assumes, and asks nothing from before the firm came on; compliance records a filing with its
  reference, the admin the registered officers with a reason. None of it says the firm is compliant.
- **SEBI's family of client is what a person recorded, and an Indian estate is what its documents say.** `aurea_core/sebi_family.py`:
  the individual, spouse, dependent children and dependent parents (the research report's reading, counsel to confirm), from rows
  that place one client in another's family with whether a child or parent is dependent, from a declaration, added and never
  edited. Dependence is never worked out from an age or an income. With nothing recorded the household stands in and the view
  says so; anyone a row says is not in the family stands alone, and a row can join people across households. A family that is
  larger than the truth only makes the fee cap stricter. The fee cap's yearly total and the pre-advice gate read it; an
  accredited investor is not recorded, so none is exempted. `aurea_core/india_estate.py` keeps, from documents, a bank account's
  four nominees, EPF's family-only nominees and a signed opt-out for a demat account or a folio (read by `nominations.check`
  against the caps, the shares, who has died and the will; flags only, no account changes), a will's registration, probate and
  executor and the personal law succession follows (never inferred from a name), an HUF's karta and coparceners, and a
  gratuity statement. Intestacy is laid out from `law_figures` for the recorded law, "for counsel", no share worked out; Muslim
  succession is named as not laid out; with no law recorded it is not assessed. The client file reads the records (an opt-out is
  no longer an open nomination); the plan names a gratuity beside the pot and never adds it. Staff only: no client route or tool.
- **Governance is compliance's record, India's dates are the countdown's, and a law figure carries what counsel said.**
  `aurea_core/governance.py`: grievance, cyber, business continuity, outsourcing and conflict-of-interest policies recorded by
  compliance from the approved document (version, owner, approval, next review a year on unless the document sets one), added
  and never edited; overdue shows, not recorded does not mean none. The licence map reads `licences.py` out: held and not-held
  licences, what each track allows, and the agents the licences cap (configured beside effective), never a tier of its own.
  `aurea_core/counsel_review.py`: each `in_` law figure reads "not reviewed" until compliance records counsel's opinion (reviewer,
  day, confirmed, corrected or not confirmed); "changed since" when the figure was re-read after; the figure itself is a code
  change. `annotate` puts it beside every Indian figure in the Licences payload, the countdown and every tool result.
  `aurea_core/india_deadlines.py` puts India in the Regulatory Countdown: the firm's filings, registration expiry, policy reviews
  and rule changes, and a household's advance tax, return, KYC review from its risk band, a minor's majority and open nominations
  (no day invented), each with its source and "assumed" where it is. The agent still proposes on the US and UK only. None of it
  says the firm is compliant.
- **Whose client a household is, is what a person recorded.** `aurea_core/advisers.py` is the one reading of the
  "adviser" edges, the household's own outranking one to a member; nothing works out an adviser from activity, and
  unassigned says so. `assign_adviser` changes it with a reason, replacing every earlier edge, on the audit log. An
  adviser's cockpit counts their own households only; other roles see the firm's. Hours reclaimed count decided
  proposals, never runs. Supervision proposals are compliance's and the branch leader's (`decision_rights.SUPERVISION_AGENTS`).
- **A guardrail is a control only if something enforces it.** `aurea_core/autonomy.py` sets an agent's tier per mandate
  type with a reason on the audit log, and a licence caps it. A Tier 3 agent acts alone only inside `policy.GUARDRAILS`:
  `held_by_guardrails` holds a proposal for a person in both run paths. A stored key nothing reads is shown as not
  enforced, and client consent is the licence's, never a guardrail. A run a person starts of a book-moving agent still
  stops at a proposal.
- **A rule and an agent control are their owner's, on record.** `aurea_core/rulebook.py`: whether a rule is checked and
  its severity are compliance's alone; governance controls are compliance's and platform controls an administrator's.
  Every change, from a tool or a page, carries a reason on the audit log. A refusal names the owner and offers no way
  round it. New rules are code, because each carries its law.
- **Who changes the platform.** `require_roles` admits a firm's admin to every check and the platform owner to none:
  it signs in to an arbitrary firm and works from `/superadmin`, and `STAFF_ROLES` leaves it out. Users, roles, an
  agent's tier, pause and schedule, and the firm's settings are the admin's alone (`api/admin.AdminOnly`); compliance reads them and keeps the
  regulatory rules and the governance settings (rulebook). Nobody grants the superadmin role or changes their own role. An agent's tier, pause and schedule go
  through `aurea_core/agent_controls.py`: a tier within the licence, no resuming an agent conduct surveillance paused while its
  flag is open, a valid cron, and each change an `AutonomyChange` made by a person. A model portfolio is the portfolio
  team's and research's, and one that mandates follow is not deleted; a connector is operations'. A firm switched off
  signs its people out. A reset or invite link reaches an admin and never a conversation tool. Every setting the Admin
  and Connectors pages change is a tool behind a Confirm card, sharing `aurea_core/platform_admin.py` with its route; a
  secret (an LLM key, a connector's credentials), the CSV import and the firm's jurisdiction and currency stay on the
  page. Alerts go only to the firm's people, and only published research grounds an agent. An address that sends or
  shows something outside (a webhook, a logo) is https on the firm's own `allowed_domains`, and none listed refuses
  all (`platform_admin.check_outbound_url`). An admin sets a mandate's type and whether it is active; its risk,
  horizon and equity are the adviser's (`decide_risk_profile`, `record_ips`).
- **A prospect becomes a client through onboarding, and the funnel reads both.** `aurea_core/pipeline.py`: a deal is won only when its onboarding case opens (`start_onboarding` with its `opportunity_id`), activation names the household on the deal and a reversal takes it back. A stage moves with its reason on the deal's activity, lost needs one, and a probability nobody gave is none. A deal is quiet past its stage's target with nothing logged; the follow-up agent gives the owner a task and moves nothing. An adviser reads their own contacts' deals. A prospect's brief is what was recorded and advises nothing about their money.
- **A way in is what a person recorded.** `aurea_core/introductions.py`: who knows whom is a link a person records with
  its evidence, never worked out from a contact's notes or activity. Paths read those links and what the client graph
  and the CRM hold as fact (a family, a household, a recorded adviser, a touch someone logged), score them, and name who
  should ask; they introduce nobody. The Warm Introduction agent's approval is a task for the asker: it contacts nobody
  and logs nothing on the prospect.
- **A brief is laid out for its RM, and its facts are everyone's.** `aurea_core/brief_profile.py`: an RM keeps their own
  profile (what leads, how much opens, their strengths), and shaping changes order and emphasis, never a fact. A meeting
  brief is shaped for the household's recorded adviser. What an RM opens or closes is offered back as a suggestion and
  changes nothing until they accept it. The brief's history is dated and names where each item is recorded; a client's
  message appears as who and when, never its words in a prompt; there is no complaints register here, and it says so.
- **A persona needs a lawful basis, and quotes what it rests on.** `aurea_core/persona.py`: off for every firm until
  compliance records the basis in the privacy notice's words and switches it on (`persona_basis`, `persona_profiling`
  in `rulebook.SETTINGS`, with a reason on the audit log); a refusal names compliance. Articles come only from a source
  registered at `news_sources.register`, never scraped. Each talking point cites its article, quotes words checked to
  be in it, and expires 180 days after it; nothing on health, religion, politics, sex life, ethnicity, union membership
  or criminal matters, filtered before and after any model and left out as a count; adverse media stays with screening.
  Never shown to a client. The network map (`network_map.py`) reads the ways-in graph two links out and tells a recorded
  link from a fact; an RM's own LinkedIn export (`linkedin_import.py`) adds "knows" links to people already on file, and
  a row matching nobody is counted, never made a contact.
- **Firm scope.** `household_brain` and every `for_household` take a required keyword-only `firm_id`. Never
  make it optional. Agents pass `ctx.firm.id`; routes pass `firm.id`.
- **A legal figure carries its law.** `aurea_core/law_figures.py` holds each exemption, allowance and rate with its
  source, as-of date and window; one past its window reads expired, never a countdown. A figure the code would assume
  is named as assumed or not used. Check a new one against its source before a client relies on it. A figure set
  year by year (the US exclusions and brackets) keeps a version per year and is read with the firm's today.
- **Today is the firm's day.** A date a person reads (an age, a tax year, a due date, a settlement) is
  `firm_time.today(firm)` or `today_of(session, firm_id)`; `utcnow()` is for stored instants only.
- **An outside agent calls Astra as one person, and a change waits for them.** `aurea_core/outside_agents.py`: `/api/mcp` is an
  MCP server, off until the administrator switches `outside_agents` on with a reason. A key is made by staff for themselves,
  shown once, kept as a hash, and opens only that endpoint; a person's sign-in does not open it. The tools are the conversation's
  for the person's role, less `open_view`; a state-changing one is put as a Confirm card to that person on the key's own
  conversation and carried out once only when they confirm in Astra. The key is checked on every call, revoked by its person or
  the administrator with a reason, and every call is on the audit log without its inputs. A client makes no key.
- **The Foundation is native until an administrator says otherwise.** `app/foundation/`: each domain (parties,
  accounts, positions, activity, prices, crm) is owned by native (Astra's own tables, the default, which syncs nothing)
  or an adapter, in `firm.settings["foundation"]["sources"]`, set only by the admin with a reason
  (`platform_admin.set_foundation_owner`). An owned domain comes in through its connector's sync by (source,
  external_id) into the same tables, so every reader is unchanged; a record changed here and at the source is a break
  with its evidence, never overwritten in either direction. Astra's writes in an owned domain leave as a command (a CRM
  activity) and appear when the sync brings them back, or are refused naming the owner. What an owner cannot supply
  (tax lots) reads not assessed. Conformance is `tests.verify_foundation_contract`.
- **An agent remembers what a person told it, and nothing else.** `aurea_core/decision_memory.py`: a proposal carries
  the last decision on the same finding with its reason; a hold or a note is made by a person who may decide that
  agent's proposals, in their words, from a proposal, on the audit log, a hold for a year at most. A run says what a
  hold kept back. A control's finding (supervision, IPS, reconciliation, billing, book integration, screening) is
  never held. Nothing here widens autonomy, and no model writes a preference.
- **A simulation can be thrown away.** `app/simulations/<key>/` stands in for a system we do not have yet (a wealth
  platform, an aggregation feed, a news service, a LinkedIn export); the feature it feeds is real and reads only a
  seam that the real source will use too. Nothing outside `app/simulations` imports it; `install_all` runs only when
  `AUREA_SIMULATIONS` is on; it acts only for a firm that opted in; every row it writes is tagged `simulated:<key>`
  and nothing untagged, no audit or ledger entry; what it supplies carries `simulated: true` and shows a chip.
  `python -m app.simulations discard` removes it all, and `tests.verify_simulations` proves a discard leaves every
  table as it was (`docs/03_Architecture/simulations.md`).
- **An order on a book a product owns goes to that product.** `foundation/orders.py`: with the positions owned by an
  adapter that takes `orders.place` (the wealth platform through MCP, `foundation/adapters/mcp.py`, profile in
  `docs/03_Architecture/foundation-mcp-profile.md`), `venue_for_firm` is that product. An order leaves as a command and
  waits as placed; holdings, cash and the trade change only when the sync brings the product's trade back
  (`lineage.order_ref`), which records the fill as booked, never through `orders.settle`. A product that takes no
  orders, or does not own the activity, refuses naming itself. The guards run unchanged; on a book with no lots a sale's
  gain reads not assessed. What an MCP server declares it supplies is recorded in `settings["foundation_declared"]`,
  never assumed. A firm whose positions are native keeps the paper venue exactly.
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
  `verify_model_instruments`, `verify_capacity`, `verify_schedules`, `verify_proposals`, `verify_price_freshness`, `verify_reconciliation`, `verify_reconciliation_agent`, `verify_wash_sale_guard`, `verify_tasks`, `verify_reports`, `verify_messages`, `verify_protection`, `verify_protection_agent`, `verify_supervision`, `verify_risk_profile`, `verify_client_reviews`, `verify_ips`, `verify_direct_orders`, `verify_exits`, `verify_shelf`, `verify_equity_comp`, `verify_financial_plan`, `verify_client_file`, `verify_cash_movement`, `verify_trades`, `verify_ips_limits`, `verify_agent_decisions`, `verify_aml_reviews`, `verify_onboarding_states`, `verify_aml_risk`, `verify_consents`, `verify_document_matrix`, `verify_transfer_files`, `verify_transaction_monitoring`, `verify_money_in`, `verify_account_features`, `verify_onboarding_scoreboard`, `verify_repapering`, `verify_advisers`, `verify_stalled_onboarding`, `verify_demo_cards`, `verify_rulebook`, `verify_start_onboarding`, `verify_overpromise`, `verify_admin_controls`, `verify_admin_tools`, `verify_client_search`, `verify_confirm_cards`, `verify_balance_sheet`, `verify_markets`, `verify_india_tax`, `verify_licences`, `verify_india_demo`, `verify_book_screen`, `verify_studio_views`, `verify_client_canvas`, `verify_goal_likelihood`, `verify_firm_dates`, `verify_book_scan_route`, `verify_fees`, `verify_wash_sale`, `verify_ledger`, `verify_pipeline_funnel`, `verify_approval_tasks`, `verify_skill_reruns`, `verify_lapsed_confirmations`, `verify_platform_secrets`, `verify_introductions`, `verify_meeting_briefs`, `verify_foundation_contract`, `verify_foundation_dynamics`, `verify_persona`, `verify_network_map`, `verify_held_away`, `verify_simulations`, `verify_decision_memory`, `verify_ops_skills`, `verify_foundation_mcp`, `verify_sim_wealth_platform`, `verify_rmd_roth`, `verify_planning_bugs`, `verify_model_inventory`, `verify_planning_inputs`, `verify_estate_care`, `verify_outside_agents`, `verify_product_distribution`, `verify_agent_subjects`, `verify_assumption_set`, `verify_funded_ratio`, `verify_plan_scenarios`, `verify_goal_tiers`, `verify_advice_file`, `verify_plan_record`, `verify_plan_summary`, `verify_tax_returns`, `verify_social_security`, `verify_roth_window`, `verify_healthcare`, `verify_giving`, `verify_discovery_notes`, `verify_plan_triggers`, `verify_employer_benefits`, `verify_return_need`, `verify_guardrails`, `verify_india_nri`, `verify_india_pms`, `verify_india_aa`, `verify_india_complaints`, `verify_india_family`, `verify_india_governance`, `verify_india_tax_full`, `verify_india_kyc`, `verify_india_polish`, `verify_india_l100a`, `verify_india_batch_b`, `verify_india_batch_a`. From the repo root:
  `node e2e/check-testids.mjs`.
- **Seed changes need a backfill.** `seed/run.py` skips a firm that already exists, so changing what it
  writes does nothing to a deployed database. Ship a `seed/<name>_backfill.py` alongside, and say so.
- **`create_all` adds tables, never columns.** A new column on an existing table needs an explicit
  `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in `app/db_bootstrap.py`.
- **Agent context** is `ctx.session` and `ctx.firm` — there is no `ctx.db` or `ctx.firm_id`. A wrong
  attribute raises only when the agent runs, and scheduled runs log the failure and move on.
- **Frontend typecheck** is clean (the two admin data-quality errors went with B-96). Add none.
- **Two dev servers on one checkout** overwrite each other's `.next` and start answering 404. Give each its own
  `NEXT_DIST_DIR` (for example `.next-3011`) in `.claude/launch.json`; builds and deploys still use `.next`.

## Azure deployment

`az` is installed and signed in on the user's laptop, so on their go-ahead the deploy can be run from here. Deploy
a clean copy (`git worktree add --detach <dir> origin/main`), never the working checkout, because `az acr build`
uploads the folder it runs in. Windows `az.cmd` starts Python isolated and crashes printing the build log
(`charmap`), so put first on PATH a wrapper `az` that runs `"<CLI2>/python.exe" -X utf8 -Bm azure.cli "$@"`. A
command inside a container runs as `az containerapp exec ... --command "python -m seed.<name>" < /dev/null`.

Commands handed to the user are for a **brand-new** Cloud Shell, every time, starting with the subscription:

```bash
az account set --subscription b017b8b9-0911-43e7-96e9-4e38dd4c06f7
az account show --output table
[ -d ~/aurea ] || git clone https://github.com/swarupd227/Aurea.git ~/aurea
cd ~/aurea && git pull && bash scripts/deploy.sh
```

`scripts/deploy.sh backend` deploys backend and worker (they share an image); `frontend` deploys the
frontend; no argument deploys both. Commands run inside a container start from
`az containerapp exec --name aurea-backend --resource-group aurea-rg --command "/bin/bash"`.
