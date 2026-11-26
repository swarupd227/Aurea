"""India Stack seams: a DigiLocker document pull and the eSign of the advisory agreement (L100 India edition, B-302).

Both are seams. The real feature reads only what a provider delivers through `PROVIDERS`; the DigiLocker API (through a registered
requester) and an Aadhaar eSign service provider will register there, and `app/simulations/india_stack` stands in until they do. With
no provider for a firm every function here says "not connected" and nothing is invented.

A pull or an envelope never changes a record by itself. A provider delivers an *offer*, and a person confirms it behind a Confirm card:

  - **A DigiLocker document** arrives as a reference: the issuer, the document type, the masked number and the day it was fetched.
    Confirming it records the party's KYC with the mode `digilocker` (`kyc_india.record`): the day of the KYC is the day it was fetched,
    the identity proof is the document type where that is one the KYC record knows, and the evidence names the reference. A document
    that proves nothing the KYC record holds still records the mode and the day. Nothing else is read out of it.
  - **An eSigned agreement** arrives as an envelope: the document, the signer and the state the provider reports (sent, signed,
    declined). Confirming a signed one records the `advisory_agreement` consent with the method `e_signature` and the envelope as its
    evidence (`consents.record`), which is the evidence the pre-advice gate reads. An envelope may also declare what its template included
    (`includes`: annexure_a, mitc); confirming it records those flags (`agreement_contents.from_envelope`, B-324) and, where it declared nothing,
    records nothing, so the agreement's contents stay "not recorded". A sent or declined envelope cannot be recorded.

A full Aadhaar number is never stored or shown: a provider's reference that carries one is cut to its last four, masked, and a person's
text carrying one is refused (`kyc_india.AADHAAR_NUMBER`). The KRA and CKYC lookup is not connected here: it needs a vendor (B-272), and
a record of the KRA's status is still what a person read from its acknowledgement. This is not a statement that a KYC or an agreement
meets any rule; a person checks the document.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import consents, firm_time, kyc_india
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Household, Person
from app.models.india_batch_b import StackItem
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS})
# What a DigiLocker document is, and the identity proof of the KYC record it stands for (None: it proves no kind the record holds).
DOC_TYPES = {"aadhaar": ("Aadhaar", "aadhaar_proof"), "driving_licence": ("Driving licence", "driving_licence"), "passport": ("Passport", "passport"),
             "voter_id": ("Voter ID", "voter_id"), "pan": ("PAN card", None)}
ESIGN_STATES = ("sent", "signed", "declined")
KRA_CKYC = {"connected": False, "said": "The KRA and CKYC lookup is not connected: it needs a vendor (B-272). A KYC status is still what a person read from "
                                         "the KRA's acknowledgement."}
NOT_CONNECTED = "DigiLocker and eSign are not connected for this firm: no provider is registered, so nothing was fetched or sent."
CAVEAT = "A person checks the document. This does not say the KYC or the agreement meets any rule."

PROVIDERS: dict[str, object] = {}


class StackError(ValueError):
    """This cannot be pulled, sent or recorded, with the reason."""


def register(provider) -> None:
    PROVIDERS[provider.key] = provider


def provider_for(firm: Firm):
    for key in sorted(PROVIDERS):
        if PROVIDERS[key].enabled_for(firm):
            return PROVIDERS[key]
    return None


def applies(firm: Firm | None) -> bool:
    return kyc_india.applies(firm)


def mask(text: str | None) -> str | None:
    """A masked document number, never a whole Aadhaar number: a 12-digit run is cut to its last four."""
    if not text:
        return None
    v = " ".join(str(text).split())
    m = kyc_india.AADHAAR_NUMBER.search(v)
    if m:
        digits = "".join(c for c in m.group(0) if c.isdigit())
        v = kyc_india.AADHAAR_NUMBER.sub(f"XXXX XXXX {digits[-4:]}", v)
    return v[:40]


def _item_view(i: StackItem, names: dict, people: dict) -> dict:
    return {"id": str(i.id), "kind": i.kind, "state": i.state, "household_id": str(i.household_id), "household": names.get(i.household_id),
            "person_id": str(i.person_id) if i.person_id else None, "person": people.get(i.person_id), "provider": i.provider,
            "simulated": bool(i.source_system), "reference": i.reference, "issuer": i.issuer, "doc_type": i.doc_type,
            "doc_label": DOC_TYPES.get(i.doc_type or "", (i.doc_type, None))[0], "masked_number": i.masked_number,
            "fetched_on": i.fetched_on.isoformat() if i.fetched_on else None, "document_name": i.document_name, "signer": i.signer,
            "provider_state": i.provider_state, "of_item_id": str(i.of_item_id) if i.of_item_id else None, "recorded_by": i.recorded_by,
            "recorded_on": i.created_at.date().isoformat() if i.created_at else None}


async def _resolved(session: AsyncSession, firm_id: uuid.UUID) -> tuple[list[StackItem], dict]:
    rows = list((await session.execute(select(StackItem).where(StackItem.firm_id == firm_id).order_by(StackItem.created_at.asc()))).scalars().all())
    closed = {r.of_item_id: r for r in rows if r.of_item_id is not None}
    return rows, closed


async def overview(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None) -> dict:
    if not applies(firm):
        return {"applies": False, "items": [], "kra_ckyc": KRA_CKYC}
    rows, closed = await _resolved(session, firm.id)
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    people = {p.id: p.full_name for p in (await session.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    offers = [r for r in rows if r.of_item_id is None and (household_id is None or r.household_id == household_id)]
    items = []
    for r in offers:
        v = _item_view(r, names, people)
        c = closed.get(r.id)
        v["open"] = c is None and (r.kind == "digilocker" or r.provider_state == "signed")
        v["outcome"] = c.state if c else None
        v["outcome_by"] = c.recorded_by if c else None
        items.append(v)
    p = provider_for(firm)
    return {"applies": True, "items": list(reversed(items)), "kra_ckyc": KRA_CKYC,
            "provider": ({"key": p.key, "name": p.name, "simulated": bool(getattr(p, "simulated", False))} if p else None),
            "counts": {"documents": sum(1 for i in items if i["kind"] == "digilocker"), "envelopes": sum(1 for i in items if i["kind"] == "esign"),
                       "open": sum(1 for i in items if i["open"]), "recorded": sum(1 for i in items if i["outcome"] == "recorded")},
            "said": NOT_CONNECTED if p is None else CAVEAT, "household_id": str(household_id) if household_id else None}


async def _party(session: AsyncSession, firm: Firm, role, person_id) -> tuple[Person, Household]:
    if UserRole(role) not in RECORDERS:
        raise StackError("A DigiLocker pull or an eSign is made by an adviser, a paraplanner or operations.")
    if not applies(firm):
        raise StackError("DigiLocker and eSign are kept for an Indian firm.")
    try:
        person = await session.get(Person, uuid.UUID(str(person_id)))
    except (ValueError, TypeError):
        raise StackError("Give the person's id, from the client's page.")
    household = await session.get(Household, person.household_id) if person is not None and person.household_id else None
    if person is None or household is None or household.firm_id != firm.id:
        raise StackError("No such person at this firm.")
    return person, household


async def pull_digilocker(session: AsyncSession, firm: Firm, role, *, person_id, by: str) -> dict:
    """Ask the provider for the client's documents. The client authorises it in DigiLocker itself; what comes back are offers."""
    person, household = await _party(session, firm, role, person_id)
    provider = provider_for(firm)
    if provider is None:
        raise StackError(NOT_CONNECTED)
    today = firm_time.today(firm)
    docs = await provider.pull_documents(session, firm, person)
    have = {(r.reference) for r in (await session.execute(select(StackItem).where(StackItem.firm_id == firm.id, StackItem.kind == "digilocker"))).scalars().all()}
    added = 0
    for d in docs:
        ref = " ".join(str(d.get("reference") or "").split())[:160]
        if len(ref) < 4 or ref in have or (d.get("doc_type") or "") not in DOC_TYPES:
            continue
        session.add(StackItem(firm_id=firm.id, household_id=household.id, person_id=person.id, kind="digilocker", state="offered", provider=provider.name,
                              reference=ref, issuer=" ".join(str(d.get("issuer") or "").split())[:160] or None, doc_type=d["doc_type"],
                              masked_number=mask(d.get("masked_number")), fetched_on=d.get("fetched_on") or today, recorded_by=by,
                              source_system=provider.key if getattr(provider, "simulated", False) else None, created_at=utcnow()))
        added += 1
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="digilocker_pulled", actor=by, content={
        "household_id": str(household.id), "person_id": str(person.id), "documents": added, "provider": provider.name})
    return {"added": added, **await overview(session, firm, household_id=household.id)}


def _declared(env: dict) -> dict | None:
    """What the template declared it included (`includes`: annexure_a, mitc), kept only as the provider returned it. None where it declared nothing, so
    nothing is assumed (B-324)."""
    inc = env.get("includes")
    if not isinstance(inc, (list, tuple)):
        return None
    return {"includes": sorted({str(x) for x in inc if str(x) in ("annexure_a", "mitc")})}


def clean_document(document_name: str | None) -> str:
    name = " ".join((document_name or "Investment advisory agreement").split())
    if len(name) < 5 or kyc_india.AADHAAR_NUMBER.search(name):
        raise StackError("Name the document being signed, without any Aadhaar number.")
    return name[:200]


async def send_for_esign(session: AsyncSession, firm: Firm, role, *, person_id, document_name: str | None, by: str) -> dict:
    person, household = await _party(session, firm, role, person_id)
    provider = provider_for(firm)
    if provider is None:
        raise StackError(NOT_CONNECTED)
    name = clean_document(document_name)
    env = await provider.send_for_signature(session, firm, person, name)
    state = env.get("state")
    ref = " ".join(str(env.get("reference") or "").split())[:160]
    if state not in ESIGN_STATES or len(ref) < 4:
        raise StackError("The provider did not return an envelope with a state; nothing is recorded.")
    session.add(StackItem(firm_id=firm.id, household_id=household.id, person_id=person.id, kind="esign", state="offered", provider=provider.name,
                          reference=ref, document_name=name, signer=person.full_name, provider_state=state, fetched_on=env.get("signed_on"),
                          detail=_declared(env), recorded_by=by, source_system=provider.key if getattr(provider, "simulated", False) else None, created_at=utcnow()))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="esign_sent", actor=by, content={
        "household_id": str(household.id), "person_id": str(person.id), "envelope": ref, "state": state})
    return await overview(session, firm, household_id=household.id)


async def _offer(session: AsyncSession, firm: Firm, item_id, kind: str) -> tuple[StackItem, StackItem | None]:
    try:
        item = await session.get(StackItem, uuid.UUID(str(item_id)))
    except (ValueError, TypeError):
        raise StackError("Give the item's id, from read_india_stack.")
    if item is None or item.firm_id != firm.id or item.of_item_id is not None or item.kind != kind:
        raise StackError(f"No such {'DigiLocker document' if kind == 'digilocker' else 'eSign envelope'} at this firm.")
    _, closed = await _resolved(session, firm.id)
    return item, closed.get(item.id)


async def prepare_kyc(session: AsyncSession, firm: Firm, role, *, item_id, document: str | None = None) -> dict:
    if UserRole(role) not in kyc_india.RECORDERS:
        raise StackError("A DigiLocker document is confirmed into the KYC by an adviser, a paraplanner, operations or compliance.")
    if not applies(firm):
        raise StackError("DigiLocker and eSign are kept for an Indian firm.")
    item, closed = await _offer(session, firm, item_id, "digilocker")
    if closed is not None:
        raise StackError(f"That document was already {closed.state} by {closed.recorded_by}.")
    label, proof = DOC_TYPES[item.doc_type]
    extra = " ".join((document or "").split())
    if kyc_india.AADHAAR_NUMBER.search(extra):
        raise StackError("An Aadhaar number is never stored, not even in a note.")
    sim = " [simulated: " + item.source_system + "]" if item.source_system else ""
    evidence = f"DigiLocker {item.issuer or 'issuer'} {label} {item.masked_number or ''}, fetched {item.fetched_on.isoformat() if item.fetched_on else ''}, ref {item.reference}{sim}"
    terms = {"person_id": item.person_id, "document": " ".join(evidence.split())[:300] + (f"; {extra}" if extra and len(evidence) < 250 else ""),
             "verification_mode": "digilocker", "kyc_completed_on": item.fetched_on}
    if proof:
        terms["id_proof_kind"] = proof
    if item.doc_type == "aadhaar":
        m = kyc_india.AADHAAR_NUMBER.search(item.masked_number or "")
        last4 = "".join(c for c in (item.masked_number or "") if c.isdigit())[-4:]
        if len(last4) == 4 and not m:
            terms["aadhaar_last4"] = last4
    try:
        p = await kyc_india.prepare(session, firm, **terms)
    except kyc_india.KycError as exc:
        raise StackError(str(exc))
    return {"item": item, "terms": terms, "kyc": p, "label": label}


async def record_kyc(session: AsyncSession, firm: Firm, role, *, by: str, item_id, document: str | None = None) -> dict:
    p = await prepare_kyc(session, firm, role, item_id=item_id, document=document)
    result = await kyc_india.record(session, firm, by=by, **p["terms"])
    item = p["item"]
    session.add(StackItem(firm_id=firm.id, household_id=item.household_id, person_id=item.person_id, kind="digilocker", state="recorded", of_item_id=item.id,
                          provider=item.provider, reference=item.reference, issuer=item.issuer, doc_type=item.doc_type, masked_number=item.masked_number,
                          fetched_on=item.fetched_on, recorded_by=by, source_system=item.source_system, created_at=utcnow()))
    await session.flush()
    return {"kyc": result, **await overview(session, firm, household_id=item.household_id)}


async def prepare_agreement(session: AsyncSession, firm: Firm, role, *, item_id) -> dict:
    if UserRole(role) not in RECORDERS:
        raise StackError("An eSigned agreement is recorded by an adviser, a paraplanner or operations.")
    if not applies(firm):
        raise StackError("DigiLocker and eSign are kept for an Indian firm.")
    item, closed = await _offer(session, firm, item_id, "esign")
    if closed is not None:
        raise StackError(f"That envelope was already {closed.state} by {closed.recorded_by}.")
    if item.provider_state != "signed" or item.fetched_on is None:
        raise StackError(f"The provider reports that envelope as {item.provider_state}, not signed: there is no signature to record.")
    try:
        c = await consents.prepare(session, firm.id, case_id=None, household_id=item.household_id, kind="advisory_agreement", status="granted",
                                   given_by=item.signer, method="e_signature", given_on=item.fetched_on,
                                   evidence=f"eSign envelope {item.reference} ({item.provider}){' [simulated: ' + item.source_system + ']' if item.source_system else ''}")
    except consents.ConsentError as exc:
        raise StackError(str(exc))
    return {"item": item, "consent": c}


async def record_agreement(session: AsyncSession, firm: Firm, role, *, by: str, item_id) -> dict:
    p = await prepare_agreement(session, firm, role, item_id=item_id)
    c, item = p["consent"], p["item"]
    registry = await consents.record(session, firm.id, by=by, case_id=None, household_id=c["household_id"], kind=c["kind"], status=c["status"],
                                     given_by=c["given_by"], method=c["method"], evidence=c["evidence"], given_on=c["given_on"])
    session.add(StackItem(firm_id=firm.id, household_id=item.household_id, person_id=item.person_id, kind="esign", state="recorded", of_item_id=item.id,
                          provider=item.provider, reference=item.reference, document_name=item.document_name, signer=item.signer, provider_state=item.provider_state,
                          detail=item.detail, fetched_on=item.fetched_on, recorded_by=by, source_system=item.source_system, created_at=utcnow()))
    await session.flush()
    from app.aurea_core import agreement_contents

    declared = await agreement_contents.from_envelope(session, firm, item, by=by)
    return {"consents": registry, "contents": ("declared by the template" if declared else "not declared by the envelope: not recorded"), **await overview(session, firm, household_id=item.household_id)}
