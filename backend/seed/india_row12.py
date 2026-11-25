"""India row 12 on the demonstration firm: KYC as a person recorded it, in every state.

Every record is invented for the demonstration firm and says so in its evidence, and the CKYC numbers are not real. Run from
`seed.india_demo` when the firm is built and from `seed.india_demo_backfill` on a firm built before row 12; it adds nothing where a
KYC record is already on file. No Aadhaar number is written anywhere: only the last four digits.

  Rajesh and Meera Sharma, the Sharma HUF   verified, three bands and an entity
  Karthik Iyer                              due: his re-KYC falls inside 60 days
  Lakshmi Iyer                              overdue: a high-risk client whose 2 years have passed
  Nandini Kapoor                            blocked: the KRA holds her record On Hold
  Kapoor Family Trust                       a trust with no owners recorded: what it needs is listed
  Subir Banerjee                            due: Registered at the KRA, documents not verified at source
  Vikram Menon, Anita Menon                 an NRI verified by video, and a PAN not linked to Aadhaar
  Elaine D'Souza                            nothing recorded: not on file, which is not none
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, kyc_india as k
from app.aurea_core.reviews import add_months
from app.models.graph import LegalEntity, Person
from app.models.kyc import KycRecord
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: india row 12 (demonstration)"


async def add(s: AsyncSession, firm: Firm) -> int:
    if (await s.execute(select(KycRecord.id).where(KycRecord.firm_id == firm.id).limit(1))).first():
        return 0
    today = firm_time.today(firm)

    async def person(name: str):
        return (await s.execute(select(Person).where(Person.firm_id == firm.id, Person.full_name == name))).scalar_one_or_none()

    async def entity(name: str):
        return (await s.execute(select(LegalEntity).where(LegalEntity.firm_id == firm.id, LegalEntity.name == name))).scalars().first()

    base = dict(kra_name="CVL KRA", kra_status="validated", verification_mode="aadhaar_ekyc", pan_aadhaar_linked=True, id_proof_kind="passport",
                id_proof_valid_to=today + timedelta(days=2400))
    n = 0

    async def put(who, number: str, **facts):
        nonlocal n
        key = {"person_id": who.id} if isinstance(who, Person) else {"entity_id": who.id}
        await k.record(s, firm, by=BY, document=f"KRA acknowledgement and CKYC letter ({SOURCE}; the number is not a real one)",
                       ckyc_number=number, **key, **facts)
        n += 1

    rajesh, meera, huf = await person("Rajesh Sharma"), await person("Meera Sharma"), await entity("Rajesh Sharma HUF")
    if rajesh:
        await put(rajesh, "70000000000001", **base, risk_band="medium", kyc_completed_on=add_months(today, -14), aadhaar_last4="4021",
                  aadhaar_xml_dated=add_months(today, -14) - timedelta(days=1))
    if meera:
        await put(meera, "70000000000002", **base, risk_band="low", kyc_completed_on=add_months(today, -26), aadhaar_last4="7788")
    if huf:
        await put(huf, "70000000000003", **{**base, "id_proof_kind": None, "id_proof_valid_to": None}, risk_band="medium", kyc_completed_on=add_months(today, -20),
                  structure="huf", beneficial_owners_text="Rajesh Sharma, 0, karta, yes")
    karthik, lakshmi = await person("Karthik Iyer"), await person("Lakshmi Iyer")
    if karthik:
        await put(karthik, "70000000000004", **base, risk_band="medium", kyc_completed_on=add_months(today, -96) + timedelta(days=40), aadhaar_last4="1190")
    if lakshmi:
        await put(lakshmi, "70000000000005", **base, risk_band="high", kyc_completed_on=add_months(today, -30), aadhaar_last4="3356")
    nandini, trust = await person("Nandini Kapoor"), await entity("Kapoor Family Trust")
    if nandini:
        await put(nandini, "70000000000006", **{**base, "kra_status": "on_hold"}, risk_band="high", kyc_completed_on=add_months(today, -8), aadhaar_last4="9042")
    if trust:
        await put(trust, "70000000000007", **{**base, "id_proof_kind": None, "id_proof_valid_to": None}, risk_band="high", kyc_completed_on=add_months(today, -9),
                  structure="trust")
    subir = await person("Subir Banerjee")
    if subir:
        await put(subir, "70000000000008", **{**base, "kra_status": "registered"}, risk_band="low", kyc_completed_on=add_months(today, -40), aadhaar_last4="6615")
    vikram, anita = await person("Vikram Menon"), await person("Anita Menon")
    if vikram:
        await put(vikram, "70000000000009", **{**base, "verification_mode": "video_kyc"}, risk_band="high", kyc_completed_on=add_months(today, -7), aadhaar_last4="2204")
    if anita:
        await put(anita, "70000000000010", **{**base, "pan_aadhaar_linked": False}, risk_band="medium", kyc_completed_on=add_months(today, -7), aadhaar_last4="8837")
    return n
