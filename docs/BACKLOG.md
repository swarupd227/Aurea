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

| ID | Item | State |
|---|---|---|
| B-01 | Deploy `03c04d2`: protection assets, Not now, Messages, Reports | Deploy running. After it: expect protection at 0 with a gap, 3 clear, 6 not assessed, 5 no one to assess |

## Next, in order

| ID | Item | L100 | Where it stands | Size |
|---|---|---|---|---|
| B-02 | Risk questionnaire scoring | §5.2 | Answers saved in `Mandate.suitability`; no score; tolerance not compared with capacity | Small |
| B-03 | Client review cadence | §5.10 | No next-review date for clients; cadence exists only for compliance and AI reviews | Small |
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
| B-17 | Protection and reconciliation in the work pane | - | Not in `OPEN_VIEWS` |
| B-18 | Wash-sale calendar for non-US households | - | The tool still reports for NZ households, though the rule binds US ones only |
| B-19 | Double-count guard for pensions | - | Accounts have no wrapper type, so one both booked and recorded on a profile would count twice |

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
| B-30 | India alignment | Proposals await approval in `reports/India market alignment for Astra.md`; the SEBI licence could change B-02, B-04 and B-08 |

## Built, not yet proven live

| ID | Item | Proven so far |
|---|---|---|
| B-31 | Wash-sale guard holding a trade back | Tests only; on the 00:00 UTC run on 31 Oct it applied to Morrison and held nothing |
| B-32 | Protection record, end and income Confirm flows | Tests only |
| B-33 | Three protection tools in the AI inventory | Not checked; needs an admin session |
| B-34 | Reconciliation import and resolve Confirm cards in chat, and a real proposal | Tests only |
| B-35 | Tasks Confirm write | Tests only |
| B-36 | Crediting an invoice, and Astra's prepare-run and credit tools | Tests only |

## Done

| Item | Commit |
|---|---|
| Billing and invoices | |
| Performance: time-weighted and money-weighted returns | |
| Price refresh | |
| Fee-source mismatch, API URL typo | |
| Custodian reconciliation and its agent | `56a75e6`, `5b27d62` |
| Wash-sale enforcement in the rebalancer | `dbbf98d` |
| "$0 short" on proposals | `a094429` |
| Tasks in the conversation | `e424ae8` |
| Protection slice 1 and its fixes | `7a06b89`, `569bc13`, `3028117` |
| Reports in the conversation | `8326cd9` |
| Messages in the conversation | `03c04d2` |
