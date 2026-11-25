"""A simulated WhatsApp Business API, standing in for the real one (L100 India edition, B-301 step 2).

The real feature is `aurea_core/whatsapp.py`: the client's `whatsapp` opt-in in the consent registry, the 24-hour window, the approved
templates compliance recorded, both texts on the Confirm card, and each delivery status recorded as the channel reports it. This
package is only the channel: a provider registered at `whatsapp.register` which, for a firm that opted in, accepts a message and
reports statuses back (sent, delivered, read, or failed for a number that is not on WhatsApp). Nothing is claimed delivered that the
provider did not report.

It reaches nobody: no message leaves the platform. Seeding opts the firm in and writes the client messages that open a window (an
inbound event with no words kept: an open window for the Sharmas, a closed one for the Banerjees), every row tagged
`simulated:whatsapp_channel`. Statuses the provider reports when a person sends are tagged the same way. Discarding deletes every row
it tagged and the opt-in; the consents and templates a person recorded stay, because they are the firm's own records.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import delete, func, select

from app.aurea_core import client_language, whatsapp
from app.core.db import utcnow
from app.models.graph import Household
from app.models.india_batch_b import MessageDelivery
from app.models.tenant import Firm
from app.simulations import Simulation, enabled_for, opt_in, opt_out, register, tag

KEY = "whatsapp_channel"
SOURCE = tag(KEY)
NAME = "Simulated WhatsApp Business API (demonstration)"
NOT_ON_WHATSAPP = "Kapoor"             # a household whose number the simulated channel says is not on WhatsApp

# (household, hours ago the client last wrote on WhatsApp)
INBOUND = [("The Sharma Family", 2), ("The Banerjee Household", 72)]


class SimulatedWhatsapp:
    key = SOURCE
    name = NAME
    simulated = True

    def enabled_for(self, firm) -> bool:
        return enabled_for(firm, KEY) and client_language.applies(firm)

    async def send(self, session, firm, household, text: str, template: str | None) -> list[dict]:
        now = utcnow()
        ref = f"wamid.SIM.{uuid.uuid5(uuid.NAMESPACE_URL, f'{household.id}:{text}').int % 10**10:010d}"
        if NOT_ON_WHATSAPP in household.name:
            return [{"status": "failed", "at": now, "ref": ref, "detail": "The number is not on WhatsApp (simulated)"}]
        out = [{"status": "sent", "at": now, "ref": ref}, {"status": "delivered", "at": now + timedelta(seconds=2), "ref": ref}]
        if len(text) % 2 == 0:
            out.append({"status": "read", "at": now + timedelta(seconds=40), "ref": ref})
        return out


PROVIDER = SimulatedWhatsapp()


def install() -> None:
    whatsapp.register(PROVIDER)


async def demo_firms(session) -> list:
    return list((await session.execute(select(Firm).where(Firm.slug == "india-demo"))).scalars().all())


async def seed(session, firm) -> dict:
    if not client_language.applies(firm):
        return {"inbound": 0, "skipped": "not an Indian firm"}
    if whatsapp.PROVIDERS.get(SOURCE) is not PROVIDER:
        install()
    await opt_in(session, firm, KEY)
    have = (await session.execute(select(func.count()).select_from(MessageDelivery).where(
        MessageDelivery.firm_id == firm.id, MessageDelivery.source_system == SOURCE))).scalar_one()
    if have:
        return {"inbound": 0}
    homes = {h.name: h for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    now, added = utcnow(), 0
    for name, hours in INBOUND:
        h = homes.get(name)
        if h is None:
            continue
        at = now - timedelta(hours=hours)
        session.add(MessageDelivery(firm_id=firm.id, household_id=h.id, event="inbound", channel="whatsapp", happened_at=at, created_at=at,
                                    detail="The client wrote on WhatsApp (simulated); no words are kept", source_system=SOURCE))
        added += 1
    await session.flush()
    return {"inbound": added}


async def discard(session, firm) -> dict:
    rows = (await session.execute(select(func.count()).select_from(MessageDelivery).where(
        MessageDelivery.firm_id == firm.id, MessageDelivery.source_system == SOURCE))).scalar_one()
    if rows:
        await session.execute(delete(MessageDelivery).where(MessageDelivery.firm_id == firm.id, MessageDelivery.source_system == SOURCE))
    was = enabled_for(firm, KEY)
    if was:
        await opt_out(session, firm, KEY)
    await session.flush()
    return {"deliveries": rows, "opted_out": was}


register(Simulation(
    key=KEY, name="Simulated WhatsApp Business API", stands_for="The WhatsApp Business Platform, as a firm sends and receives through it",
    seam="aurea_core.whatsapp.register, gated by the client's whatsapp consent, the 24-hour window and the approved templates (B-301)",
    install=install, seed=seed, discard=discard, firms=demo_firms,
    notes=["Inbound events and statuses are invented for the Indian demonstration firm and tagged simulated:whatsapp_channel; no ledger or audit entry from the seed.",
           "Nothing leaves the platform: the simulated channel reports statuses and reaches nobody."],
))
