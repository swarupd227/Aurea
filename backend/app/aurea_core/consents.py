"""The consent registry, and disclosures kept up after opening (L200-1 §3).

Examiners and downstream systems both ask "show me the consent", so every consent sits in one queryable record:
e-delivery, householding, data aggregation, authority to contact a trusted contact, discretionary authority, and the
authority to debit fees. Each is recorded by a person from its evidence and dated the day it was given; a withdrawal
is a new record, and the latest stands. Discretion granted in a signed agreement shows as that agreement, not as a
consent nobody recorded.

Two gates read it. Delivering a disclosure by email or portal needs e-delivery consent on file, because electronic
delivery without it is not delivery. A fee schedule needs the authority to debit it. After opening, a US firm's
brochure and privacy notice fall due again a year after they were last delivered, and the registry lists them.
Nothing here is the client's to change: a client asks their adviser, who records it.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, markets
from app.models.consent_registry import INDIA_KINDS, KINDS, METHODS, ConsentRecord
from app.models.graph import Household
from app.models.onboarding import DisclosureDelivery, OnboardingCase
from app.models.tenant import Firm
from app.provenance import ledger

LABELS = {"e_delivery": "Electronic delivery", "householding": "Householding", "data_aggregation": "Data aggregation",
          "trusted_contact": "Contact a trusted contact", "discretionary_authority": "Discretionary authority", "fee_debit": "Fee debit",
          "advisory_agreement": "Advisory agreement signed (with MITC)", "ai_use_disclosure": "AI use disclosure received",
          "investor_charter": "Investor charter received", "risk_profile": "Risk profile consented",
          "non_sebi_declaration": "Non-SEBI products declaration"}
# How an Indian client's act is evidenced. The risk profile is consented "either through registered email or physical
# document" (IA Master Circular para 2.2), read here to include an e-signed document; the agreement is signed.
INDIA_METHODS = {"advisory_agreement": ("e_signature", "signed_form"),
                 "risk_profile": ("email", "signed_form", "e_signature"),
                 "non_sebi_declaration": ("e_signature", "signed_form", "email")}
ELECTRONIC = {"email", "portal"}
ANNUAL = {"US": ("form_adv_2a", "privacy_notice")}   # what falls due again each year after it was delivered


class ConsentError(ValueError):
    """This cannot be recorded, with the reason."""


def standing(rows: list) -> dict[str, ConsentRecord]:
    """The consent that stands for each kind: the latest given, by its date and then when it was recorded."""
    out: dict[str, ConsentRecord] = {}
    for r in sorted(rows, key=lambda r: (r.given_on, r.created_at)):
        out[r.kind] = r
    return out


def granted(rows: list) -> set[str]:
    return {k for k, r in standing(rows).items() if r.status == "granted"}


async def for_case_rows(session: AsyncSession, case: OnboardingCase) -> list:
    household = (case.materialized or {}).get("household_id")
    clauses = [ConsentRecord.case_id == case.id]
    if household:
        clauses.append(ConsentRecord.household_id == uuid.UUID(str(household)))
    return list((await session.execute(select(ConsentRecord).where(ConsentRecord.firm_id == case.firm_id, or_(*clauses)))).scalars().all())


def gate_status(rows: list, *, disclosure_items: list[dict], fee_assigned: bool) -> dict:
    """What the two consent gates need, from the consents on file and how disclosures went out."""
    have = granted(rows)
    electronic = [i["label"] for i in disclosure_items if i.get("delivered") and i.get("method") in ELECTRONIC]
    return {"granted": sorted(have), "electronic_without_consent": electronic if "e_delivery" not in have else [],
            "fee_debit_missing": bool(fee_assigned and "fee_debit" not in have)}


def _view(r: ConsentRecord) -> dict:
    return {"id": str(r.id), "kind": r.kind, "label": LABELS.get(r.kind, r.kind), "status": r.status, "given_by": r.given_by,
            "given_on": r.given_on.isoformat(), "method": r.method, "evidence": r.evidence, "recorded_by": r.recorded_by}


async def _redeliveries(session: AsyncSession, firm: Firm, cases: list, today: date) -> list[dict]:
    docs = ANNUAL.get(markets.code(firm.jurisdiction), ())
    if not docs or not cases:
        return []
    rows = (await session.execute(select(DisclosureDelivery).where(DisclosureDelivery.case_id.in_([c.id for c in cases]),
                                                                   DisclosureDelivery.doc_type.in_(docs)))).scalars().all()
    last: dict = {}
    for d in rows:
        key = (d.case_id, d.doc_type)
        if key not in last or d.delivered_at > last[key].delivered_at:
            last[key] = d
    names = {c.id: c.prospect_name for c in cases}
    out = []
    for (case_id, doc), d in last.items():
        due = firm_time.today(firm, d.delivered_at) + timedelta(days=365)
        if due <= today:
            out.append({"case_id": str(case_id), "account": names[case_id], "doc_type": doc, "last_delivered": firm_time.today(firm, d.delivered_at).isoformat(),
                        "due": due.isoformat()})
    return sorted(out, key=lambda r: r["due"])


async def registry(session: AsyncSession, firm_id: uuid.UUID, *, household_id: uuid.UUID | None = None, case_id: uuid.UUID | None = None) -> dict:
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    cases = (await session.execute(select(OnboardingCase).where(OnboardingCase.firm_id == firm_id))).scalars().all()
    if case_id is not None:
        cases = [c for c in cases if c.id == case_id]
        if not cases:
            raise ConsentError("Case not found.")
    if household_id is not None:
        household = await session.get(Household, household_id)
        if household is None or household.firm_id != firm_id:
            raise ConsentError("Household not found.")
        cases = [c for c in cases if (c.materialized or {}).get("household_id") == str(household_id)]
    query = select(ConsentRecord).where(ConsentRecord.firm_id == firm_id)
    if case_id is not None or household_id is not None:
        clauses = [ConsentRecord.case_id.in_([c.id for c in cases])] if cases else []
        if household_id is not None:
            clauses.append(ConsentRecord.household_id == household_id)
        query = query.where(or_(*clauses)) if clauses else query.where(ConsentRecord.id.is_(None))
    rows = (await session.execute(query.order_by(ConsentRecord.given_on.desc(), ConsentRecord.created_at.desc()))).scalars().all()
    stands = standing(list(rows)) if (case_id or household_id) else {}
    from_agreements = [{"kind": "discretionary_authority", "label": LABELS["discretionary_authority"], "status": "granted", "given_by": c.prospect_name,
                        "given_on": c.agreement_signed_at.date().isoformat(), "method": "e_signature", "evidence": f"Signed agreement {c.agreement_envelope_id or ''}".strip(),
                        "source": "agreement"} for c in cases if c.discretion_granted and c.agreement_signed_at]
    households = {h.id: h.name for h in (await session.execute(select(Household).where(
        Household.id.in_({r.household_id for r in rows if r.household_id})))).scalars().all()} if rows else {}
    names = {c.id: c.prospect_name for c in (await session.execute(select(OnboardingCase).where(OnboardingCase.firm_id == firm_id))).scalars().all()}
    records = [{**_view(r), "client": households.get(r.household_id) or names.get(r.case_id)} for r in rows]
    return {"records": records, "standing": {k: _view(v) for k, v in stands.items()} if stands else None,
            "from_agreements": from_agreements, "redeliveries_due": await _redeliveries(session, firm, [c for c in cases if c.activated_at], today),
            "kinds": [{"kind": k, "label": LABELS[k]} for k in kinds_for(firm)], "household_id": str(household_id) if household_id else None,
            "case_id": str(case_id) if case_id else None, "as_of": today.isoformat()}


def kinds_for(firm: Firm | None) -> tuple[str, ...]:
    """The kinds a firm records: India's own only for a firm in India."""
    if markets.code(getattr(firm, "jurisdiction", None)) == "IN":
        return KINDS
    return tuple(k for k in KINDS if k not in INDIA_KINDS)


async def prepare(session: AsyncSession, firm_id: uuid.UUID, *, case_id: uuid.UUID | None, household_id: uuid.UUID | None, kind: str | None,
                  status: str | None, given_by: str | None, method: str | None, evidence: str | None, given_on: date | None) -> dict:
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    if (case_id is None) == (household_id is None):
        raise ConsentError("Name the onboarding case or the household, one of them.")
    if case_id is not None:
        case = await session.get(OnboardingCase, case_id)
        if case is None or case.firm_id != firm_id:
            raise ConsentError("Case not found.")
        whose = case.prospect_name
    else:
        household = await session.get(Household, household_id)
        if household is None or household.firm_id != firm_id:
            raise ConsentError("Household not found.")
        whose = household.name
    kind = (kind or "").strip().lower()
    if kind not in kinds_for(firm):
        if kind in INDIA_KINDS:
            raise ConsentError(f"{LABELS[kind]} is recorded for a firm in India only.")
        raise ConsentError(f"The consent is one of {', '.join(kinds_for(firm))}.")
    status = (status or "granted").strip().lower()
    if status not in ("granted", "withdrawn"):
        raise ConsentError("Granted or withdrawn.")
    method = (method or "").strip().lower()
    if method not in METHODS:
        raise ConsentError(f"How it was given: one of {', '.join(METHODS)}.")
    if kind in INDIA_METHODS and method not in INDIA_METHODS[kind]:
        raise ConsentError(f"{LABELS[kind]} is evidenced by {', '.join(m.replace('_', ' ') for m in INDIA_METHODS[kind])}, "
                           f"not {method.replace('_', ' ')} (IA Master Circular, 6 Feb 2026).")
    who, named = (given_by or "").strip(), (evidence or "").strip()
    if len(who) < 2:
        raise ConsentError("Say who gave it.")
    if len(named) < 5:
        raise ConsentError("Say where the evidence is: the form, the e-signature envelope, the recording.")
    day = given_on or today
    if day > today:
        raise ConsentError("A consent cannot be dated in the future.")
    return {"case_id": case_id, "household_id": household_id, "whose": whose, "kind": kind, "status": status, "given_by": who, "method": method,
            "evidence": named, "given_on": day}


async def record(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> dict:
    r = await prepare(session, firm_id, **terms)
    row = ConsentRecord(firm_id=firm_id, case_id=r["case_id"], household_id=r["household_id"], kind=r["kind"], status=r["status"], given_by=r["given_by"],
                        given_on=r["given_on"], method=r["method"], evidence=r["evidence"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="consent_recorded", actor=by, content={
        "case_id": str(r["case_id"]) if r["case_id"] else None, "household_id": str(r["household_id"]) if r["household_id"] else None,
        "kind": r["kind"], "status": r["status"], "evidence": r["evidence"]})
    return await registry(session, firm_id, household_id=r["household_id"], case_id=r["case_id"])
