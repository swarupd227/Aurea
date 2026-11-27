"""Politically exposed persons: a designed path, not a note (B-390, L200-1 India §5.3 and §8).

A person is known to be one in two ways here: a screening match on a PEP list that a second person confirmed
(aml_review), or a KYC record a person marked as one (kyc_india). Either opens enhanced due diligence on the case,
floors the risk rating at high (aml_risk), keeps operations from signing the file off, and holds activation until
compliance records senior approval: who the senior person was, why, and where it is filed. Approval needs the case
rated high and its source of wealth signed off; for a client with no onboarding case here the source of wealth is
recorded with the approval itself. An exposed person who appears after an approval is not covered by it.

An approval is added and never edited, and the latest stands. It holds an account not yet opened; it does not hold
the book of a client already open, which is the firm's to decide and is shown as outstanding. None of this is told to
the client.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, indian_names
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.aml_review import EddReview
from app.models.enums import EDDStatus
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.models.pmla import PepApproval
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.COMPLIANCE})
DECISIONS = ("approve", "decline")
SCREENING = "confirmed screening match"
KYC = "KYC record marked politically exposed"


class PepError(ValueError):
    """This cannot be recorded, with the reason."""


def _name(value: str) -> str:
    return " ".join(indian_names.words(value))


async def _kyc_marked(session: AsyncSession, firm: Firm) -> list[tuple]:
    """(onboarding case id or None, person or entity id or None, name) for each standing KYC record marked PEP."""
    from app.aurea_core import kyc_india

    if not kyc_india.applies(firm):
        return []
    latest = await kyc_india.latest(session, firm.id)
    case_of = await kyc_india._case_of_holder(session, firm.id)
    out = []
    for key, record in latest.items():
        if not record.pep:
            continue
        holder = key[1] if key[0] in ("person", "entity") else None
        case_id = record.case_id or (case_of.get(holder) if holder else key[1])
        out.append((case_id, holder, record.party_name))
    return out


async def register(session: AsyncSession, firm: Firm, *, case_id: uuid.UUID | None = None) -> list[dict]:
    """Every relationship with a politically exposed person, and what it still needs."""
    from app.aurea_core import aml_review

    query = select(OnboardingCase).where(OnboardingCase.firm_id == firm.id, OnboardingCase.status != "rejected")
    if case_id is not None:
        query = query.where(OnboardingCase.id == case_id)
    cases = {c.id: c for c in (await session.execute(query)).scalars().all()}
    subjects: dict = {}

    def add(subject: tuple, name: str, how: str) -> None:
        people = subjects.setdefault(subject, {})
        people.setdefault(_name(name), {"name": name, "how": []})
        if how not in people[_name(name)]["how"]:
            people[_name(name)]["how"].append(how)

    parties = (await session.execute(select(OnboardingParty).where(OnboardingParty.case_id.in_(list(cases))))).scalars().all() if cases else []
    decided = await aml_review.decisions_for(session, [p.id for p in parties])
    for p in parties:
        if any(h["state"] == "true_match" and h.get("category") == "pep" for h in aml_review.party_state(p, decided.get(p.id, []))["hits"]):
            add(("case", p.case_id), p.legal_name, SCREENING)
    for marked_case, holder, name in await _kyc_marked(session, firm):
        if marked_case in cases:
            add(("case", marked_case), name, KYC)
        elif holder is not None and case_id is None and marked_case is None:
            add(("holder", holder), name, KYC)

    standing: dict = {}
    for row in (await session.execute(select(PepApproval).where(PepApproval.firm_id == firm.id)
                                      .order_by(PepApproval.created_at))).scalars().all():
        standing[("case", row.case_id) if row.case_id else ("holder", row.holder_id)] = row
    out = []
    for (kind, ident), people in subjects.items():
        case = cases.get(ident) if kind == "case" else None
        row = standing.get((kind, ident))
        covered = {_name(n) for n in (row.parties or [])} if row else set()
        status = "outstanding" if row is None or set(people) - covered else ("approved" if row.decision == "approve" else "declined")
        needs = []
        if case is not None and status != "approved":
            if (case.aml_risk_tier or "") != "high":
                needs.append("rating of high")
            if case.edd_status != EDDStatus.EDD_COMPLETE.value:
                needs.append("source-of-wealth file signed off")
        if status != "approved":
            needs.append("senior approval")
        names = ", ".join(sorted(v["name"] for v in people.values()))
        out.append({
            "case_id": str(ident) if kind == "case" else None, "holder_id": str(ident) if kind == "holder" else None,
            "case": case.prospect_name if case else names, "account_open": bool(case.activated_at) if case else True,
            "people": sorted(people.values(), key=lambda v: v["name"]), "status": status, "needs": needs,
            "risk_tier": case.aml_risk_tier if case else None, "edd_status": case.edd_status if case else None,
            "approval": {"decision": row.decision, "approver": row.approver, "reason": row.reason, "evidence": row.evidence,
                         "source_of_wealth": row.source_of_wealth, "decided_on": row.decided_on.isoformat(),
                         "recorded_by": row.recorded_by} if row else None})
    order = {"declined": 0, "outstanding": 1, "approved": 2}
    return sorted(out, key=lambda x: (order[x["status"]], x["case"]))


async def for_case(session: AsyncSession, firm: Firm, case: OnboardingCase) -> dict | None:
    return next(iter(await register(session, firm, case_id=case.id)), None)


async def kyc_names(session: AsyncSession, firm: Firm, case_id: uuid.UUID) -> list[str]:
    return [name for marked, _holder, name in await _kyc_marked(session, firm) if marked == case_id]


async def gate(session: AsyncSession, firm: Firm, case: OnboardingCase) -> dict | None:
    """The activation gate's state and words, or None where nobody on the case is politically exposed."""
    entry = await for_case(session, firm, case)
    if entry is None:
        return None
    who = ", ".join(f"{p['name']} ({'; '.join(p['how'])})" for p in entry["people"])
    if entry["status"] == "approved":
        a = entry["approval"]
        return {"state": "pass", "detail": f"{who}: senior approval by {a['approver']} on {a['decided_on']}."}
    if entry["status"] == "declined":
        return {"state": "fail", "detail": f"{who}: senior approval was declined, and the account does not open."}
    return {"state": "fail", "detail": f"{who}: still needs {', '.join(entry['needs'])}. Compliance records senior approval."}


async def open_edd(session: AsyncSession, firm: Firm, record, before) -> None:
    """A KYC record marked PEP opens enhanced due diligence on its case. A file signed off before the person was
    marked is opened again; a declined one stays declined."""
    if not record.pep:
        return
    newly = not (before is not None and before.pep)
    for case_id, _holder, name in await _kyc_marked(session, firm):
        if case_id is None or _name(name) != _name(record.party_name):
            continue
        case = await session.get(OnboardingCase, case_id)
        if case is None or case.firm_id != firm.id or str(case.status) == "rejected":
            continue
        unopened = case.edd_status in (None, "", "none", "cdd")
        if unopened or (newly and case.edd_status == EDDStatus.EDD_COMPLETE.value):
            case.edd_status = EDDStatus.EDD_PENDING.value
            await ledger.append_entry(session, firm_id=firm.id, event_type="edd_opened_for_pep", actor=record.recorded_by or "kyc",
                                      content={"case_id": str(case.id), "party": record.party_name, "from": "kyc_record"})
    await session.flush()


# ── Recording the senior approval ───────────────────────────────────────────────────────────────────────────────

async def prepare(session: AsyncSession, firm: Firm, role, *, case_id=None, holder_id=None, decision: str | None = None,
                  approver: str | None = None, reason: str | None = None, evidence: str | None = None,
                  source_of_wealth: str | None = None) -> dict:
    if role not in RECORDERS:
        raise PepError("Senior approval of a politically exposed person is recorded by compliance.")
    if not case_id and not holder_id:
        raise PepError("Say which relationship: the onboarding case, or the client, as read_aml_reviews lists it under pep.")
    case = None
    if case_id:
        case = await session.get(OnboardingCase, case_id)
        if case is None or case.firm_id != firm.id:
            raise PepError("Case not found.")
    # The register is this firm's alone, so an id from anywhere else matches nothing in it.
    entries = await register(session, firm)
    entry = next((e for e in entries if (case and e["case_id"] == str(case.id)) or (not case and e["holder_id"] == str(holder_id))), None)
    if entry is None and not case:
        from app.aurea_core import kyc_india

        mapped = (await kyc_india._case_of_holder(session, firm.id)).get(holder_id)
        entry = next((e for e in entries if mapped and e["case_id"] == str(mapped)), None)
        case = await session.get(OnboardingCase, mapped) if entry else None
    if entry is None:
        raise PepError("Nobody there is recorded as politically exposed: a confirmed screening match or a KYC record marks one.")
    choice = (decision or "").strip().lower()
    if choice not in DECISIONS:
        raise PepError("The decision is approve or decline.")
    who = " ".join((approver or "").split())
    if not 3 <= len(who) <= 200:
        raise PepError("Name the senior person who decided, with their office.")
    why = (reason or "").strip()
    if len(why) < 10:
        raise PepError("Give the approver's reason.")
    if not 5 <= len((evidence or "").strip()) <= 300:
        raise PepError("Say where the approval is filed (up to 300 characters).")
    wealth = (source_of_wealth or "").strip() or None
    if choice == "approve" and case is not None:
        if (case.aml_risk_tier or "") != "high":
            raise PepError("Rate the client first: a politically exposed person is never rated below high (rate_aml_risk).")
        if case.edd_status != EDDStatus.EDD_COMPLETE.value:
            raise PepError("Sign off the source-of-wealth file first (sign_off_edd): senior approval is given on it.")
        last = (await session.execute(select(EddReview).where(EddReview.case_id == case.id, EddReview.decision == "approve")
                                      .order_by(EddReview.created_at.desc()).limit(1))).scalars().first()
        if last is not None and wealth is None:
            wealth = f"{last.wealth_type.replace('_', ' ')}: " + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in last.evidence.items())
    elif choice == "approve" and (wealth is None or len(wealth) < 15):
        raise PepError("There is no onboarding file here: record the source of wealth and the documents seen for it.")
    names = [p["name"] for p in entry["people"]]
    standing = entry["approval"]
    if standing and standing["decision"] == choice and entry["status"] in ("approved", "declined"):
        raise PepError(f"Already {entry['status']} by {standing['approver']} on {standing['decided_on']}.")
    return {"entry": entry, "case": case, "holder_id": None if case else holder_id, "decision": choice, "approver": who, "reason": why,
            "evidence": evidence.strip(), "source_of_wealth": wealth, "names": names,
            "basis": [f"{p['name']}: {'; '.join(p['how'])}" for p in entry["people"]], "today": firm_time.today(firm)}


async def record(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    row = PepApproval(created_at=utcnow(), firm_id=firm.id, case_id=p["case"].id if p["case"] else None, holder_id=p["holder_id"],
                      parties=p["names"], basis=p["basis"], decision=p["decision"], approver=p["approver"],
                      source_of_wealth=p["source_of_wealth"], reason=p["reason"], evidence=p["evidence"], decided_on=p["today"],
                      recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="pep_approval", actor=by, content={
        "case_id": str(row.case_id) if row.case_id else None, "client": str(row.holder_id) if row.holder_id else None,
        "parties": p["names"], "decision": p["decision"], "approver": p["approver"], "evidence": p["evidence"]})
    return p
