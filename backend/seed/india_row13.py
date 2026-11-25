"""India row 13 on the demonstration firm: SEBI's family of client, nominations a person recorded, a will's state, an HUF and a gratuity.

Every record is invented for the demonstration firm and says so in its evidence. Run from `seed.india_demo` when the firm is built
and from `seed.india_demo_backfill` on a firm built before row 13; it adds nothing where a family relation is already on file.

  The Sharmas     Rajesh and Meera are one family; Aarav, 22, is recorded as not dependent, so he is a family of his own and the
                  household no longer stands in. Rajesh's will is registered, names Hindu law, and his HUF has its karta and
                  coparceners recorded. His bank nominees and his EPF nominees are recorded, one EPF nominee outside the family.
  The Banerjees   Subir's bank nominees: one is not named in his will.
  The D'Souzas    Elaine's demat is a signed opt-out, so it is no longer an open nomination.
  Gratuity        Rajesh's and Lakshmi Iyer's employers' statements, named beside the retirement pot and never added to it.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import client_file, firm_time, india_estate, sebi_family
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.india_family import SebiRelation
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: india row 13 (demonstration)"


async def add(s: AsyncSession, firm: Firm) -> int:
    if (await s.execute(select(SebiRelation.id).where(SebiRelation.firm_id == firm.id).limit(1))).first():
        return 0
    today = firm_time.today(firm)
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    rajesh, meera, aarav = people.get("Rajesh Sharma"), people.get("Meera Sharma"), people.get("Aarav Sharma")
    if rajesh is None or meera is None or aarav is None:
        return 0
    n = 0
    await sebi_family.record(s, firm, by=BY, person_id=meera.id, of_person_id=rajesh.id, relation="spouse",
                             document=f"Family declaration signed by Rajesh Sharma ({SOURCE})")
    await sebi_family.record(s, firm, by=BY, person_id=aarav.id, of_person_id=rajesh.id, relation="child", dependent=False,
                             document=f"Family declaration: Aarav earns his own income and lives separately ({SOURCE})")
    n += 2
    vikram, anita, ira, kabir = (people.get(x) for x in ("Vikram Menon", "Anita Menon", "Ira Menon", "Kabir Menon"))
    if vikram and anita and ira and kabir:
        await sebi_family.record(s, firm, by=BY, person_id=anita.id, of_person_id=vikram.id, relation="spouse",
                                 document=f"Family declaration signed by Vikram Menon ({SOURCE})")
        for child in (ira, kabir):
            await sebi_family.record(s, firm, by=BY, person_id=child.id, of_person_id=vikram.id, relation="child", dependent=True,
                                     document=f"Birth certificate and family declaration: a minor child ({SOURCE})")
        n += 3

    # Rajesh: the will is the client file's (row 5); here is where it stands and the law it follows.
    try:
        await india_estate.record_will_status(s, firm, by=BY, person_id=rajesh.id, personal_law="hindu", registered=True,
                                              registration_ref="Sub-Registrar, Pune, Book 3, No. 4417 of 2022 (demonstration)",
                                              probate="not_required", executor="Meera Sharma", as_of=date(2022, 6, 14),
                                              document=f"Registered will, with the Sub-Registrar's endorsement ({SOURCE})")
        n += 1
    except india_estate.EstateError:      # a firm built before row 5 has no will on file for him
        pass
    await india_estate.record_nomination(s, firm, by=BY, person_id=rajesh.id, asset_kind="bank_account", label="Rajesh Sharma HDFC Bank savings",
                                         status="nominated", nominees="Meera Sharma:spouse:60; Aarav Sharma:son:40",
                                         document=f"Bank's nomination form DA1, stamped ({SOURCE})")
    await india_estate.record_nomination(s, firm, by=BY, person_id=rajesh.id, asset_kind="epf", label="Rajesh Sharma EPF (UAN, demonstration)",
                                         status="nominated", nominees="Meera Sharma:spouse:70; Priya Nair:friend:30",
                                         document=f"EPF nomination, Form 2, from the member's portal ({SOURCE})")
    n += 3
    huf = (await s.execute(select(LegalEntity).where(LegalEntity.firm_id == firm.id, LegalEntity.name == "Rajesh Sharma HUF"))).scalar_one_or_none()
    if huf is not None:
        await india_estate.record_huf(s, firm, by=BY, entity_id=huf.id, karta_name="Rajesh Sharma", karta_person_id=rajesh.id,
                                      coparceners="Rajesh Sharma:karta; Aarav Sharma:son", as_of=date(2019, 4, 2),
                                      document=f"HUF declaration and PAN allotment letter ({SOURCE})")
        n += 1
    await india_estate.record_gratuity(s, firm, by=BY, person_id=rajesh.id, employer="Sharma Industrial Components Pvt Ltd (demonstration)",
                                       amount=3_800_000, years_of_service=24.5, as_of=today - timedelta(days=60),
                                       document=f"Employer's gratuity statement, from the HR portal ({SOURCE})")
    n += 1
    lakshmi = people.get("Lakshmi Iyer")
    if lakshmi is not None:
        await india_estate.record_gratuity(s, firm, by=BY, person_id=lakshmi.id, employer="Coastal Software Services Ltd (demonstration)",
                                           amount=1_250_000, years_of_service=17.0, as_of=today - timedelta(days=45),
                                           document=f"Employer's gratuity statement ({SOURCE})")
        n += 1

    # Subir Banerjee: a bank nomination one of whose nominees his will does not name.
    subir = people.get("Subir Banerjee")
    if subir is not None:
        await client_file.record_estate_document(s, subir.id, firm_id=firm.id, by=BY, kind="will", signed_on=date(2021, 3, 9), held_by="Family solicitor",
                                                 source_document=SOURCE, named_heirs=["Rina Banerjee"])
        await india_estate.record_will_status(s, firm, by=BY, person_id=subir.id, personal_law="hindu", registered=False,
                                              as_of=date(2021, 3, 9), document=f"The will's cover note: not registered ({SOURCE})")
        await india_estate.record_nomination(s, firm, by=BY, person_id=subir.id, asset_kind="bank_account", label="Subir Banerjee SBI savings",
                                             status="nominated", nominees="Rina Banerjee:spouse:50; Aniket Banerjee:son:50",
                                             document=f"Bank's nomination form DA1 ({SOURCE})")
        n += 3

    # Elaine D'Souza's demat: a signed opt-out, not an open nomination.
    elaine = people.get("Elaine D'Souza")
    if elaine is not None:
        account = (await s.execute(select(Account).join(Mandate, Mandate.id == Account.mandate_id).where(
            Mandate.person_id == elaine.id, Account.status == "active"))).scalars().first()
        if account is not None:
            await india_estate.record_nomination(s, firm, by=BY, person_id=elaine.id, asset_kind="demat", label=account.name, status="opted_out",
                                                 declaration_on=today - timedelta(days=200),
                                                 document=f"Signed opt-out declaration, filed with the depository participant ({SOURCE})")
            n += 1
    return n
