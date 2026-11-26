"""Onboarding in India's own terms (B-385): what an Indian account is called, how a joint one is operated, how money and
securities move, and what is not asked for twice.

**Joint holding.** An Indian joint account is operated "joint", where all holders sign, or "anyone or survivor". The two are
two account types, so the person opening the case chooses one and nothing is assumed; the US tenancies are not offered. An order
or a payment out on an all-sign account says who must sign. Nothing here collects or checks a signature.

**Identity the registry holds.** An individual whose KYC registry record holds identity is not asked for a passport or driving
licence again. The record is what a person recorded (`kyc_india`): validated by the KRA, or, with no KRA status recorded, a CKYC
number. A KRA status of registered (the next intermediary repeats the KYC), on hold or rejected, or an identity proof that has
run out, does not count, and neither does a record under another name or another case. Where nothing is on file the requirement
stays. A joint account needs it for every holder. Real KRA and CKYC lookups are not connected.

**What a registry answer says it holds is not asked again** (B-386). The registry status that stands (`kyc_registry`) is read first:
one that is not validated holds nothing. Where a validated answer lists the documents the registry holds, identity answers an NRI
account's photo ID too, and the PAN card of the person or entity the account needs it from is not asked.
"""
from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, markets
from app.models.enums import PartyRole
from app.models.graph import Account, Person
from app.models.kyc import KycRecord
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.models.tenant import Firm

JOINT_ALL, JOINT_ANYONE = "joint_all", "joint_anyone"
JOINT_ASKED = ("In India a joint account is joint_all (all holders sign) or joint_anyone (anyone or survivor). Ask which the "
               "holders chose on their forms: neither is assumed.")
_US_JOINT = frozenset({"joint", "joint_jtwros", "joint_tic", "jtwros", "tic"})

PHOTO_ID = frozenset({"passport", "drivers_licence"})
_HOLDERS_OWN_ID = frozenset({"individual", JOINT_ALL, JOINT_ANYONE, "joint_jtwros", "joint_tic"})
HELD_LABEL = "Identity held on the KYC registry record"
HELD_PAN_LABEL = "PAN held by the KYC registry"
_NRI = frozenset({"nri_nre", "nri_nro"})

# How money and securities reach an account. An Indian account has no ACAT: securities move off-market between demats.
TRANSFER_TYPES = {
    "IN": (("demat", "Off-market demat transfer"), ("rtgs_neft", "RTGS or NEFT"), ("upi", "UPI"), ("cheque", "Cheque")),
    None: (("acat", "ACAT (securities)"), ("wire", "Wire transfer"), ("ach", "ACH"), ("rollover", "Rollover"), ("exchange_1035", "1035 exchange")),
}


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def transfer_types(firm: Firm | None) -> list[dict]:
    return [{"value": v, "label": label} for v, label in TRANSFER_TYPES["IN" if applies(firm) else None]]


def transfer_refusal(firm: Firm | None, transfer_type: str) -> str | None:
    allowed = transfer_types(firm)
    if transfer_type in {t["value"] for t in allowed}:
        return None
    said = ", ".join(f"{t['label'].lower() if t['value'] == 'demat' else t['label']} ({t['value']})" for t in allowed)
    return f"For this firm a transfer is one of: {said}."


def registration_refusal(firm: Firm | None, registration_type: str | None) -> str | None:
    """Why an Indian account may not be opened or re-registered as this type, where it is a US way of holding jointly."""
    return JOINT_ASKED if applies(firm) and (registration_type or "").strip().lower() in _US_JOINT else None


def account_registration(firm: Firm | None, case: OnboardingCase) -> str | None:
    """The registration an Indian account carries from its case, so the mode of holding is on the account itself."""
    return case.registration_type if applies(firm) else None


def _norm(name: str | None) -> str:
    return " ".join((name or "").split()).casefold()


def _holds_identity(record: KycRecord, today, status: str | None) -> bool:
    if record.id_proof_valid_to and record.id_proof_valid_to < today:
        return False
    if status:
        return status == "validated"
    return bool(record.ckyc_number)


async def registry_holds(session: AsyncSession, firm: Firm | None, case: OnboardingCase, parties: list) -> dict:
    """The document requirements this case need not collect, each with what stands in for it. Empty outside India."""
    if not applies(firm):
        return {}
    from app.aurea_core import kyc_registry

    registration = case.registration_type or "individual"
    people: dict[uuid.UUID, str] = {}
    if (case.materialized or {}).get("person_id"):
        people[uuid.UUID(str(case.materialized["person_id"]))] = _norm(case.prospect_name)
    if (case.intake or {}).get("household_id"):       # a member of the household it joins, already a client
        home = uuid.UUID(str(case.intake["household_id"]))
        for pid, name in (await session.execute(select(Person.id, Person.full_name).where(Person.firm_id == firm.id, Person.household_id == home))).all():
            people[pid] = _norm(name)
    where = KycRecord.case_id == case.id
    if people:
        where = or_(where, KycRecord.person_id.in_(list(people)))
    latest: dict[str, KycRecord] = {}
    for r in (await session.execute(select(KycRecord).where(KycRecord.firm_id == firm.id, where).order_by(KycRecord.created_at))).scalars().all():
        latest[_norm(r.party_name)] = r
    # The registry status that stands for each name on this case: a lookup or a record, the latest said.
    stands: dict[str, dict] = {}
    entity = (case.materialized or {}).get("entity_id")
    for key, said in (await kyc_registry.standing(session, firm.id)).items():
        name = (people.get(key[1]) if key[0] == "person" else _norm(said["party_name"]) if said["case_id"] == case.id
                or (key[0] == "case" and key[1] == case.id) or (entity and key == ("entity", uuid.UUID(str(entity)))) else None)
        if name and (name not in stands or said["recorded_at"] > stands[name]["recorded_at"]):
            stands[name] = said
    today = firm_time.today(firm)
    held: dict = {}

    def lists(name: str, kind: str) -> bool:
        said = stands.get(name)
        return bool(said and said["status"] == "validated" and kind in said["holds"])

    def identity(name: str) -> bool:
        if registration in _NRI:
            return lists(name, "identity_proof")
        record, said = latest.get(name), stands.get(name)
        if said is not None:
            if said["status"] != "validated":
                return False
            return lists(name, "identity_proof") or record is None or _holds_identity(record, today, said["status"])
        return record is not None and _holds_identity(record, today, record.kra_status)

    if not case.is_entity and registration in (_HOLDERS_OWN_ID | _NRI):
        holders = {_norm(p.legal_name) for p in parties if p.role in (PartyRole.OWNER, PartyRole.JOINT_OWNER)}
        if registration == "individual" or registration in _NRI:
            holders = holders or {_norm(case.prospect_name)}
        elif len(holders) < 2:
            holders = set()      # a joint account whose holders are not all recorded
        if holders and all(identity(h) for h in holders):
            held["id"] = HELD_LABEL
    minors = {_norm(p.legal_name) for p in parties if p.role == PartyRole.MINOR}
    pan_of = minors if registration == "minor_guardian" else {_norm(case.prospect_name)}
    if pan_of and all(lists(n, "pan_card") for n in pan_of):
        held["pan"] = HELD_PAN_LABEL
    return held


async def required_identity(session: AsyncSession, firm: Firm, case: OnboardingCase, required: set, present: set) -> set:
    """The documents the onboarding agent raises as missing. In India either photo ID answers, and a registry record that holds
    identity answers for both; a company, partnership or LLP has no trust deed, and what it does need is the document matrix's.
    Anywhere else, what it was given stands."""
    if applies(firm) and case.is_entity and case.entity_type in ("company", "partnership") and set(required) == {"trust_deed"}:
        return set()
    if not applies(firm) or set(required) != PHOTO_ID:
        return set(required)
    parties = (await session.execute(select(OnboardingParty).where(OnboardingParty.case_id == case.id))).scalars().all()
    if set(present) & PHOTO_ID or "id" in await registry_holds(session, firm, case, list(parties)):
        return set()
    return set(required)


async def holder_note(session: AsyncSession, firm: Firm | None, account: Account | None) -> str | None:
    """What an order or a payment out on an all-sign joint account must say, or None."""
    if account is None or not applies(firm) or account.registration_type != JOINT_ALL:
        return None
    names: list[str] = []
    for case in (await session.execute(select(OnboardingCase).where(
            OnboardingCase.firm_id == firm.id, OnboardingCase.activated_at.is_not(None)))).scalars().all():
        if (case.materialized or {}).get("account_id") == str(account.id):
            rows = (await session.execute(select(OnboardingParty).where(
                OnboardingParty.case_id == case.id, OnboardingParty.role.in_([PartyRole.OWNER, PartyRole.JOINT_OWNER]))
                .order_by(OnboardingParty.role.desc(), OnboardingParty.legal_name))).scalars().all()
            names = [p.legal_name for p in rows]
    who = ", ".join(names) if names else "the holders are not recorded here"
    return f"All holders must sign (joint holding on {account.name}): {who}. No signature is collected or checked here."
