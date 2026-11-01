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

Nothing is being built right now.

## Next, in order

| ID | Item | L100 | Where it stands | Size |
|---|---|---|---|---|
| B-06 | Protection slice 2 | §5.4 | No agent raises gaps as proposals; long-term care not modelled | Medium |
| B-07 | Offboarding | §5.11 | No account closure or transfer-out flow | Medium |
| B-08 | Approved product list | §5.7 | No manager due diligence or approved list | Medium |
| B-09 | Equity compensation and concentrated stock | §5.2, §5.4 | Not built | Large |

## Smaller items

| ID | Item | L100 | Where it stands |
|---|---|---|---|
| B-10 | Trusted contact | §5.3 | Not built |
| B-11 | Social Security claiming | §5.4 | Not built |
| B-12 | Education funding | §5.4 | Passing mentions only |
| B-13 | Tax documents (1099, K-1 and local equivalents) | §5.9 | Not built |
| B-14 | Binary document storage | - | Text only |
| B-15 | Book scan covers every scheduled agent | - | `POST /api/studio/scan` runs three agents |
| B-16 | Household currency | - | Not stored; protection shows UK and US figures as NZD with no conversion |
| B-18 | Wash-sale calendar for non-US households | - | The tool still reports for NZ households, though the rule binds US ones only |
| B-19 | Double-count guard for pensions | - | A recorded pension now replaces the pots on a person's profile, but accounts have no wrapper type, so a pension both booked and recorded still counts twice |
| B-37 | A mandate's model against its risk profile | §5.5 | Adopting changes the profile, not the model; only equity over the capacity ceiling is checked, so a conservative profile can sit on a Growth model unflagged |
| B-38 | Capacity from wealth against needs | §5.2 | The questionnaire cannot see it, so capacity comes from horizon, income stability and liquidity alone |
| B-39 | Questionnaire by person or entity | §5.2 | One set of answers per household, compared with every mandate; a trust is not asked separately |
| B-44 | Astra overstates "not taken" | §5.2 | Live on 31 Oct it said nothing on file supports a mandate's profile. The tool knows only that no questionnaire is recorded here; a firm may hold one elsewhere |
| B-68 | Plans that read debts and spending | §5.4 | Net worth and protection read them. Goal and retirement projections do not: they still assume no debt payments and no recorded spending |
| B-69 | The client sees their net worth | §5.4 | Reading is staff only. A client's Canvas shows no net worth, and the client cannot correct what is on file |
| B-67 | Every Confirm card names the change | - | 34 tools stop on a Confirm card and 16 name what they change. The other 18 show a general sentence: among them `execute_orders`, `credit_invoice`, `record_valuation` and `record_client_consent`, where the card matters most. `run_agent` has its own preview |
| B-48 | Over-promising rule reads a denial as a promise | - | `no_overpromise` matches words, so "does not mean a rebuy carries no risk" and "not a guarantee of outcome" fail it. Two Indian tax memos show this live. Every firm shares the rule and over-flagging is the safe direction, so the user chose on 31 Oct to leave it |
| B-49 | Approve shown to a role that cannot approve | - | `RecommendationCard` shows Approve to everyone. The server refuses compliance on a proposal that moves a book, so the control holds, but the screen should read `decision_rights` |
| B-51 | A lapsed Confirm card still offers Confirm | - | A pending action expires after 24 hours, but only when someone clicks. Until then the card shows live buttons; the server refuses the click and only then does it read Expired |
| B-52 | Astra blurs "no review on record" | §5.10 | Live on 31 Oct it listed the Chens, reviewed on 14 Sep, among clients paying fees with no review on file. `read_client_reviews` was right. A carried review also reports all four topics as `not_covered` when they are unknown; the page says "not recorded" |
| B-54 | Approved outreach under the right adviser's name | - | `client_care.act` signs with the first adviser found at the firm, not that client's adviser. Only matters with more than one adviser |
| B-55 | A report's age at the moment of approval | - | Approving shows the period but no warning. The live Morrison draft is dated 13 Aug, so its figures were 11 weeks old on 31 Oct; a redraft refreshes the same draft |
| B-61 | The signed-in app on a phone | - | `Shell.tsx` keeps a fixed `w-64` sidebar with no breakpoint, so at phone width it takes about half the screen and clips the page beside it. Seen on the client's Canvas |
| B-63 | What an IPS limit does not do yet | §5.5 | The rebalancer stops a buy at a position limit and a cash floor, and never buys a restricted holding, but it does not sell down a holding already over its limit or restricted unless its class is overweight. The monitor reports those. A position limit also counts a diversified fund as one holding, because an instrument does not say what it is |
| B-64 | The client sees their IPS | §5.5 | Reading is staff only. A client's Canvas shows no IPS, though the terms are theirs |
| B-66 | A model sleeve the rebalancer cannot fill | §5.5 | Live on 1 Nov, all 15 mandates are outside their model's band with a rebalance open. Five Growth mandates hold none of the model's 10% alternatives, which is a private fund subscribed to outside the OMS, so a rebalance cannot clear it, and Patel and Wei & Mei are outside only because of it. The proposal reappears until the money goes in or the model changes |
| B-65 | `verify_fees` fails before its first check | - | Its stub user has no `firm_id`, which `canvas._resolve_household` has needed since Canvas was held to its household. Same failure on the commit before B-04. It is not in the checks list, so nothing caught it. Thirteen more suites need a running server and do not run offline |

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
| B-27 | PPEF1 real valuation | Needs a real figure and its source |
| B-28 | Credit and rebill the 13 bills issued on stale prices | Moves money again |
| B-29 | Hartley and Morrison retirement dates | Seeded goals have James Hartley working to 77 and Robert Morrison to 74, which sets their protection figures |
| B-30 | India alignment: the rest | Rows 1-3 of the investor demo are built. Rows 4-6 and everything for real client data still await approval (`reports/India market alignment for Astra.md`), and hosting is deferred. The customer's SEBI licences are unknown (D1), and could change B-02, B-04 and B-08 |
| B-47 | Reverse Churning reading the review record | Today any task or proposal counts as advisory activity, so its own proposal resets its 12-month clock. Reading recorded reviews fixes that, but flags every fee-paying household with no record and opens a surveillance flag for each, so it should wait for a yes and for real reviews to be recorded |
| B-42 | Tax Intelligence agent on NZ, US and UK | Calls `narrate` positionally, which raises, and reads NZ-only keys for US and UK. Fixing it starts raising proposals on the live firm, so it needs a yes. The India path is separate and works |

## Built, not yet proven live

| ID | Item | Proven so far |
|---|---|---|
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
| B-70 | A real debt, policy or statement through Confirm on the live site | Live on 1 Nov: the cards for a debt, a policy and a statement named every term, and Not now wrote nothing. A confirmed write is tests, and for a debt a confirmed one on a throwaway database with the real model. Every live household reads "assets only" until a person records one from a document, and none should be invented. The risk decision's card has not been seen live, because no questionnaire is on file |
| B-45 | India demo: a real consent and a real fill | Live on 31 Oct: the firm, all four Indian seats, Astra's tax and licence tools, the client assistant and compliance under IN-SEBI. Not tried on Azure: approving, recording a consent, or an order filling on either track |

## Done

| Item | Commit |
|---|---|
| Messages: what the live check found. An automated notice no longer hides an unanswered client message (the Chen thread), Astra reads a thread before describing it. Not deployed yet. Cross-firm Canvas reads were refused both ways between the NZ and Indian firms, live 31 Oct | The commit that adds this line |
| B-57 to B-60: tax memos with room to finish and each saving credited to its source; the client assistant given goals, targets and written money, and its replies rendered. Tests; not yet live | The commit that adds this line |
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
| B-17: Client reviews, Protection, Risk profiles and Reconciliation beside the conversation, acting through it, confirmed live 1 Nov. A form in the pane reached Astra and its Confirm card. Astra did not open Reconciliation from a sentence, and said it had: the view list given to the model stopped at the older views, fixed in the commit that adds this line and not yet deployed | `f194b5f` |
| B-46: Confirm cards naming the change for protection and reconciliation, confirmed live 1 Nov. The risk decision's is tests only, see B-70 | `f194b5f` |
| B-05: Net worth, confirmed live 1 Nov: 14 households all assets only, protection's summary unchanged, Astra's figures matching the record and saying no debt on file is not no debt, the card showing what a debt does to net worth. A real debt is B-70 | `a3a936b` |
