# India live proof

The India work is built and passes its suites, but a suite proves code against a SQLite seed. This page is how the same
features are proven on the hosted demonstration firm (slug `india-demo`), with no password sign-in from us and nothing
written. Two parts: a read-only harness run inside the backend container, and a click-through per role for what only
a signed-in browser shows.

Nothing here says the firm is compliant. Every line is what the demonstration seed put on file, read back through the
code a person or an agent uses.

## 1. The harness (read-only)

`backend/app/tools_live/india_live_proof.py`, one file. It finds the firm by slug, and for each India feature runs the
`ToolExecutors` `read_*` tool as the roles that hold it (the gateway's own `validate_tool_call` gate first, then the
executor), plus the `aurea_core` read functions beneath them, and asserts what `seed/india_*.py` created. One line per
check: `PASS` or `FAIL`, the feature, the role and what was seen; then a count. Exit code 1 on any `FAIL`, 0 otherwise.

Run it in a brand-new Cloud Shell (the container needs no stdin):

```bash
az account set --subscription b017b8b9-0911-43e7-96e9-4e38dd4c06f7
az account show --output table
az containerapp exec --name aurea-backend --resource-group aurea-rg \
  --command "python -m app.tools_live.india_live_proof" < /dev/null
```

It is in the image the backend ships (it sits in `app/`), so it needs the next backend deploy first. The local proof of
the harness itself is `python -m tests.verify_india_live_proof` from `backend/` (it builds the same firm on SQLite and
expects every check to pass).

### Why it cannot write

* The session is only ever rolled back. `commit`, `add`, `add_all` and `delete` on it raise `WriteBlocked`.
* A `before_flush` listener raises if anything is new, deleted or modified.
* A `before_cursor_execute` listener raises on any statement that is not a SELECT, a WITH query without a write word,
  SHOW, PRAGMA or EXPLAIN. On PostgreSQL the transaction is also `SET TRANSACTION READ ONLY`, so the server refuses a write.
* The guards are proven first on a throwaway in-memory SQLite database (six lines headed "The write guards"), never on the
  live one, and again by `tests.verify_india_live_proof`, which also checks every table's row count is the same after a run.
* A read tool that tripped a guard would be reported as `FAIL ... A READ WROTE` and would already have been stopped.

### What it reads, and what it leaves to the suites

Read here: the Licences page's whole payload (`licences.overview`, 25 India sections) as compliance and as an adviser, the
pre-advice gate, the fee cap (`billing.preview`, read), the compliance calendar, residency and guardianship, Portfolio Manager
checks, the Account Aggregator and held-away lines, the complaints register, the advertising check on a text, India tax
(three taxpayers, the loss ledger, the engine's records, the dates), KYC states and the book hold, the SEBI family and estate,
the policy register, the licence map, counsel's review, adviser certification, the reminders (the would-raise list only), the
ticket-size gates, accredited investors, adviser fee terms, the rate card, LRS headroom, SIP debit mandates, the shelf's other
classes, pledges, tax habits, languages and WhatsApp, the DigiLocker, eSign and KRA seams, mutual fund order rails and NAV days,
advice kept apart from distribution, advertisement approvals, the decision rights, and a client's own read. Then a sweep: every
India read tool, run as every role that holds it.

Read-only, so **only the suites prove** these write paths: every `record_*` and `prepare_*` followed by a Confirm card
(KYC, PAN, residency, guardian, PMS agreement, filings, complaints, LRS, pledges, SIPs and SIP mandates, savings, tax losses,
nominations, HUF, gratuity, policies, counsel review, accredited investors, fee terms, language, WhatsApp template, orders,
certifications, advertisement submit and decide), `raise_india_reminders` and its job, the approval of a book-moving
proposal and the refusal it gets while a KYC is overdue or paperwork is missing (`runtime.decide`), consents, the client's
own answer, a simulation's DigiLocker pull or eSign envelope, and the fee run itself. The click-through below shows the
buttons exist; it does not press them.

Tools skipped because they write when read: none. `read_case_facts` (needs an onboarding case id) and `read_pms_report`
(needs a mandate id) are left out of the sweep because they take an id this harness does not choose; their data is covered
through `read_pms` and the onboarding pages.

### A FAIL is a finding

Dates in the seed are days before the seeding day, so some states drift as the live firm ages: an X-B certificate that
was "due soon" runs out, the complaints at risk go overdue, a KYC "due" goes overdue. The harness accepts the drifts it
can foresee and prints what it saw. Records a person has since added or changed on the live firm (a consent for the
Banerjees, a refreshed KYC for Lakshmi Iyer) will read as a `FAIL`; that is the live data differing from the seed, not a
broken feature, and the line says what was seen.

## 2. The click-through, by role

Sign in at the hosted frontend with each demonstration persona (`<name>@astra-india.demo`), in a private window per role,
because parallel sessions share one browser's sign-in. Every page below is under **/studio** unless it says otherwise. The
role's menu is `frontend/lib/nav.ts`; the sections are tabs. An India firm hides the US-only pages (Equity compensation,
Repapering, Account features, Money in, Transfers in) and calls Beneficiary audit "Nominations".

### Everyone on the staff side: Licences & SEBI (`/studio/licences`)

Open for adviser, paraplanner, portfolio, compliance, operations, branch and admin. Header "Licences" with a
**demonstration firm** chip. Top tiles (`licence-summary`): Licences held 2; Agent ceiling Tier 2 (never Tier 3); Client
consents; Waiting on clients. Cards, in order, each with what must be on screen:

| Card | Must show |
|---|---|
| Compliance calendar (`licence-calendar`, `licence-officers`) | Complaint data (Annexure C) and the Portfolio Manager's monthly report with due days and the circular; a chip "from the register" on the complaints filing; officers not recorded (Designated Director, CISO). Compliance, branch, operations and admin see the filing form. |
| Held by this firm (`licence-matrix`) | Investment Adviser and Portfolio Manager, discretionary, each "held" with what it allows. |
| Before advice (`licence-pre-advice`) | Four adviser households. Each agreement reads Annexure A and the MITC recorded (after `seed.india_demo_backfill`). Sharma all green; D'Souza met, declaration "n/a"; **Banerjee fails "Risk profile complete and consented"**; **Menon fails "FATCA and CRS self-certification"** (the "No advice yet" household). |
| Fees against SEBI's cap (`licence-fee-caps`) | **D'Souza over the cap, fixed-fee mode, Rs 1,51,000**, shown blocked; Sharma within 2.5% in AUA mode (three arrangements, HUF included); Banerjee within. |
| Residence | The Menons: two people, non-resident. |
| Minors and their guardians (`licence-guardians`) | Ira: guardian on file (father); Kabir: no guardian; Tara: now an adult, conversion to be recorded. |
| KYC, as recorded (`licence-kyc`) | "Lookups not connected" chip. **Lakshmi Iyer overdue; Nandini Kapoor blocked**; Karthik, Subir, Anita due; Kapoor Family Trust not assessed; Elaine D'Souza not on file; Rajesh, Meera, the HUF and Vikram verified. |
| Portfolio Manager clients (`licence-pms`) | Karthik Iyer all checks pass; Kapoor Family Trust has no agreement and reads "not assessed", not "none". |
| Account Aggregator and statements (`licence-aggregator`) | One CAMS/KFintech statement for the Iyers, two lines; no consent on file (opt-in). |
| Complaints register (`licence-complaints`) | Seven complaints: overdue, at risk, open, two resolved, one withdrawn; the impersonation complaint with acknowledgement due or late; the complaint about advice marked told to compliance. |
| Check client wording | Paste "We guarantee 20% returns, risk free": a block and a flag. A plain risk caveat shows nothing and says it is not an approval. |
| Family of client and estate (`licence-family`) | Sharmas: family recorded; Iyers: "stand in" chip. HUF karta Rajesh Sharma; gratuity Rs 38,00,000; Banerjee nominee not named in the will. |
| Dates that need a task (`licence-reminders`) | Six or more dates: the cyber policy review overdue, the filings, Lakshmi's KYC, Nandini's KYC, Tara's conversion, Ananya's X-B certificate; each with its task. |
| Licence map and governance register; Adviser certifications; Counsel's review | **Compliance, branch, operations and admin only** (absent for adviser, paraplanner and portfolio). One policy overdue, one due soon, two current, one not recorded; counsel 2 confirmed, 1 not confirmed, 1 awaiting the Budget, the rest not reviewed; Ananya X-A valid, X-B due soon. |
| Product categories open to each household | SIF, PMS, AIF 1/2/3 by ticket size; Kapoor SIF "waived" (accredited member); Iyer not. |
| Accredited investors | Nandini accredited to May 2027; **Karthik lapsed, "not accredited"**. |
| Adviser fee terms | Sharmas: six months in advance, switched to AUA mode; Menons: second-opinion fee Rs 40,000; the IAASB deposit in a liquid fund. |
| Languages and WhatsApp | Sharma Hindi, Banerjee Bengali, D'Souza none (English); three households opted in; annual-review template approved in English and Hindi; no provider. |
| DigiLocker and eSign | "Not connected" with the reason, and the KRA/CKYC lookup "not connected". |
| Overseas remittances | FY 2026-27: Karthik US$1,00,000 remitted, US$1,50,000 headroom of 2,50,000, TCS collected. |
| SIP mandates | UMRN-DEMO-IYER-0001 in force, limit Rs 25,000; SIPs listed against mandates. |
| Savings, pension and other classes | Banerjee government security and market-linked debenture, held away, in rupees. |
| Mutual fund cut-off figures | One figure recorded (standard cut-off), three assumed. |
| Mutual fund order rails and NAV days | Four orders with rail, RTA and folio; **one NAV day differs (3 days), one not assessed**. |
| Advice kept apart from distribution | Three group entities (one determined outside the family of entities, one awaiting counsel), one flag for compliance, and the direct-against-regular expense ratio comparison (empty until a scheme ratio is recorded; a scheme with none reads not assessed). |
| Advertisement approvals | One approved, one rejected, one waiting for compliance. |
| Pledged units | 5,000 Nifty ETF units under lien of 20,000; 15,000 sellable. |
| February rate card | One figure "awaiting the Budget"; the rest not reviewed. |
| The firm's India dates (`licence-india-dates`) | Dated items with their sources. |

Also on this page: Waiting on clients, Answers on record, and Mandates by track (`licence-mandates`): seven on the adviser
track, two on the Portfolio Manager track.

### Role by role

* **adviser@ (Ananya Rao).** Lands on `/studio`. Pinned: Recommendations, Clients, Meetings, Tasks. Open Licences & SEBI (all
  cards above except the governance register, certifications and counsel's review). Clients > The Sharma Family: tax
  intelligence shows three taxpayers (Rajesh, Meera, the HUF), Rajesh's brought-forward Rs 40,000, Meera's regime
  comparison "new"; Plan shows the EPS pension and gratuity; Protection shows the policies with who is paid. The Banerjee
  recommendation's Confirm is refused naming the missing risk profile consent (suites prove the refusal; here only see the
  gate on Licences). Recommendations: approve is offered on adviser-track proposals, and a client's consent is awaited.
* **paraplanner@ (Ishaan Verma).** `/studio/meetings`. Licences & SEBI as the adviser (no registers); reminders; the
  Menon pre-advice gate.
* **portfolio@ (Vikram Shah).** `/studio/review`. Recommendations: approves the Portfolio Manager proposals by name; the
  Kapoor and Iyer households are held by KYC (Nandini blocked, Lakshmi overdue) so approval is refused with the reason.
  Licences: the Portfolio Manager clients card. Money & trades > Reconciliation, Prices.
* **compliance@ (Neha Kulkarni).** `/provenance`. Licences & SEBI with every card including the governance register,
  certifications, counsel's review and the calendar's filing form. Recommendations can be dismissed or rolled back, not
  approved. Provenance shows the ledger. Compliance > Regulatory lists the rules.
* **operations@ (Suresh Pillai).** `/studio/onboarding`. Onboarding section tabs (Onboarding, queues, document matrix,
  consents, AML, scoreboard), no US-only tabs. Licences & SEBI: KYC, mutual fund orders and the calendar. Billing: three
  mandates with no fee agreement; D'Souza held at the cap.
* **branch@ (Kavita Joshi).** `/studio/capacity`. Licences & SEBI as compliance (registers included). Billing; the
  Compliance section.
* **admin@ (Rohan Gupta).** Menu: Settings (Firm & agents, Agent controls), Agents, Compliance. Licences & SEBI opens (admin
  sees every view). The admin approves no book-moving proposal and cannot decide a recommendation; Agent controls shows
  tiers capped by the licence (no Tier 3).
* **client@ (Rajesh Sharma).** `/workspace` and `/canvas`, no staff menu. Ask "what is my net worth" or "how are my goals":
  the answer is in rupees, for the Sharma household only, goals carrying a likelihood as an estimate. `/studio/licences`
  is refused by the API (403); no India staff tool is offered to the conversation. On `/canvas`, Requests shows the recorded language
  (Hindi) and WhatsApp state with an ask for another language; pressing one writes a task for the adviser (a write: not in the harness).
* **research@ (Dr. Meenakshi Sundaram).** `/studio/reports`. No Licences & SEBI page and no licence or pre-advice tool;
  Product shelf and the advertisement check are the India pages in reach.

### What a signed-in session adds to the harness

* The rendered state (chips, colours, the "demonstration firm" chip, the section tabs) and the menu per role, which the
  harness cannot see.
* That the hosted frontend and backend are the same build as this branch.
* The side-by-side with the conversation: ask Astra in the adviser's conversation "what is missing before the Menons can be
  advised" and expect the FATCA and CRS self-certification, with a Sources row and a card, never a number from memory.

## 3. Extending it

A feature gets one `async def g_<feature>(P: Proof)` decorated `@group` in `india_live_proof.py`, using `P.tool(role, key,
inputs)` for a conversation read and `P.ok(feature, role, condition, observed)` for the line. Add its read tool name to
`INDIA_READ_TOOLS` so the role sweep and the client boundary check cover it. A new read function that writes (a cache, a
ledger row) will fail the run; call the underlying read instead.
