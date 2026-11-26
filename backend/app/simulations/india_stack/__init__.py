"""A simulated DigiLocker and eSign provider, standing in for the real services (L100 India edition, B-302).

The real feature is `aurea_core/india_stack.py`: a document reference a provider delivers (issuer, type, masked number, the day it was
fetched) that a person confirms into the client's KYC with the mode `digilocker`, and an eSign envelope for the advisory agreement
that a person confirms into the consent registry as `advisory_agreement`, method `e_signature`, with the envelope as evidence. This
package is only the provider, registered at `india_stack.register`: for a firm that opted in it returns invented documents for an
invented client and an envelope that reads signed. It reaches no service, and an Aadhaar number it returns is always masked.

Seeding opts the firm in and offers one DigiLocker document and one signed envelope to a person of the Indian demonstration firm, every
row tagged `simulated:india_stack`; nothing is confirmed into a record, because only a person does that. The KRA and CKYC lookup is not
simulated: it needs a vendor (B-272). Discarding deletes every offer it wrote and the opt-in; a KYC record or a consent a person already
confirmed from an offer stays, naming "simulated" in its evidence.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select

from app.aurea_core import firm_time, india_stack, kyc_india
from app.core.db import utcnow
from app.models.graph import Household, Person
from app.models.india_batch_b import StackItem
from app.models.tenant import Firm
from app.simulations import Simulation, enabled_for, opt_in, opt_out, register, tag

KEY = "india_stack"
SOURCE = tag(KEY)
NAME = "Simulated DigiLocker and eSign (demonstration)"
# (household, person): who the demonstration offers go to
OFFERS = [("The Banerjee Household", "Subir Banerjee")]


def _n(*parts, mod: int) -> int:
    return uuid.uuid5(uuid.NAMESPACE_URL, ":".join(str(p) for p in parts)).int % mod


class SimulatedStack:
    key = SOURCE
    name = NAME
    simulated = True

    def enabled_for(self, firm) -> bool:
        return enabled_for(firm, KEY) and kyc_india.applies(firm)

    async def pull_documents(self, session, firm, person) -> list[dict]:
        today = firm_time.today(firm)
        tail = f"{_n(person.id, mod=10000):04d}"
        return [{"reference": f"in.gov.simulated/DEMO/AADHAAR/{tail}", "issuer": "UIDAI (simulated)", "doc_type": "aadhaar",
                 "masked_number": f"XXXX XXXX {tail}", "fetched_on": today},
                {"reference": f"in.gov.simulated/DEMO/PAN/{tail}", "issuer": "Income Tax Department (simulated)", "doc_type": "pan",
                 "masked_number": f"XXXXX{tail}X", "fetched_on": today}]

    async def send_for_signature(self, session, firm, person, document_name: str) -> dict:
        ref = f"SIM-ESIGN-{_n(person.id, document_name, mod=10**8):08d}"
        # The invented template declares what it includes; the real provider declares its own, or nothing (B-324).
        return {"reference": ref, "state": "signed", "signed_on": firm_time.today(firm), "includes": ["annexure_a", "mitc"]}


PROVIDER = SimulatedStack()


def install() -> None:
    india_stack.register(PROVIDER)


async def demo_firms(session) -> list:
    return list((await session.execute(select(Firm).where(Firm.slug == "india-demo"))).scalars().all())


async def seed(session, firm) -> dict:
    if not kyc_india.applies(firm):
        return {"offers": 0, "skipped": "not an Indian firm"}
    if india_stack.PROVIDERS.get(SOURCE) is not PROVIDER:
        install()
    await opt_in(session, firm, KEY)
    have = (await session.execute(select(func.count()).select_from(StackItem).where(StackItem.firm_id == firm.id, StackItem.source_system == SOURCE))).scalar_one()
    if have:
        return {"offers": 0}
    homes = {h.name: h for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    added = 0
    for household, who in OFFERS:
        h = homes.get(household)
        p = (await session.execute(select(Person).where(Person.household_id == h.id, Person.full_name == who))).scalars().first() if h else None
        if p is None:
            continue
        for d in await PROVIDER.pull_documents(session, firm, p):
            if d["doc_type"] != "aadhaar":
                continue
            session.add(StackItem(firm_id=firm.id, household_id=h.id, person_id=p.id, kind="digilocker", state="offered", provider=NAME, reference=d["reference"],
                                  issuer=d["issuer"], doc_type=d["doc_type"], masked_number=d["masked_number"], fetched_on=d["fetched_on"],
                                  recorded_by="simulation: india_stack", source_system=SOURCE, created_at=utcnow()))
            added += 1
        env = await PROVIDER.send_for_signature(session, firm, p, "Investment advisory agreement")
        session.add(StackItem(firm_id=firm.id, household_id=h.id, person_id=p.id, kind="esign", state="offered", provider=NAME, reference=env["reference"],
                              document_name="Investment advisory agreement", signer=p.full_name, provider_state=env["state"], fetched_on=env["signed_on"],
                              detail=india_stack._declared(env),
                              recorded_by="simulation: india_stack", source_system=SOURCE, created_at=utcnow()))
        added += 1
    await session.flush()
    return {"offers": added}


async def discard(session, firm) -> dict:
    rows = (await session.execute(select(func.count()).select_from(StackItem).where(StackItem.firm_id == firm.id, StackItem.source_system == SOURCE))).scalar_one()
    if rows:
        await session.execute(delete(StackItem).where(StackItem.firm_id == firm.id, StackItem.source_system == SOURCE))
    was = enabled_for(firm, KEY)
    if was:
        await opt_out(session, firm, KEY)
    await session.flush()
    return {"items": rows, "opted_out": was}


register(Simulation(
    key=KEY, name="Simulated DigiLocker and eSign", stands_for="DigiLocker (through a registered requester) and an Aadhaar eSign service provider",
    seam="aurea_core.india_stack.register: a document a person confirms into the KYC, an envelope into the consent registry (B-302)",
    install=install, seed=seed, discard=discard, firms=demo_firms,
    notes=["Documents and envelopes are invented for the Indian demonstration firm and tagged simulated:india_stack; no ledger or audit entry from the seed.",
           "An Aadhaar number is always masked; the KRA and CKYC lookup is not simulated (B-272)."],
))
