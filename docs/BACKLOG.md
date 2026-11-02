# Backlog

The one list of what is left to do on Astra for Wealth. If it is not here, it is not tracked.

- **Start here.** Any plan or "what next" answer starts from this file, not from memory, chat or an old report.
- **Keep it true.** Finding, deferring or finishing something updates this file in the same commit.
- **Check before stating.** A status comes from the code or the live site, with the date it was checked.
- **IDs never change or get reused.** Work moves between sections; it does not get renumbered.
- **Sources.** Requirements come from the Wealth Management L100 Foundation Guide (section numbers below are
  the guide's own) and the L200 modules. Neither is in this repo. A code comment citing "L100 §9" for
  billing or performance means guide §5.9.

Status last checked against the code: **2026-10-31**, by keyword search and reading, not by running each feature.

## In progress

The rest of L100, built and verified locally for one cloud build, the user's choice on 2 Nov because deploying
each feature was costing them too much time. Every §5 and §6.1 gap from the audit is built and waits on that build.
Parked stays parked, and "Waiting on a decision" needs a figure or a yes from the user.

**The one cloud build must:** deploy both sides, which runs `app.db_bootstrap` for the new columns, then run
`seed.schedule_backfill` and `seed.ai_governance_backfill`. After it, one live pass over the batch, Not now on every
card, moves these rows to Done.

## Next, in order

Nothing from the 2 Nov audit of the guide against the code is left to build, apart from B-91, which is a decision to
leave. The guide is `Wealth_Management_L100_Foundation_Guide.docx`, with L200-1 and L200-2 beside it, in the user's
Downloads.

## Smaller items

| ID | Item | L100 | Where it stands |
|---|---|---|---|
| B-14 | Binary document storage | - | Text only |
| B-15 | Book scan covers every scheduled agent | - | `POST /api/studio/scan` runs three agents |
| B-16 | Household currency | - | Not stored; protection shows UK and US figures as NZD with no conversion |
| B-18 | Wash-sale calendar for non-US households | - | The tool still reports for NZ households, though the rule binds US ones only |
| B-19 | Double-count guard for pensions | - | A recorded pension now replaces the pots on a person's profile, but accounts have no wrapper type, so a pension both booked and recorded still counts twice |
| B-91 | Goal projections read debts and spending | §5.3 | Retirement reads what is recorded (B-68). Other goals' projections do not, because which debt comes out of which goal is a planning choice, and any rule would move every client's on-track figure. Left as it is on 2 Nov. No live household has a debt on file yet |
| B-48 | Over-promising rule reads a denial as a promise | - | `no_overpromise` matches words, so "does not mean a rebuy carries no risk" and "not a guarantee of outcome" fail it. Two Indian tax memos show this live. Every firm shares the rule and over-flagging is the safe direction, so the user chose on 31 Oct to leave it |
| B-49 | Approve shown to a role that cannot approve | - | `RecommendationCard` shows Approve to everyone. The server refuses compliance on a proposal that moves a book, so the control holds, but the screen should read `decision_rights` |
| B-51 | A lapsed Confirm card still offers Confirm | - | A pending action expires after 24 hours, but only when someone clicks. Until then the card shows live buttons; the server refuses the click and only then does it read Expired |
| B-54 | Approved outreach under the right adviser's name | - | `client_care.act` signs with the first adviser found at the firm, not that client's adviser. Only matters with more than one adviser |
| B-55 | A report's age at the moment of approval | - | Approving shows the period but no warning. The live Morrison draft is dated 13 Aug, so its figures were 11 weeks old on 31 Oct; a redraft refreshes the same draft |
| B-73 | "Today" taken from the server's date | - | 58 places across 35 files use `utcnow().date()` or `date.today()`, including ages in protection and tax. Each can be a day out on the firm's calendar, and they are date-only so `on_firm_clock` cannot reach them. The conversation's own "today" is right (B-72). Move them to `firm_time.today` as each is touched, or in one pass |
| B-61 | The signed-in app on a phone | - | `Shell.tsx` keeps a fixed `w-64` sidebar with no breakpoint, so at phone width it takes about half the screen and clips the page beside it. Seen on the client's Canvas |
| B-65 | `verify_fees` fails before its first check | - | Its stub user has no `firm_id`, which `canvas._resolve_household` has needed since Canvas was held to its household. Same failure on the commit before B-04. It is not in the checks list, so nothing caught it. Thirteen more suites need a running server and do not run offline |
| B-79 | An open tax memo goes stale when the book trades | - | Tax Intelligence runs on the 1st of the month and a filled order does not refresh it. Live on 1 Nov, Karthik Iyer's rebalance used the whole exemption and left ₹81,942 of estimated tax, while the open memo still reads ₹1,25,000 unused and a ₹18,646 saving. Banerjee's reads ₹39,300 harvestable against ₹25,812. The Tax page and Astra read live, so only the memo is wrong, until 1 Dec. `seed.india_demo_backfill` rewrites them by hand |

## Parked until a client needs it

| ID | Item | L100 |
|---|---|---|
| B-20 | Lending and securities-backed credit | §4, L200 |
| B-21 | Revenue beyond advisory fees (NII, performance and trust fees) | §4 |
| B-22 | FATCA and CRS reporting | §9.3 |
| B-23 | Marketing Rule controls | §5.1 |
| B-24 | Annuities, structured products, options | §7 |
| B-25 | Tactical allocation | §5.6 |
| B-26 | Reconciliation book adjustment | - |

B-26 waits for a real custodian statement.

## Waiting on a decision

| ID | Decision | Why it is stuck |
|---|---|---|
| B-76 | The firm's cost of care | Care reads "not assessed" until a branch leader or administrator sets a yearly cost and years to plan for, from a source. Needs a real figure, and it differs by country, so the Indian firm needs its own |
| B-77 | Should the protection agent chase what it cannot assess | It raises measured gaps only. On 1 Nov the live NZ book had none, and six households were not assessed for want of an income. A proposal to go and get the income is possible, but it adds six to the queue, and a household with no gap measured is not known to have one |
| B-27 | PPEF1 real valuation | Needs a real figure and its source |
| B-28 | Credit and rebill the 13 bills issued on stale prices | Moves money again |
| B-29 | Hartley and Morrison retirement dates | Seeded goals have James Hartley working to 77 and Robert Morrison to 74, which sets their protection figures |
| B-30 | India alignment: the rest | Rows 1-3 of the investor demo are built. Rows 4-6 and everything for real client data still await approval (`reports/India market alignment for Astra.md`), and hosting is deferred. The customer's SEBI licences are unknown (D1), and could change B-02, B-04 and B-08 |
| B-47 | Reverse Churning reading the review record | Today any task or proposal counts as advisory activity, so its own proposal resets its 12-month clock. Reading recorded reviews fixes that, but flags every fee-paying household with no record and opens a surveillance flag for each, so it should wait for a yes and for real reviews to be recorded |
| B-42 | Tax Intelligence agent on NZ, US and UK | Calls `narrate` positionally, which raises, and reads NZ-only keys for US and UK. Fixing it starts raising proposals on the live firm, so it needs a yes. The India path is separate and works |

## Built, not yet proven live

| ID | Item | Proven so far |
|---|---|---|
| B-78 | The protection agent and long-term care | The real model on a throwaway database on 1 Nov: a run, a proposal, its Confirm card and a real approval adding the task; a care cost set by a branch leader and refused for an adviser; a care policy recorded and counted. Tests for the rest: clearing, staying quiet, growing, six months, rollback and Tier 3. On the live site it has no gap to raise and no cost of care to work from until B-76 |
| B-31 | Wash-sale guard holding a trade back | Tests only; on the 00:00 UTC run on 31 Oct it applied to Morrison and held nothing |
| B-32 | Protection record, end and income Confirm flows | Tests only |
| B-34 | Reconciliation import and resolve Confirm cards in chat, and a real proposal | Tests only |
| B-35 | Tasks Confirm write | Tests only |
| B-36 | Crediting an invoice, and Astra's prepare-run and credit tools | Tests only |
| B-41 | A real questionnaire through to an adviser's decision | Tests only; no client answers are seeded and none should be invented |
| B-50 | A client review recorded, booked or re-cadenced through Astra's Confirm | Tests and a local click-through of the page's own forms. Live on 31 Oct, Astra's Confirm card named the record and Not now wrote nothing. No review has been recorded live, because a record cannot be edited and none should be invented |
| B-56 | Sending a client message | Live on 31 Oct: the card with the exact words, and Not now. A confirmed send is tests only, because it reaches a client at once and cannot be taken back |
| B-53 | Approving and withdrawing a report | Live on 31 Oct: the approval card, and Not now. A confirmed approval or withdrawal is tests only, because a client would see it |
| B-04 | IPS monitoring: the accepted terms, the rebalancer working to them, the IPS monitor and exceptions. Includes B-40, the drafter reading the scored assessment and the targets on file | Live on 1 Nov, backfills run: 15 mandates, all no IPS on file, the page, the client chip, the inventory, the schedule set, an admin able to read and not record. On the real model, through a local frontend on the live backend: Astra read the book, the work pane opened it, the form reached Astra as a sentence, Astra questioned a document that was not an IPS, the Confirm card named every term, and Not now wrote nothing. Live on 1 Nov after the deploy: Astra opened it beside the conversation from a sentence, and the rebalancer's scheduled run left all 15 open proposals stamped, no failed runs. The drafter has not been run, and the monitor's first scheduled run is still to come |
| B-62 | A real IPS on a live mandate | Nothing yet. No IPS is on file for any seeded mandate and none should be invented, so every mandate reads "No IPS on file" until an adviser records one from a document. Until then the monitor has nothing to raise and the rebalancer is unchanged. An accepted breach is local and tests only |
| B-90 | A name that matches nothing gets an offer | Found in testing on 2 Nov: "Hurtley" came back not found. Astra now says so, offers the closest names on file as one-press choices and waits, because a similar name can be a different client. A search also finds a household by a person or entity in it. The Clients list and the search box offer the closest names too. Tests, and on the real model twice on throwaway data: not found, offered, then the right client once chosen. Not deployed |
| B-92 | What a walk through two scenarios on the live site found, 2 Nov | Fixed. Astra could not read a proposal's orders, so it worked out its own trades and warned of a tax breach the proposal did not have: it now reads the exact orders, gain and budget. The card for deciding any proposal other than a report, the IPS monitor or protection said only "This decision will be recorded permanently": it now names the proposal, each order, the cash it leaves (the Hartley rebalance takes $82,540 to $136), the gain against its budget and the venue. A UK-taxed household's NZD book was quoted in pounds. Astra narrated a failed id lookup, re-offered an approval after Not now, and listed expired proposals above open ones. The conversation lost the newest message when a view opened beside it, chips filled half the column, two rounds ran together with no space, a raw "**" showed while streaming, every row said "you may decide", a wash-sale check was offered to a household it cannot bind, and one press could approve whatever topped a list. Opening a proposal now shows that proposal alone. Tests, and on the real model on throwaway data. Not deployed |
| B-70 | A real debt, policy or statement through Confirm on the live site | Live on 1 Nov: the cards for a debt, a policy and a statement named every term, and Not now wrote nothing. A confirmed write is tests, and for a debt a confirmed one on a throwaway database with the real model. Every live household reads "assets only" until a person records one from a document, and none should be invented. The risk decision's card has not been seen live, because no questionnaire is on file |
| B-82 | Astra not drawing a rule from a mandate's type | Live on 1 Nov, after a refusal, Astra added that an advisory mandate would route through a proposal for client consent, which no tool returned and which is not so at a firm with no licences. Locally the same request drew it in 3 of 6 replies. The prompt now says a rule is a fact and a mandate's type is a label: 0 of 8 on the real model on throwaway data. Live on 2 Nov the same request drew a clean refusal, once, so it rests mainly on the eight |
| B-07 | Offboarding and wealth transfer: a departure or a death recorded from a document, the book kept as it stood that day, the mandate held, what a custodian moved booked to holdings, lots and cash, beneficiaries paid by designation, basis stepped up where the law does, and accounts, mandates and the household closing (`aurea_core/exits.py`, Departures and estates) | Tests, 86 checks. On the real model on throwaway data: a departure and a death from a plain sentence, Astra opening the view on one client, a payout from the form in the pane through its Confirm card, the view updating itself. Three things fell out of that run: Astra wrongly required an executor and a share for a named beneficiary (tool wording), a form dated by the browser's day where the firm's is the one checked, and an estate card that read as blocked. A final part-period fee shows as days unbilled and is raised by hand. Estate tax and a spouse's half step-up are not modelled. Not deployed |
| B-08 | Approved product list: research decisions added and never edited, closed stopping every purchase, gates needing a certification from a document, an "approved products only" switch a person throws, dearer share classes found within a family, and the rebalancer and direct orders reading the same answer (`aurea_core/shelf.py`, Product shelf) | Tests, 44 checks. On the real model on throwaway data: a product closed from a sentence and confirmed, and Astra warning before the firm-wide switch that nothing was approved yet. The page rendered locally. The shelf stops purchases and never pushes a sale: an early build fed it through exclusions, which the rebalancer divests, and tests now hold that apart. No decision is on file anywhere, so nothing changes live until the research team records one. Not deployed |
| B-09 | Equity compensation and concentrated stock: grants recorded from the plan statement, vesting from the schedule, value only on a real price, a position measured with what is granted, selling paths over 3, 5 and 7 years, and a plan read against the position (`aurea_core/equity_comp.py`, Equity compensation) | Tests, 50 checks. On the real model on throwaway data: concentration and paths from a question, a grant recorded through its card. The page rendered locally. Tax, AMT and withholding are named as not modelled. A broad equity fund above a fifth reads as concentrated, as in B-63. Not deployed |
| B-83, B-11, B-84, B-12, B-68 | The financial plan: retirement with withdrawal rates, income to count on, Social Security claiming, pension elections, the cash reserve, debts by cost, a ten-year projection and education funding, one view on one client (`aurea_core/financial_plan.py`, Financial plan). Recorded spending and debts now drive retirement. Goal projections outside retirement still do not read them | Tests, 62 checks, with the statutory factors checked against known figures. With nothing recorded the retirement figures do not change. On the real model on throwaway data: a plan read, a statement recorded and confirmed, claiming ages laid out. Not deployed |
| B-10, B-86, B-88 | The client file: trusted contacts, estate documents and whether they are out of date, life events that raise a review task, and beneficiaries who have died or are a former spouse (`aurea_core/client_file.py`, Client file) | Tests, 33 checks. Not deployed |
| B-85, B-89 | Cash movements: payment requests, maker-checker, third-party callback, RMDs taken against required, standing instructions, and phasing new money in, with open requests and later stages held back from every purchase (`aurea_core/cash_movement.py`, Cash movements) | Tests, 50 checks. Standing instructions are recorded and show the next date; raising each payment is a person's. Not deployed |
| B-87, B-81, B-13 | The trade lifecycle, direct orders and tax documents. A blotter with confirmations, settlement dates worked from the market, sources and blocks across accounts (`aurea_core/blotter.py`, Trades). A direct order needs the person's reason, goes on the provenance ledger, is refused above its capacity ceiling and names a stock past a fifth of the book. A year's summary and what it should produce, on the market's own tax year, with a person recording what arrives (`aurea_core/tax_documents.py`, Tax documents) | Tests, 42 checks. On the real model on throwaway data: Astra asked for a reason it had been making up, kept the client's words, read the blotter and said the venue was paper. Through the app locally: a sale from a sentence, its card, Confirm, and the blotter updating itself. Three things fell out of that run: a reason filled in by Astra, a bond fund called concentrated, and a calendar year used for every market. Surveillance still does not review a direct order, whose rules are written for proposals: the ledger is its record. Settlement does not model holidays, and the book still takes a trade at once on its trade date. Not deployed |
| B-37, B-38, B-39, B-44, B-52 | Risk and wording. A mandate's model read against its profile (`model_fit`), named on the Risk profiles page, on the adopt card and as a gap on a rebalance. Wealth against needs beside the questionnaire, as figures that change nothing. A questionnaire per person or entity, so a trust is asked for itself. Astra no longer calls a profile unsupported, or a reviewed client unreviewed | Tests. On the real model on throwaway data: Astra said no questionnaire is recorded here and that it cannot call the profile unsupported, twice. It first still opened with "nothing supports it", so the tool result now says so outright. A model's profile is read from its name, which is the only thing that says; a name that does not say reads "not stated". Moving a mandate to another model is still a person's, from admin. Not deployed |
| B-63, B-66 | The rebalancer acts on an IPS limit. A holding over its limit is sold down and a restricted one sold out, whatever its class is doing, inside the tax budget and the wash-sale guard, unless a person accepted the breach or holds it back. The research team can mark a product a diversified fund, and a limit is then not read against it, here, in the monitor, on a direct order or as concentration. A sleeve nothing here can fill, like a private fund, no longer raises a rebalance alone, and the monitor still reports it outside the band | Tests, 43 checks with B-64 and B-69, on the optimiser and through the agent. No mandate has an IPS live, so nothing sells until one is recorded (B-62). No live product is marked diversified. A fund is marked, not looked through: what it holds is not on file. Not deployed |
| B-64, B-69 | A client sees their net worth and the IPS terms they accepted on Canvas, and the client assistant can state both. Only the terms, not what the monitor finds. A client says something is wrong by a message that raises a task for the adviser, and nothing changes until a person records it | Tests, including a client naming another household. Not deployed |
| B-75 | The remaining Confirm cards on the live site | From B-67: client consent, registration, conflict, WSP evidence, the pipeline and corporate actions have been seen in tests only |

## Done

| Item | Commit |
|---|---|
| B-06: Protection slice 2. The protection agent raises measured gaps, and long-term care is modelled from the firm's stated cost. Not deployed. Needs `app.db_bootstrap`, `seed.schedule_backfill` and `seed.ai_governance_backfill`. See B-76, B-77 and B-78 | The commit that adds this line |
| B-45: India demo, a real consent and a real fill, confirmed live 1 Nov on both tracks with real prices on the paper venue. Banerjee: approval placed nothing, a simulated consent bound to those orders filled 3 of 3. Iyer: approval filled 4 of 4 with no consent asked. FIFO lots, fees and tax reconcile, the ledger chain is valid. Found B-79 | The check, no code |
| B-72: a moment in a tool result reaches the model on the firm's clock (`firm_time.on_firm_clock`); stored results stay in UTC. Tests only, not deployed | `be3723a` |
| Messages: what the live check found. An automated notice no longer hides an unanswered client message (the Chen thread), Astra reads a thread before describing it, both confirmed live 1 Nov. Cross-firm Canvas reads were refused both ways between the NZ and Indian firms, live 31 Oct | `3a0203c` |
| B-57 to B-60: tax memos with room to finish and each saving credited to its source; the client assistant given goals, targets and written money, and its replies rendered. Confirmed live 1 Nov, backfill run: all five memos finish, each saving is credited to its source, the assistant states the equity and both goals | `d94fc65` |
| India demo: what the live check found. The carry-forward on a harvest, memo wording, goals sized with contributions, the client assistant's cut-off replies, a line that read as a data-location claim. Confirmed live 31 Oct, backfill run: ₹0 carried, 4 of 6 goals on track. The memo wording only partly, finished in B-57 | `8427c73`, with `seed/india_demo_backfill.py` |
| India demo rows 1-3: market layer, licences and consent, per-taxpayer tax, the demonstration firm | `035007d`, `3183767`, `7a10e46` |
| Billing and invoices | |
| Performance: time-weighted and money-weighted returns | |
| Price refresh | |
| Fee-source mismatch, API URL typo | |
| Custodian reconciliation and its agent | `56a75e6`, `5b27d62` |
| Wash-sale enforcement in the rebalancer | `dbbf98d` |
| "$0 short" on proposals | `a094429` |
| Tasks in the conversation | `e424ae8` |
| Protection slice 1 and its fixes, confirmed live 31 Oct | `7a06b89`, `569bc13`, `3028117` |
| Reports in the conversation | `8326cd9` |
| B-01: Messages in the conversation, and Astra hearing Not now, confirmed live 31 Oct | `03c04d2`, `8c9e5a3` |
| B-02: Risk questionnaire scoring, confirmed live 31 Oct; a real questionnaire through to a decision is B-41 | `aef1e1a` |
| B-33: Three protection tools in the AI inventory, confirmed live 31 Oct | `7a06b89` |
| B-03: Client reviews, confirmed live 31 Oct: 14 households, 2 carried by the backfill, the page, Astra's card, the client chip, the three tools in the inventory, a client refused. A real record through Confirm is B-50 | `ed6974f` |
| B-43: A decided Confirm card stays in the thread, confirmed live 31 Oct: Not now keeps its rows through a reload, and older cards keep Confirmed or Not now without rows. Expired and Failed are tests only | `6f8a272` |
| B-17: Client reviews, Protection, Risk profiles and Reconciliation beside the conversation, acting through it, confirmed live 1 Nov. A form in the pane reached Astra and its Confirm card. Astra first said it had opened Reconciliation without doing so, because the view list given to the model stopped at the older views. Fixed in `d48fddb`, and live on 1 Nov Astra called `open_view` and Reconciliation and Protection each opened from a sentence | `f194b5f`, `d48fddb` |
| B-46: Confirm cards naming the change for protection and reconciliation, confirmed live 1 Nov. The risk decision's is tests only, see B-70 | `f194b5f` |
| B-05: Net worth, confirmed live 1 Nov: 14 households all assets only, protection's summary unchanged, Astra's figures matching the record and saying no debt on file is not no debt, the card showing what a debt does to net worth. A real debt is B-70 | `a3a936b` |
| B-71: A direct order held to what a proposal is held to: what the account can do, the mandate's exclusions, the IPS and the household's tax window, before its card and again at Confirm, the set as a whole (`aurea_core/order_guard.py`). A sale carries its estimated gain from the lots it would relieve. Confirmed live 1 Nov: a sale of 100,000 against 800 held and a buy short by 439,369.70 refused with the reason, nothing placed, and a sale's card showing its gain, Not now. No live mandate has an IPS, so the IPS refusals are tests and the real model on throwaway data until one is recorded (B-62). The tax-window refusals are tests only | `f6f751e` |
| B-74: Astra acting when it says it will. It could say "I'll refresh the prices now" and end the turn with no tool called, because the history it reads holds its words and not its tool calls; the prompt now says so, and the gateway asks once. Confirmed live 1 Nov on the two sequences that had stalled: each reached its tool, a refusal with its reason or a card. Whether the prompt or the reminder did it is not visible | `85791da` |
| B-67: Every Confirm card names the change, all 36 tools, and a new gated tool with no rows fails `verify_confirm_cards`. Confirmed live 1 Nov on the order, beneficiary, goal, billing, price refresh, credit and valuation cards, Not now each time, and a refresh inside its 10 minutes refused with the reason. A confirmed goal change on the real model on throwaway data. The other cards are B-75. Also fixed: `log_crm_activity` took an opportunity from any firm or contact, and posting a merger from a conversation recorded nothing and closed the entitlement. A goal timeline that is not a year is now refused, not noted. Corrected 2 Nov: a decision on a proposal named itself only for reports, the IPS monitor and protection, and the check that every card names its change passed because it looked for the tool's name; B-92 names every proposal | `992890c` |
| The app is named Astra for Wealth at the top of the conversation's sidebar, without the Preview label, the user's ask on 2 Nov | The commit that adds this line |
| B-80: A view opens on the client being discussed. Net worth, Protection, Client reviews, IPS and Risk profiles open on one client from a sentence or a card's button, with that client's figures and an All clients button. Confirmed live 2 Nov by the user's own retest, on Net worth and Protection. Client reviews, IPS and Risk profiles were seen locally | `734cf25` |
