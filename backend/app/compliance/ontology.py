"""The regulatory ontology — versioned regime frameworks of advice obligations.

Each `Rule` is a machine-readable obligation with a real statutory/code citation, a category, a
default severity, the agents/actions it applies to, and the key of a deterministic evaluator
(`rules.py`). Frameworks are versioned so the ledger can prove which body of rules applied. Citations
are mapped illustratively and are configurable per firm — a firm tunes severities and scope to its
own compliance posture (see the Admin → Regulatory console)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    id: str            # stable identifier, e.g. "code.std6.guardrails"
    code: str          # short citation handle shown in the UI, e.g. "Code Std 6"
    citation: str      # full citation
    title: str         # plain-language obligation
    category: str      # suitability | conduct | disclosure | best_interest | aml | tax | records
    severity: str      # high | medium | low  (default; firm-overridable)
    applies_to: tuple[str, ...]  # agent keys, or ("*",) for every agent
    eval: str          # evaluator key in rules.REGISTRY
    description: str = ""


@dataclass(frozen=True)
class Framework:
    regime: str        # NZ-FMA
    name: str
    version: str
    effective: str     # ISO date the version took effect
    authority: str
    rules: tuple[Rule, ...]


# ── New Zealand — Financial Markets Conduct Act + FAP Code of Conduct + AML/CFT ───────────────────
NZ_FMA = Framework(
    regime="NZ-FMA",
    name="Financial Markets Conduct — Regulated Financial Advice",
    version="2026.1",
    effective="2026-01-01",
    authority="Financial Markets Authority (New Zealand)",
    rules=(
        Rule("fmc.s22.no_unsubstantiated", "FMC Act s22",
             "Financial Markets Conduct Act 2013, s22 — Unsubstantiated representations",
             "No misleading or unsubstantiated representations", "conduct", "medium", ("*",),
             "no_overpromise", "Client-facing statements must not present outcomes as guaranteed."),
        Rule("code.std1.fair_dealing", "Code Std 1",
             "Code of Professional Conduct for Financial Advice Services, Standard 1 — Treat clients fairly",
             "Treat clients fairly (no excessive trading)", "conduct", "medium",
             ("drift_rebalancing", "next_best_action"), "turnover",
             "Avoid churning — turnover must stay within the firm's fair-dealing limit."),
        Rule("code.std3.understandable", "Code Std 3",
             "Code Standard 3 — Ensure the client understands the financial advice",
             "Advice is explainable to the client", "disclosure", "medium", ("*",),
             "explainable", "A plain-language rationale must accompany every recommendation."),
        Rule("code.std6.suitability", "Code Std 6",
             "Code Standard 6 — Ensure the advice is suitable",
             "Advice suitable to the client's mandate", "suitability", "medium",
             ("drift_rebalancing", "next_best_action", "research_reporting"), "mandate_suitability",
             "The action must fall within the client's mandate type and scope."),
        Rule("code.std6.guardrails", "Code Std 6",
             "Code Standard 6 — Suitability (acting within agreed limits)",
             "Within mandate guardrails", "suitability", "high", ("*",), "within_guardrails",
             "No guardrail breach (position limits, eligibility, reconciliation) may remain unresolved."),
        Rule("best_interest.cgt_budget", "Code Std 5",
             "Code Standard 5 — Competence (tax-aware advice) & client's interests",
             "Realised gains within the client's CGT budget", "tax", "high",
             ("drift_rebalancing",), "cgt_budget",
             "Tax-managed: realised gains must not exceed the client's capital-gains budget."),
        Rule("code.std2.grounding", "Code Std 2",
             "Code Standard 2 — Act with integrity (advice on a proper basis)",
             "Investment advice grounded in firm research", "conduct", "low",
             ("drift_rebalancing", "research_reporting"), "grounding",
             "Investment advice should cite the firm's research basis."),
        Rule("code.std3.narrative_groundedness", "Code Std 3",
             "Code Standard 3 — Ensure the client understands the financial advice (rationale must describe the actual recommendation)",
             "Rationale grounded in the recommendation payload", "disclosure", "medium",
             ("drift_rebalancing", "next_best_action", "research_reporting"),
             "narrative_instrument_check",
             "The plain-language rationale must reference the asset classes and trade direction in the order set, not a generic or disconnected narrative."),
        Rule("code.std6.data_confidence", "Code Std 6",
             "Code Standard 6 — Suitability (reliable information)",
             "Decision confidence above the firm threshold", "suitability", "medium", ("*",),
             "confidence", "Advice must rest on sufficiently reliable, reconciled data."),
        Rule("aml.cft.cdd", "AML/CFT Act ss11–31",
             "Anti-Money Laundering and Countering Financing of Terrorism Act 2009 — Customer due "
             "diligence, PEP & sanctions screening",
             "AML/CFT screening cleared or escalated", "aml", "high",
             ("onboarding_kyc_aml", "book_integration"), "aml_screening",
             "No sanctions match; PEP / adverse-media hits escalated for enhanced due diligence."),
        Rule("disclosure.material", "Disclosure Regs 2020",
             "Financial Markets Conduct (Regulated Financial Advice Disclosure) Amendment Regulations "
             "2020 — Disclosure of material information",
             "Material limitations disclosed", "disclosure", "low", ("*",), "disclosure",
             "Any material limitation or guardrail breach is surfaced to the adviser and recorded."),
        Rule("fmc.s446.records", "FMC Act s446",
             "Financial Markets Conduct Act 2013, s446 — Record-keeping obligations",
             "Decision written to the tamper-evident ledger", "records", "low", ("*",), "records",
             "Every recommendation is recorded in the hash-chained decision ledger."),
    ),
)

# ── United Kingdom — FCA COBS + Consumer Duty (shows the engine is multi-regime) ──────────────────
UK_FCA = Framework(
    regime="UK-FCA",
    name="FCA Conduct of Business + Consumer Duty",
    version="2026.1",
    effective="2026-01-01",
    authority="Financial Conduct Authority (United Kingdom)",
    rules=(
        Rule("cobs.4.fair_clear", "COBS 4.2",
             "FCA Handbook COBS 4.2 — Fair, clear and not misleading communications",
             "Communications fair, clear and not misleading", "conduct", "medium", ("*",),
             "no_overpromise"),
        Rule("cobs.9a.narrative_groundedness", "COBS 9A",
             "FCA Handbook COBS 9A — Suitability of advice (rationale must reflect the actual recommendation)",
             "Rationale grounded in the recommendation payload", "disclosure", "medium",
             ("drift_rebalancing", "next_best_action", "research_reporting"),
             "narrative_instrument_check"),
        Rule("cobs.9a.suitability", "COBS 9A",
             "FCA Handbook COBS 9A — Suitability of advice",
             "Advice suitable to the client's mandate", "suitability", "high",
             ("drift_rebalancing", "next_best_action", "research_reporting"), "mandate_suitability"),
        Rule("prin.consumer_duty", "PRIN 2A",
             "FCA Principle 12 / PRIN 2A — Consumer Duty (act to deliver good outcomes, avoid foreseeable harm)",
             "No foreseeable harm — within agreed guardrails", "best_interest", "high", ("*",),
             "within_guardrails"),
        Rule("cobs.records", "SYSC 9",
             "FCA Handbook SYSC 9 — Record-keeping",
             "Decision written to the tamper-evident ledger", "records", "low", ("*",), "records"),
    ),
)

# ── United States — SEC + FINRA + DOL ─────────────────────────────────────────────────────────────
US_SEC = Framework(
    regime="US-SEC",
    name="SEC Regulation Best Interest + Advisers Act + FINRA",
    version="2026.1",
    effective="2026-01-01",
    authority="U.S. Securities and Exchange Commission / FINRA / DOL",
    rules=(
        Rule("us.regbi.best_interest", "Reg BI",
             "SEC Regulation Best Interest — Securities Exchange Act Rule 15l-1",
             "Act in the retail client's best interest", "best_interest", "high", ("*",),
             "within_guardrails"),
        Rule("us.advisers_act.fiduciary", "Advisers Act s206",
             "Investment Advisers Act of 1940, s206 — fiduciary duty of care and loyalty",
             "Adviser fiduciary duty (care & loyalty)", "suitability", "high",
             ("drift_rebalancing", "next_best_action", "research_reporting"), "mandate_suitability"),
        Rule("us.finra.2111", "FINRA 2111",
             "FINRA Rule 2111 — Suitability (reasonable-basis from reliable information)",
             "Reasonable-basis suitability", "suitability", "high",
             ("drift_rebalancing", "next_best_action"), "confidence"),
        Rule("us.finra.2210", "FINRA 2210",
             "FINRA Rule 2210 — Communications with the public (no exaggerated, unwarranted or "
             "promissory statements)", "Fair & balanced communications", "conduct", "medium", ("*",),
             "no_overpromise"),
        Rule("us.regbi.narrative_groundedness", "Reg BI / Advisers Act",
             "SEC Regulation Best Interest / Advisers Act s206 — Rationale must reflect the specific advice given",
             "Rationale grounded in the recommendation payload", "disclosure", "medium",
             ("drift_rebalancing", "next_best_action", "research_reporting"),
             "narrative_instrument_check"),
        Rule("us.regbi.disclosure", "Reg BI / Form CRS",
             "Reg BI Disclosure Obligation; Form ADV Part 3 (Form CRS)",
             "Relationship & material facts disclosed", "disclosure", "low", ("*",), "disclosure"),
        Rule("us.dol.pte2020_02", "DOL PTE 2020-02",
             "DOL Prohibited Transaction Exemption 2020-02; ERISA impartial-conduct standards",
             "Impartial conduct on retirement advice", "best_interest", "high",
             ("next_best_action", "client_care", "onboarding_kyc_aml"), "within_guardrails"),
        Rule("us.bsa.aml", "BSA / OFAC",
             "Bank Secrecy Act & USA PATRIOT Act; FinCEN CDD Rule; OFAC sanctions screening",
             "AML/CFT & sanctions screening", "aml", "high",
             ("onboarding_kyc_aml", "book_integration"), "aml_screening"),
        Rule("us.irc.1091_wash_sale", "IRC §1091",
             "Internal Revenue Code §1091 — Wash sales of stock or securities",
             "No wash sale on loss harvesting", "tax", "medium", ("drift_rebalancing",), "wash_sale"),
        Rule("us.sec.204_2", "SEC 204-2",
             "Investment Advisers Act Rule 204-2 — Books and records",
             "Decision recorded", "records", "low", ("*",), "records"),
        Rule("us.reg_sp.privacy", "Reg S-P",
             "SEC Regulation S-P — Privacy of consumer financial information",
             "Client data protected", "disclosure", "low", ("*",), "privacy"),
    ),
)

# ── European Union — MiFID II + PRIIPs + SFDR + AMLD ───────────────────────────────────────────────
EU_MIFID = Framework(
    regime="EU-MiFID",
    name="MiFID II + PRIIPs + SFDR + AMLD",
    version="2026.1",
    effective="2026-01-01",
    authority="European Securities and Markets Authority (ESMA)",
    rules=(
        Rule("eu.mifid.art25_suitability", "MiFID II Art 25",
             "Directive 2014/65/EU (MiFID II), Art 25(2) — Suitability assessment",
             "Suitability assessment", "suitability", "high",
             ("drift_rebalancing", "next_best_action", "research_reporting"), "mandate_suitability"),
        Rule("eu.mifid.art24_3_fair", "MiFID II Art 24(3)",
             "MiFID II Art 24(3) — Fair, clear and not misleading communications",
             "Fair, clear and not misleading", "conduct", "medium", ("*",), "no_overpromise"),
        Rule("eu.mifid.narrative_groundedness", "MiFID II Art 25",
             "MiFID II Art 25(6) — Suitability statement must explain the recommendation and why it is suitable",
             "Rationale grounded in the recommendation payload", "disclosure", "medium",
             ("drift_rebalancing", "next_best_action", "research_reporting"),
             "narrative_instrument_check"),
        Rule("eu.mifid.art24_4_costs", "MiFID II Art 24(4)",
             "MiFID II Art 24(4) — Costs and charges disclosure",
             "Costs & charges disclosed", "disclosure", "low", ("drift_rebalancing",), "costs_disclosed"),
        Rule("eu.mifid.product_governance", "MiFID II Art 16(3)",
             "MiFID II Art 16(3) / 24(2) — Product governance & target market",
             "Within the product target market", "suitability", "medium",
             ("drift_rebalancing", "next_best_action"), "target_market"),
        Rule("eu.mifid.art27_best_ex", "MiFID II Art 27",
             "MiFID II Art 27 — Best execution",
             "Best execution", "conduct", "medium", ("drift_rebalancing",), "best_execution"),
        Rule("eu.mifid.art23_conflicts", "MiFID II Art 23",
             "MiFID II Art 23 — Conflicts of interest & inducements",
             "Conflicts & inducements managed", "conduct", "medium", ("*",), "within_guardrails"),
        Rule("eu.priips.kid", "PRIIPs Reg",
             "Regulation (EU) 1286/2014 (PRIIPs) — Key Information Document",
             "PRIIPs KID provided where required", "disclosure", "low", ("*",), "kid_provided"),
        Rule("eu.sfdr.sustainability", "SFDR",
             "Regulation (EU) 2019/2088 (SFDR) & MiFID II sustainability preferences",
             "Sustainability preferences respected", "disclosure", "low",
             ("drift_rebalancing",), "esg_preferences"),
        Rule("eu.amld5.cdd", "AMLD 5",
             "Directive (EU) 2018/843 (5th AML Directive); EU consolidated sanctions list",
             "AML/CFT & sanctions screening", "aml", "high",
             ("onboarding_kyc_aml", "book_integration"), "aml_screening"),
        Rule("eu.mifid.art16_6_records", "MiFID II Art 16(6)",
             "MiFID II Art 16(6) — Record-keeping",
             "Decision recorded", "records", "low", ("*",), "records"),
    ),
)

FRAMEWORKS: dict[str, Framework] = {f.regime: f for f in (NZ_FMA, UK_FCA, US_SEC, EU_MIFID)}

_EU_JURIS = {"EU", "EUROPE", "DE", "FR", "NL", "IE", "IT", "ES", "LU", "BE", "AT", "FI", "PT",
             "GERMANY", "FRANCE", "IRELAND", "NETHERLANDS", "LUXEMBOURG", "BELGIUM", "SPAIN", "ITALY"}


def framework_for(firm) -> Framework:
    """Resolve the regime for a firm from its jurisdiction / regulator (default NZ-FMA)."""
    reg = (getattr(firm, "regulator", "") or "").upper()
    juris = (getattr(firm, "jurisdiction", "") or "").upper()
    if "FCA" in reg or juris in ("UK", "GB", "UNITED KINGDOM"):
        return FRAMEWORKS["UK-FCA"]
    if "SEC" in reg or "FINRA" in reg or juris in ("US", "USA", "UNITED STATES"):
        return FRAMEWORKS["US-SEC"]
    if "ESMA" in reg or "MIFID" in reg or juris in _EU_JURIS:
        return FRAMEWORKS["EU-MiFID"]
    return FRAMEWORKS["NZ-FMA"]
