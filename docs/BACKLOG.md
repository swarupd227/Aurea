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
| B-04 | IPS monitoring | §5.5 | Drift is checked against the model portfolio, not the accepted IPS | Medium |
| B-05 | Net worth and liabilities | §5.4 | Held-away not in household totals; no liabilities or expenses model; protection cannot count debts | Medium |
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
| B-17 | Protection, reconciliation, risk profiles and client reviews in the work pane | - | Not in `OPEN_VIEWS` |
| B-18 | Wash-sale calendar for non-US households | - | The tool still reports for NZ households, though the rule binds US ones only |
| B-19 | Double-count guard for pensions | - | Accounts have no wrapper type, so one both booked and recorded on a profile would count twice |
| B-37 | A mandate's model against its risk profile | §5.5 | Adopting changes the profile, not the model; only equity over the capacity ceiling is checked, so a conservative profile can sit on a Growth model unflagged |
| B-38 | Capacity from wealth against needs | §5.2 | The questionnaire cannot see it, so capacity comes from horizon, income stability and liquidity alone |
| B-39 | Questionnaire by person or entity | §5.2 | One set of answers per household, compared with every mandate; a trust is not asked separately |
| B-40 | IPS drafting reads the assessment | §5.5 | `ips_drafting` reads the mandate's profile, not the scored tolerance and capacity. Do with B-04 |
| B-44 | Astra overstates "not taken" | §5.2 | Live on 31 Oct it said nothing on file supports a mandate's profile. The tool knows only that no questionnaire is recorded here; a firm may hold one elsewhere |
| B-46 | Confirm cards that name the change | - | `preview_change` has no rows for `decide_risk_profile` or the protection tools, so their cards show only a general sentence, not the mandate, policy or figure being confirmed |
| B-48 | Over-promising rule reads a denial as a promise | - | `no_overpromise` matches words, so "does not mean a rebuy carries no risk" and "not a guarantee of outcome" fail it. Two Indian tax memos show this live. Every firm shares the rule and over-flagging is the safe direction, so the user chose on 31 Oct to leave it |
| B-49 | Approve shown to a role that cannot approve | - | `RecommendationCard` shows Approve to everyone. The server refuses compliance on a proposal that moves a book, so the control holds, but the screen should read `decision_rights` |
| B-51 | A lapsed Confirm card still offers Confirm | - | A pending action expires after 24 hours, but only when someone clicks. Until then the card shows live buttons; the server refuses the click and only then does it read Expired |
| B-52 | Astra blurs "no review on record" | §5.10 | Live on 31 Oct it listed the Chens, reviewed on 14 Sep, among clients paying fees with no review on file. `read_client_reviews` was right. A carried review also reports all four topics as `not_covered` when they are unknown; the page says "not recorded" |
| B-54 | Approved outreach under the right adviser's name | - | `client_care.act` signs with the first adviser found at the firm, not that client's adviser. Only matters with more than one adviser |
| B-55 | A report's age at the moment of approval | - | Approving shows the period but no warning. The live Morrison draft is dated 13 Aug, so its figures were 11 weeks old on 31 Oct; a redraft refreshes the same draft |
| B-61 | The signed-in app on a phone | - | `Shell.tsx` keeps a fixed `w-64` sidebar with no breakpoint, so at phone width it takes about half the screen and clips the page beside it. Seen on the client's Canvas |

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
