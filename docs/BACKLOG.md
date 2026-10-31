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
| B-03 | Client reviews: the record, cadence, next due date, booking, and fees since the last review | Tests and a local click-through on a throwaway database, not deployed. Needs `seed.client_review_backfill` and `seed.ai_governance_backfill` |
| B-43 | A decided Confirm card stays in the thread, with what it showed and how it ended | Tests only, not deployed. Cards decided before the deploy keep their chip but have no saved rows |
| B-45 | India demo through Astra's chat and a real consent | The firm, its 5 households and the licences API are live on 31 Oct; chat and consent not tried there |

## Done

| Item | Commit |
|---|---|
| India demo rows 1-3: market layer, licences and consent, per-taxpayer tax, the demonstration firm | `035007d`, `3183767` and the commit that adds this line |
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
