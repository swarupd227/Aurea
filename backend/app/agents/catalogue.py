"""Static catalogue metadata for the agent roster (spec §7.2, App. B) — used by the UI."""
from __future__ import annotations

from app.models.enums import AgentKey, AutonomyTier

CATALOGUE: dict[str, dict] = {
    AgentKey.ONBOARDING_KYC_AML: {
        "name": "Onboarding · KYC · AML", "stage": "Acquire & onboard",
        "default_tier": AutonomyTier.TIER_2, "subject": "onboarding_case",
        "senses": "New prospect or referral; intake form; document upload.",
        "acts": "Enriches the prospect, verifies documents, runs AML/CFT screening, drafts "
                "suitability & mandate set-up, surfaces exceptions.",
        "checkpoint": "Compliance signs off; escalates ambiguity rather than deciding it.",
    },
    AgentKey.BOOK_INTEGRATION: {
        "name": "Book Integration", "stage": "Acquire & onboard",
        "default_tier": AutonomyTier.TIER_2, "subject": "book_batch",
        "senses": "Acquisition of an advisory book; new data feed.",
        "acts": "Reconciles and maps client, account and holding data into the client brain; flags conflicts.",
        "checkpoint": "Operations validates mappings before they become golden records.",
    },
    AgentKey.NIGO_PREVENTION: {
        "name": "NIGO Prevention", "stage": "Acquire & onboard",
        "default_tier": AutonomyTier.TIER_2, "subject": "onboarding_case",
        "senses": "The case's registration type, its required-document matrix, extracted field "
                  "confidence, beneficial owners, and intake completeness.",
        "acts": "Runs the pre-submission check before a package is sent to the custodian: scores "
                "readiness 0–100, and itemises every issue against the NIGO root-cause taxonomy "
                "(missing ID, missing beneficiary, incomplete beneficial ownership, stale form).",
        "checkpoint": "Adviser or operations clears each issue before submission; the agent "
                      "prevents rejects rather than recording them after the fact.",
    },
    AgentKey.ADVERSE_MEDIA_PEP: {
        "name": "Adverse Media & PEP Screener", "stage": "Acquire & onboard",
        "default_tier": AutonomyTier.TIER_2, "subject": "onboarding_case",
        "senses": "Every party on the case — applicant, associated parties, trustees and "
                  "beneficial owners — screened against sanctions, PEP and adverse-media lists.",
        "acts": "Writes a per-party disposition log with match scores and rationale, and raises "
                "EDD where a PEP or adverse-media hit is found. Designed to re-run as lists "
                "update rather than screening once at intake.",
        "checkpoint": "Compliance dispositions each true/false match with documented rationale.",
    },
    AgentKey.EDD_SOW_NARRATOR: {
        "name": "EDD Source-of-Wealth Narrator", "stage": "Acquire & onboard",
        "default_tier": AutonomyTier.TIER_2, "subject": "onboarding_case",
        "senses": "Intake source-of-wealth and source-of-funds statements, supporting documents, "
                  "and the case's AML risk tier.",
        "acts": "Drafts the structured source-of-wealth memorandum required for enhanced due "
                "diligence on medium and high-risk customers, and lists the corroboration gaps "
                "that still need documentary support.",
        "checkpoint": "Compliance reviews and signs off the narrative; the agent never closes EDD itself.",
    },
    AgentKey.ROLLOVER_PTE_DOCUMENTER: {
        "name": "Rollover PTE 2020-02 Documenter", "stage": "Acquire & onboard",
        "default_tier": AutonomyTier.TIER_2, "subject": "onboarding_case",
        "senses": "Rollover-type registrations, the leaving plan's fee and investment features, "
                  "and the proposed IRA advisory fee.",
        "acts": "Generates the DOL PTE 2020-02 best-interest rationale memo documenting why the "
                "rollover benefits the client, with the plan-versus-IRA fee comparison in basis "
                "points. Missing rollover rationale is a standing DOL and SEC exam focus.",
        "checkpoint": "Adviser reviews and adopts the rationale; it is a fiduciary representation.",
    },
    AgentKey.ABANDONMENT_RECOVERY: {
        "name": "Abandonment Recovery", "stage": "Acquire & onboard",
        "default_tier": AutonomyTier.TIER_1, "subject": "firm",
        "senses": "Onboarding cases that have stalled — open past their SLA with no recent "
                  "progress on documents, screening or funding.",
        "acts": "Identifies stalled applications, attributes the stall to its blocking step, and "
                "drafts a re-engagement approach for the owning adviser.",
        "checkpoint": "Adviser decides whether and how to re-approach the prospect.",
    },
    AgentKey.CIP_IDENTITY_VERIFIER: {
        "name": "CIP Identity Verifier", "stage": "Acquire & onboard",
        "default_tier": AutonomyTier.TIER_2, "subject": "onboarding_case",
        "senses": "Name, date of birth, address and identity-document number for each natural "
                  "person on the case.",
        "acts": "Runs the Customer Identification Program check through the configured identity "
                "provider, returning a verification decision, a confidence score, and any "
                "mismatch flags against the provider's audit reference.",
        "checkpoint": "Operations reviews review/failed outcomes; a passing CIP is a precondition "
                      "for opening the custodian account.",
    },
    AgentKey.CUSTODIAN_ACCOUNT_OPENER: {
        "name": "Custodian Account Opener", "stage": "Acquire & onboard",
        "default_tier": AutonomyTier.TIER_2, "subject": "onboarding_case",
        "senses": "The approved case's registration type, party data, and verified identity record.",
        "acts": "Single-keys the captured data to the custodian's account-opening API and records "
                "the returned account number — so the same details are never re-entered by hand, "
                "which is the root cause of most NIGO and cycle-time pain.",
        "checkpoint": "Operations approves before an external account is created at the custodian.",
    },
    AgentKey.MEETING_PREP: {
        "name": "Meeting Preparation", "stage": "Advise & engage",
        "default_tier": AutonomyTier.TIER_1, "subject": "household",
        "senses": "Upcoming meeting in calendar/CRM.",
        "acts": "Assembles a prepared brief: portfolio, performance, plan changes, life events, house views, topics.",
        "checkpoint": "Adviser owns the conversation; brief is a starting point.",
    },
    AgentKey.MEETING_COMPANION: {
        "name": "Meeting Companion & Note", "stage": "Advise & engage",
        "default_tier": AutonomyTier.TIER_1, "subject": "household",
        "senses": "Live or recorded client meeting.",
        "acts": "Captures notes, drafts follow-ups, updates CRM, proposes tasks.",
        "checkpoint": "Adviser approves notes and any actions before they take effect.",
    },
    AgentKey.RESEARCH_REPORTING: {
        "name": "Research & Reporting", "stage": "Advise & engage",
        "default_tier": AutonomyTier.TIER_1, "subject": "household",
        "senses": "Reporting cycle; ad-hoc request; market event.",
        "acts": "Drafts client-ready performance summaries and commentary grounded in firm research.",
        "checkpoint": "Adviser/research reviews before anything is client-facing.",
    },
    AgentKey.DRIFT_REBALANCING: {
        "name": "Drift & Tax-Managed Rebalancing", "stage": "Manage & optimise",
        "default_tier": AutonomyTier.TIER_2, "subject": "mandate", "lighthouse": True,
        "senses": "Continuous monitoring of public and private holdings vs target.",
        "acts": "Runs whole-portfolio, tax-aware optimisation (lot selection, CGT budgets, loss "
                "harvesting); drafts plain-language rationale and a multi-custodian order set.",
        "checkpoint": "Adviser approves / modifies / dismisses before anything reaches the OMS.",
    },
    AgentKey.NEXT_BEST_ACTION: {
        "name": "Next-Best-Action & Growth", "stage": "Manage & optimise",
        "default_tier": AutonomyTier.TIER_1, "subject": "household",
        "senses": "Life-stage, tax, market, anomaly and relationship signals.",
        "acts": "Pushes prioritised, explainable opportunities, risks and anomalies to the adviser.",
        "checkpoint": "Adviser chooses whether and how to act.",
    },
    AgentKey.CLIENT_CARE: {
        "name": "Client Care & Retention", "stage": "Manage & optimise",
        "default_tier": AutonomyTier.TIER_1, "subject": "household",
        "senses": "Volatility; milestones; heir engagement gaps; at-risk signals.",
        "acts": "Proposes proactive, personalised outreach — stress-grounded during market stress.",
        "checkpoint": "Adviser approves outreach; Tier 3 only for pre-approved, low-risk comms.",
    },
    AgentKey.CONDUCT_SURVEILLANCE: {
        "name": "Conduct Surveillance", "stage": "Protect & govern",
        "default_tier": AutonomyTier.TIER_2, "subject": "firm",
        "senses": "Every recommendation and client communication generated on the platform.",
        "acts": "Monitors for suitability, conduct and fair-treatment risk; supervises other agents; "
                "writes to the decision ledger.",
        "checkpoint": "Flags route to compliance; can pause an agent's autonomy automatically.",
    },
    AgentKey.ESTATE_SUCCESSION: {
        "name": "Estate & Succession Planning", "stage": "Manage & optimise",
        "default_tier": AutonomyTier.TIER_1, "subject": "household",
        "senses": "Full family wealth picture: trust structures, heir readiness, private-asset concentration, "
                  "legacy goals, and NZ Trusts Act 2019 governance obligations.",
        "acts": "Identifies succession gaps, scores trust governance completeness, flags heir mandate gaps, "
                "and produces an estate health memo for the next adviser–client conversation.",
        "checkpoint": "Adviser reviews gaps and memo before raising in client meeting; no external effect.",
    },
    AgentKey.BEHAVIOURAL_FINANCE: {
        "name": "Behavioural Finance", "stage": "Advise & engage",
        "default_tier": AutonomyTier.TIER_1, "subject": "household",
        "senses": "Recommendation decision history (approve / modify / dismiss patterns), meeting transcripts, "
                  "and client messages for recency bias, anchoring, loss-aversion, and status quo signals. "
                  "Also monitors portfolio drawdown against the stress-playbook threshold.",
        "acts": "Builds a cognitive bias profile per client, detects behavioural signals in text, "
                "drafts a personalised stress-playbook message when drawdown exceeds threshold, "
                "and surfaces adviser coaching framing tailored to the dominant bias.",
        "checkpoint": "Adviser reviews behavioural memo and coaching advice before raising in client meeting; "
                      "stress message requires adviser approval before sending.",
    },
    AgentKey.TAX_INTELLIGENCE: {
        "name": "Tax Intelligence", "stage": "Manage & optimise",
        "default_tier": AutonomyTier.TIER_1, "subject": "household",
        "senses": "All mandates, TaxLots, current prices, and client tax profiles (marginal rate, PIR, "
                  "KiwiSaver rate, property acquisition dates) across the full book.",
        "acts": "Scans for loss-harvesting opportunities, PIE fund mismatches, KiwiSaver contribution gaps, "
                "bright-line property thresholds, and suboptimal withdrawal sequencing. "
                "Produces a per-household tax intelligence memo ranked by saving potential.",
        "checkpoint": "Adviser reviews memo before any restructure or client conversation; no external effect.",
    },
    AgentKey.REGULATORY_COUNTDOWN: {
        "name": "Regulatory Countdown", "stage": "Protect & govern",
        "default_tier": AutonomyTier.TIER_1, "subject": "household",
        "senses": "Household jurisdiction, estate values, retirement account balances, pension pot values, "
                  "and proximity to critical regulatory deadlines (US estate tax sunset Jan 2026, "
                  "UK IHT pension inclusion April 2027).",
        "acts": "Surfaces time-critical adviser alerts ranked by urgency and dollar impact. "
                "Proposes specific planning actions (SLATs, GRATs, pension drawdown, whole-of-life in trust) "
                "before regulatory deadlines make planning impossible or more costly.",
        "checkpoint": "Adviser reviews alerts and initiates client conversation; no external effect.",
    },
    AgentKey.IPS_DRAFTING: {
        "name": "IPS Drafter", "stage": "Advise & engage",
        "default_tier": AutonomyTier.TIER_2, "subject": "household",
        "senses": "Full client brain — goals, mandate constraints, suitability questionnaire, "
                  "risk profile, tax situation, legal entities, and unique preferences.",
        "acts": "Generates a formal Investment Policy Statement (IPS) using the RRTTLLU framework: "
                "Return objective, Risk tolerance & capacity, Time horizon, Tax situation, "
                "Liquidity needs, Legal constraints, Unique preferences. "
                "Includes target allocation, benchmark, and rebalancing policy.",
        "checkpoint": "Adviser reviews and edits the draft IPS before delivery to the client. "
                      "The IPS is an advisory-relationship document — human authorship is required.",
    },
    AgentKey.ASSET_LOCATION: {
        "name": "Asset Location Optimizer", "stage": "Manage & optimise",
        "default_tier": AutonomyTier.TIER_2, "subject": "household",
        "senses": "All holdings across all accounts (taxable, IRA/RRSP, Roth, trust, ISA) "
                  "with asset class, account type, and tax lot information.",
        "acts": "Identifies tax-inefficient asset placement — e.g. income-heavy bonds in taxable accounts, "
                "growth equities in tax-deferred accounts. Produces a repositioning proposal that minimises "
                "tax drag without generating unnecessary capital-gains events.",
        "checkpoint": "Adviser approves before any repositioning is communicated to the portfolio team or OMS.",
    },
    AgentKey.WALLET_SHARE_SCOUT: {
        "name": "Wallet Share Scout", "stage": "Manage & optimise",
        "default_tier": AutonomyTier.TIER_1, "subject": "household",
        "senses": "Held-away asset data per person, managed AUM, household life stage, "
                  "goals, and estate situation.",
        "acts": "Surfaces consolidation opportunities ranked by held-away magnitude. "
                "Generates personalised talking-point memos calibrated to each household's "
                "life stage, goals, and estate situation — not generic boilerplate.",
        "checkpoint": "Adviser uses talking points at the next review meeting; no external action.",
    },
    AgentKey.GLIDE_PATH: {
        "name": "Glide Path Advisor", "stage": "Manage & optimise",
        "default_tier": AutonomyTier.TIER_2, "subject": "household",
        "senses": "Household retirement goals (target date, target amount, current probability), "
                  "current equity allocation, and behavioural risk profile.",
        "acts": "For households within 7 years of a major goal, models the recommended "
                "equity step-down schedule per year. Flags households that are over-risked for "
                "their timeline and generates a de-risking rebalancing proposal.",
        "checkpoint": "Adviser reviews and approves the proposed allocation shift before acting.",
    },
    AgentKey.REVERSE_CHURNING: {
        "name": "Reverse Churning Detector", "stage": "Protect & govern",
        "default_tier": AutonomyTier.TIER_2, "subject": "firm",
        "senses": "All households on AUM-based fee mandates and their advisory activity history "
                  "(meeting dates, recommendation dates, task dates).",
        "acts": "Flags clients on ongoing advisory fees with no documented advisory activity "
                "in 12+ months. Generates a surveillance flag (conduct risk) and drafts a "
                "suggested check-in agenda for each flagged household.",
        "checkpoint": "Compliance reviews flags; adviser initiates re-engagement.",
    },
}
