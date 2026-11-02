"""The customer risk rating model (L200-1 §5.3, §5.4, §5.6).

A rating turns facts about a client into a score, and the score into consequences. The facts are recorded by a
person: where the client lives and holds citizenship, where the money comes from, what kind of wealth it is, how the
relationship was formed, and the activity expected, which is the baseline monitoring later measures against. A
factor with no fact on file reads "not recorded" and scores nothing, and a rating is refused until the facts the
model needs are recorded, because a rating made on blanks reads as low risk.

    geography    a country on the FATF call-for-action list 40; on the firm's own monitored list 20;
                 a residence outside the firm's market 10
    client type  trust 10, entity 15, estate 5, with 15 more for a layered or offshore structure
    wealth       cash-intensive business or crypto 25, other 10, inheritance, business or property sale 5
    screening    a confirmed PEP 30, confirmed adverse media 15
    activity     more than 24 wires a year 10, payments to or from third parties expected 10
    channel      formed without meeting face to face 10

Under 25 is low, under 50 medium, otherwise high. The band sets the depth of due diligence, who approves, and how
often the client is reviewed: low every 4 years, medium every 2, high every year, plus when something changes.
Designed paths raise the floor and say what more is needed: a PEP is never below high. A person may override the
band either way, with a reason, and the override is kept beside what the model said; an override below a path's
floor is refused. The model is versioned, and its weights are here to be read and validated.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, markets
from app.aurea_core.reviews import add_months
from app.models.aml_risk import AmlRiskRating
from app.models.enums import EDDStatus, UserRole
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.models.tenant import Firm
from app.provenance import ledger

MODEL_VERSION = "1.0"
BANDS = ("low", "medium", "high")
# The FATF "call for action" list, as published in 2025. A firm keeps its own monitored list in its settings
# (`aml_monitored_countries`), because grey lists move and a list fixed in code goes stale.
FATF_CALL_FOR_ACTION = {"KP", "IR", "MM"}
WEALTH = {"employment": 0, "investments": 0, "equity_compensation": 0, "inheritance": 5, "business_sale": 5, "property_sale": 5,
          "cash_business": 25, "crypto": 25, "other": 10}
CHANNELS = ("face_to_face", "remote")
REVIEW_MONTHS = {"low": 48, "medium": 24, "high": 12}
CONSEQUENCES = {
    "low": {"due_diligence": "CDD", "approver": "Operations or compliance"},
    "medium": {"due_diligence": "EDD, signed off by operations or compliance", "approver": "Compliance"},
    "high": {"due_diligence": "EDD, signed off by compliance", "approver": "Compliance, with senior approval"},
}
REQUIRED = ("residence_country", "wealth_source", "channel")
RATERS = {UserRole.COMPLIANCE, UserRole.OPERATIONS}


class AmlRiskError(ValueError):
    """This cannot be rated, with the reason."""


def _band(score: float) -> str:
    return "low" if score < 25 else "medium" if score < 50 else "high"


def _country(value) -> str | None:
    text = str(value or "").strip().upper()
    return markets.code(text) if text else None


def score(facts: dict, *, registration_type: str | None, firm_country: str | None, monitored: set[str], pep: bool, adverse: bool) -> dict:
    """The model on one set of facts. Pure."""
    factors, missing = [], [k for k in REQUIRED if not facts.get(k)]

    def add(name: str, points: float, why: str) -> None:
        factors.append({"factor": name, "points": points, "why": why})

    countries = {c for c in (_country(facts.get("residence_country")), _country(facts.get("citizenship")),
                             _country(facts.get("source_of_funds_country"))) if c}
    if countries & FATF_CALL_FOR_ACTION:
        add("geography", 40, f"{', '.join(sorted(countries & FATF_CALL_FOR_ACTION))} is on the FATF call-for-action list")
    elif countries & monitored:
        add("geography", 20, f"{', '.join(sorted(countries & monitored))} is on the firm's monitored list")
    elif facts.get("residence_country") and firm_country and _country(facts["residence_country"]) != markets.code(firm_country):
        add("geography", 10, "Lives outside the firm's market")
    elif facts.get("residence_country"):
        add("geography", 0, "Lives in the firm's market")
    reg = registration_type or "individual"
    base = 10 if reg == "trust" else 15 if reg.startswith("entity") else 5 if reg.startswith("estate") else 0
    if base:
        add("client_type", base, reg.replace("_", " ").capitalize())
    if facts.get("layered"):
        add("client_type", 15, "Layered or offshore structure")
    wealth = str(facts.get("wealth_source") or "").strip().lower()
    if wealth:
        add("wealth", WEALTH.get(wealth, WEALTH["other"]), wealth.replace("_", " ").capitalize())
    if pep:
        add("screening", 30, "Confirmed politically exposed person")
    if adverse:
        add("screening", 15, "Confirmed adverse media")
    wires = facts.get("wires_per_year")
    if wires is not None and float(wires) > 24:
        add("activity", 10, f"{int(float(wires))} wires a year expected")
    if facts.get("third_party_payments"):
        add("activity", 10, "Payments to or from third parties expected")
    if facts.get("channel") == "remote":
        add("channel", 10, "Relationship formed without meeting face to face")
    total = float(sum(f["points"] for f in factors))
    paths = []
    if countries & FATF_CALL_FOR_ACTION:
        paths.append({"path": "call_for_action_country", "floor": "high", "requires": ["Senior approval", "Enhanced measures the FATF calls for", "Whether the firm may act at all"]})
    if pep:
        paths.append({"path": "pep", "floor": "high", "requires": ["Senior approval", "Source of wealth corroborated", "Annual refresh"]})
    if facts.get("residence_country") and firm_country and _country(facts["residence_country"]) != markets.code(firm_country):
        needs = ["W-8BEN on file", "Treaty rate confirmed", "What may be sold where they live"] if markets.code(firm_country) == "US" else ["Tax residence self-certification", "What may be sold where they live"]
        paths.append({"path": "non_resident", "floor": "medium", "requires": needs})
    if facts.get("layered"):
        paths.append({"path": "layered_structure", "floor": "medium", "requires": ["Ownership unwrapped to the people", "Organogram on file", "Situs and tax classification"]})
    if wealth == "crypto":
        paths.append({"path": "crypto", "floor": "high", "requires": ["The firm's crypto acceptance policy met", "Exchange statements", "Wallet ownership evidence"]})
    if wealth == "cash_business":
        paths.append({"path": "cash_business", "floor": "medium", "requires": ["Expected activity set precisely", "Structuring watched"]})
    model_band = _band(total)
    floor = max((p["floor"] for p in paths), key=BANDS.index, default="low")
    if BANDS.index(floor) > BANDS.index(model_band):
        model_band = floor
    return {"factors": factors, "score": total, "model_band": model_band, "floor": floor, "paths": paths, "missing": missing,
            "model_version": MODEL_VERSION}


async def _screening_facts(session: AsyncSession, case_id: uuid.UUID) -> tuple[bool, bool]:
    from app.aurea_core import aml_review

    parties = (await session.execute(select(OnboardingParty).where(OnboardingParty.case_id == case_id))).scalars().all()
    decided = await aml_review.decisions_for(session, [p.id for p in parties])
    pep = adverse = False
    for p in parties:
        for h in aml_review.party_state(p, decided.get(p.id, []))["hits"]:
            if h["state"] == "true_match":
                pep = pep or h.get("category") == "pep"
                adverse = adverse or h.get("category") == "adverse_media"
    return pep, adverse


def _facts_from(inputs: dict, on_file: dict) -> dict:
    facts = dict(on_file)
    for key in ("residence_country", "citizenship", "source_of_funds_country", "wealth_source", "channel", "wires_per_year",
                "expected_annual_inflow", "third_party_payments", "layered"):
        if inputs.get(key) not in (None, ""):
            facts[key] = inputs[key]
    # A yes or no can arrive as text; "false" is not a yes.
    for key in ("third_party_payments", "layered"):
        if isinstance(facts.get(key), str):
            facts[key] = facts[key].strip().lower() in ("true", "yes", "y", "1")
    return facts


async def assess(session: AsyncSession, case_id: uuid.UUID, *, firm_id: uuid.UUID, facts: dict | None = None) -> dict:
    """What the model says for a case, on its recorded facts and any given now. Writes nothing."""
    case = await session.get(OnboardingCase, case_id)
    if case is None or case.firm_id != firm_id:
        raise AmlRiskError("Case not found.")
    firm = await session.get(Firm, firm_id)
    merged = _facts_from(facts or {}, (case.intake or {}).get("cdd") or {})
    if merged.get("channel") and merged["channel"] not in CHANNELS:
        raise AmlRiskError("The channel is face_to_face or remote.")
    if merged.get("wealth_source") and str(merged["wealth_source"]).lower() not in WEALTH:
        raise AmlRiskError(f"The wealth source is one of {', '.join(WEALTH)}.")
    pep, adverse = await _screening_facts(session, case.id)
    monitored = {markets.code(c) for c in ((firm.settings or {}).get("aml_monitored_countries") or [])}
    result = score(merged, registration_type=case.registration_type, firm_country=firm.jurisdiction, monitored=monitored, pep=pep, adverse=adverse)
    last = (await session.execute(select(AmlRiskRating).where(AmlRiskRating.case_id == case.id).order_by(AmlRiskRating.created_at.desc()).limit(1))).scalars().first()
    return {**result, "case": case, "facts": merged, "consequences": CONSEQUENCES[result["model_band"]], "last": last}


async def prepare_rating(session: AsyncSession, case_id: uuid.UUID, *, firm_id: uuid.UUID, role, facts: dict | None = None,
                         band: str | None = None, reason: str | None = None) -> dict:
    if role not in RATERS:
        raise AmlRiskError("Compliance or operations rates a client's risk.")
    a = await assess(session, case_id, firm_id=firm_id, facts=facts)
    if a["missing"]:
        raise AmlRiskError("Record these before rating: " + ", ".join(m.replace("_", " ") for m in a["missing"]) + ".")
    final = (band or a["model_band"]).strip().lower()
    if final not in BANDS:
        raise AmlRiskError("The band is low, medium or high.")
    why = (reason or "").strip()
    if final != a["model_band"]:
        if BANDS.index(final) < BANDS.index(a["floor"]):
            floors = ", ".join(p["path"].replace("_", " ") for p in a["paths"] if p["floor"] == a["floor"])
            raise AmlRiskError(f"A {floors} client is never rated below {a['floor']}.")
        if len(why) < 10:
            raise AmlRiskError(f"The model says {a['model_band']}. An override needs the reason, which is kept beside it.")
    if final == "high" and role != UserRole.COMPLIANCE:
        raise AmlRiskError("A high rating is compliance's.")
    return {**a, "band": final, "override": final != a["model_band"], "reason": why or None}


async def record_rating(session: AsyncSession, case_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, role, **terms) -> dict:
    ready = await prepare_rating(session, case_id, firm_id=firm_id, role=role, **terms)
    case, firm = ready["case"], await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    months = REVIEW_MONTHS[ready["band"]]
    case.intake = {**(case.intake or {}), "cdd": ready["facts"]}
    row = AmlRiskRating(firm_id=firm_id, case_id=case.id, model_version=MODEL_VERSION, facts=ready["facts"], factors=ready["factors"],
                        score=ready["score"], model_band=ready["model_band"], band=ready["band"], override_reason=ready["reason"] if ready["override"] else None,
                        paths=ready["paths"], review_months=months, next_review_on=add_months(today, months), rated_by=by, rated_by_role=str(role))
    session.add(row)
    case.aml_risk_tier, case.aml_risk_score = ready["band"], ready["score"]
    if ready["band"] in ("medium", "high") and case.edd_status != EDDStatus.EDD_COMPLETE.value:
        case.edd_status = EDDStatus.EDD_PENDING.value
    elif ready["band"] == "low" and case.edd_status in (None, "", "none", EDDStatus.EDD_PENDING.value) and not ready["paths"]:
        case.edd_status = "cdd"
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="aml_risk_rated", actor=by, content={
        "case_id": str(case.id), "model_version": MODEL_VERSION, "score": ready["score"], "model_band": ready["model_band"], "band": ready["band"],
        "override_reason": row.override_reason, "factors": ready["factors"]})
    return view(ready, row)


def view(a: dict, row: AmlRiskRating | None = None) -> dict:
    last = row or a.get("last")
    return {"case_id": str(a["case"].id), "case": a["case"].prospect_name, "model_version": a["model_version"], "facts": a["facts"],
            "factors": a["factors"], "score": a["score"], "model_band": a["model_band"], "paths": a["paths"], "missing": a["missing"],
            "consequences": a["consequences"], "rating": {
                "band": last.band, "model_band": last.model_band, "override_reason": last.override_reason, "rated_by": last.rated_by,
                "next_review_on": last.next_review_on.isoformat(), "review_months": last.review_months} if last else None}


async def reviews_due(session: AsyncSession, firm_id: uuid.UUID, *, today: date) -> list[dict]:
    """Activated clients whose periodic risk review has come due, from their latest rating."""
    rows = (await session.execute(select(AmlRiskRating).where(AmlRiskRating.firm_id == firm_id).order_by(AmlRiskRating.created_at))).scalars().all()
    latest: dict = {}
    for r in rows:
        latest[r.case_id] = r
    out = []
    for case_id, r in latest.items():
        if r.next_review_on <= today:
            case = await session.get(OnboardingCase, case_id)
            out.append({"case_id": str(case_id), "case": case.prospect_name if case else None, "band": r.band, "due": r.next_review_on.isoformat()})
    return out
