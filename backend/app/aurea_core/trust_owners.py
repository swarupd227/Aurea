"""The people behind an Indian trust, recorded as parties so they are screened and gated (L200-1 India sections 4.1 and 5.3,
B-391).

A KYC record lists a trust's beneficial owners as words (`kyc_record.beneficial_owners`), and words are not screened. The test
that says which of them are beneficial owners is `kyc_india.beneficial_owners`: the author, trustee and protector always,
and a beneficiary with an interest at the line in `law_figures`. Each of those who is not a party of the trust's open
onboarding case keeps the account out of good order (`document_matrix.check_case`), which holds activation. A person records
them as parties from the document, and from then on the party rules that already apply do: screened when recorded, every hit
decided before the account opens, and identity verified for the roles the identity rule reaches.

It reads only a case that has not been activated. A trust with no owners recorded is left to the KYC state (not assessed):
nothing is invented here. What happens to a record after activation is the KYC gate's (B-402), not this module's.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import kyc_india, screening_lists
from app.core.security import UserRole
from app.models.enums import NIGORootCause, PartyRole
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS, UserRole.COMPLIANCE})
# Which party role a deed role becomes; where one person holds several, the first listed here is recorded.
ROLE_OF = (("trustee", PartyRole.TRUSTEE), ("author", PartyRole.SETTLOR), ("protector", PartyRole.PROTECTOR),
           ("beneficiary", PartyRole.BENEFICIAL_OWNER))


class TrustOwnerError(ValueError):
    """These cannot be recorded, with the reason."""


def _norm(name: str | None) -> str:
    return " ".join((name or "").split()).casefold()


def _reads(firm: Firm | None, case: OnboardingCase) -> bool:
    return kyc_india.applies(firm) and case.registration_type == "trust" and case.activated_at is None


async def position(session: AsyncSession, firm: Firm, case: OnboardingCase, parties: list | None = None) -> dict:
    """The beneficial owners the KYC record names for this trust, each with the party that stands for them, if any."""
    if not _reads(firm, case):
        return {"applies": False, "owners": [], "missing": [], "recorded": False}
    if parties is None:
        parties = list((await session.execute(select(OnboardingParty).where(OnboardingParty.case_id == case.id))).scalars().all())
    records = [r for r in (await kyc_india._records_for_case(session, firm.id, case)).values() if r.structure == "trust"]
    owners: dict[str, dict] = {}
    document = None
    for r in records:
        listed = list(r.beneficial_owners or [])
        named = {_norm(n) for n in kyc_india.beneficial_owners("trust", listed)["beneficial"]}
        for o in listed:
            key = _norm(o.get("name"))
            if key not in named:
                continue
            found = owners.setdefault(key, {"name": o["name"], "roles": [], "share": None})
            if o.get("role") not in found["roles"]:
                found["roles"].append(o.get("role"))
            if o.get("role") == "beneficiary" and o.get("share") is not None:
                found["share"] = float(o["share"])
        document = r.document or document
    by_name: dict[str, OnboardingParty] = {}
    for p in parties:
        by_name.setdefault(_norm(p.legal_name), p)
    rows = []
    for key, found in sorted(owners.items()):
        role = next((party for deed, party in ROLE_OF if deed in found["roles"]), PartyRole.BENEFICIAL_OWNER)
        p = by_name.get(key)
        rows.append({**found, "roles_label": ", ".join(kyc_india.OWNER_ROLES.get(x, x) for x in found["roles"]), "as_role": str(role),
                     "party_id": str(p.id) if p else None, "party_role": p.role if p else None,
                     "screening": (p.screening_status or "not_screened") if p else None})
    return {"applies": True, "recorded": bool(owners), "document": document, "owners": rows,
            "missing": [r for r in rows if r["party_id"] is None]}


async def matrix_issues(session: AsyncSession, firm: Firm | None, case: OnboardingCase, parties: list) -> list[dict]:
    """The good-order issues the document matrix adds: one for each recorded owner who is not a party."""
    if firm is None or not _reads(firm, case):
        return []
    found = await position(session, firm, case, parties)
    return [{"root_cause": str(NIGORootCause.BENEFICIAL_OWNER_INCOMPLETE), "severity": "critical", "field": "beneficial_owners",
             "description": f"{o['name']} ({o['roles_label']}) is a beneficial owner on the trust's KYC record and is not recorded as a "
                            "party, so has not been screened."} for o in found["missing"]]


async def cases(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None) -> list[dict]:
    """Each open trust case with its owners, for the agreement file."""
    if not kyc_india.applies(firm):
        return []
    rows = (await session.execute(select(OnboardingCase).where(
        OnboardingCase.firm_id == firm.id, OnboardingCase.registration_type == "trust", OnboardingCase.activated_at.is_(None))
        .order_by(OnboardingCase.prospect_name))).scalars().all()
    out = []
    for case in rows:
        if case.status == "rejected":
            continue
        if household_id is not None and (case.intake or {}).get("household_id") != str(household_id):
            continue
        found = await position(session, firm, case)
        out.append({"case_id": str(case.id), "case": case.prospect_name, **found})
    return out


async def _case(session: AsyncSession, firm: Firm, case_id) -> OnboardingCase:
    try:
        cid = case_id if isinstance(case_id, uuid.UUID) else uuid.UUID(str(case_id))
    except ValueError:
        raise TrustOwnerError("Give the onboarding case's id, from the onboarding board.")
    case = await session.get(OnboardingCase, cid)
    if case is None or case.firm_id != firm.id:
        raise TrustOwnerError("No such onboarding case at this firm.")
    return case


async def prepare(session: AsyncSession, firm: Firm, role, *, case_id, document: str | None = None) -> dict:
    if not kyc_india.applies(firm):
        raise TrustOwnerError("A trust's beneficial owners are recorded as parties here for an Indian firm.")
    if role not in RECORDERS:
        raise TrustOwnerError("An adviser, paraplanner, operations or compliance records a trust's beneficial owners.")
    case = await _case(session, firm, case_id)
    if case.registration_type != "trust":
        raise TrustOwnerError(f"{case.prospect_name} is not a trust account.")
    if case.activated_at:
        raise TrustOwnerError("This account is open; its owners change through the KYC record and the custodian, not here.")
    found = await position(session, firm, case)
    if not found["recorded"]:
        raise TrustOwnerError("The trust's KYC record names no beneficial owner. Record the author, trustees, protector and "
                              "beneficiaries from the deed first (record_kyc, structure trust).")
    if not found["missing"]:
        raise TrustOwnerError("Every beneficial owner on the KYC record is already a party of the case.")
    named = (document or "").strip() or found["document"]
    if not named or len(named) < 5:
        raise TrustOwnerError("Say which document names them: the trust deed.")
    return {"case": case, "missing": found["missing"], "document": named}


async def record(session: AsyncSession, firm: Firm, role, *, by: str, case_id, document: str | None = None) -> dict:
    p = await prepare(session, firm, role, case_id=case_id, document=document)
    case = p["case"]
    listed = await screening_lists.entries(session, firm.id)
    added = []
    for o in p["missing"]:
        party = OnboardingParty(firm_id=firm.id, case_id=case.id, role=o["as_role"], legal_name=o["name"][:200],
                                ownership_pct=o["share"] if o["as_role"] == PartyRole.BENEFICIAL_OWNER else None,
                                notes=f"{o['roles_label']} of the trust, from {p['document']}", screening_status="not_screened")
        screening_lists.stamp(party, listed, "Screened when recorded. Hits await a person's decision.")
        session.add(party)
        added.append(party)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="trust_owners_recorded", actor=by, content={
        "case_id": str(case.id), "document": p["document"],
        "parties": [{"name": a.legal_name, "role": a.role, "screening": a.screening_status} for a in added]})
    found = await position(session, firm, case)
    return {"case_id": str(case.id), "case": case.prospect_name,
            "added": [{"name": a.legal_name, "role": a.role, "screening": a.screening_status} for a in added], **found}
