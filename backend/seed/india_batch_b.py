"""Languages and WhatsApp on the Indian demonstration firm (B-301). The DigiLocker and eSign offers (B-302) and the WhatsApp channel's inbound
events are written by the simulations, not here: `python -m app.simulations seed whatsapp_channel india_stack`.

Every record is invented for the demonstration firm and says so in its evidence; the approval references are invented and so is the wording. Run
from `seed.india_demo` when the firm is built and from `seed.india_demo_backfill` on a firm built before it; it adds nothing where a language is
already on file.

  The Sharmas       read Hindi (their request is on file); they opted in to WhatsApp
  The Banerjees     read Bengali; they opted in to WhatsApp
  The Kapoors       opted in to WhatsApp, and the simulated channel reports their number is not on it
  The D'Souzas      no language and no opt-in: the firm's English, the secure thread only
  Compliance        a template for an annual review reminder, approved in English and in Hindi
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import client_language, consents, firm_time, whatsapp
from app.core.security import UserRole
from app.models.graph import Household
from app.models.india_batch_b import ClientLanguage
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: languages and WhatsApp (demonstration)"
LANGUAGES = [("The Sharma Family", "hi", "Rajesh Sharma"), ("The Banerjee Household", "bn", "Subir Banerjee")]
OPT_INS = [("The Sharma Family", "Rajesh Sharma"), ("The Banerjee Household", "Subir Banerjee"), ("The Kapoor Family Trust", "Nandini Kapoor")]
TEMPLATE = {
    "en": "Hello {name}, it is time for your annual review. Please reply here or call your adviser to choose a day.",
    "hi": "नमस्ते {name}, आपकी वार्षिक समीक्षा का समय हो गया है। कृपया यहाँ उत्तर दें या दिन चुनने के लिए अपने सलाहकार को फ़ोन करें।",
}


async def add(s: AsyncSession, firm: Firm) -> int:
    if (await s.execute(select(ClientLanguage.id).where(ClientLanguage.firm_id == firm.id).limit(1))).first():
        return 0
    homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    n = 0
    for name, code, who in LANGUAGES:
        h = homes.get(name)
        if h is not None:
            await client_language.record(s, firm, UserRole.ADVISER, by=BY, household_id=h.id, language=code, asked_by=who,
                                         evidence=f"The client's request in their first meeting note ({SOURCE})")
            n += 1
    for name, who in OPT_INS:
        h = homes.get(name)
        if h is not None:
            await consents.record(s, firm.id, by=BY, case_id=None, household_id=h.id, kind="whatsapp", status="granted", given_by=who, method="portal",
                                  evidence=f"WhatsApp opt-in on the client form ({SOURCE})", given_on=firm_time.today(firm))
            n += 1
    for code, body in TEMPLATE.items():
        await whatsapp.record_template(s, firm, UserRole.COMPLIANCE, by=BY, name="annual_review_reminder", language=code, body=body,
                                       provider_ref=f"DEMO-TPL-{code.upper()}-0001 (invented)")
        n += 1
    return n
