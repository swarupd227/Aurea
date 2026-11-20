"""Give the demonstration firm a few recorded links between its clients and its prospects (B-200).

The demo firm's clients and prospects are invented, and so are these links: each is marked as demonstration data in
its evidence and its source, and recorded by "Demonstration seed" rather than in a member of staff's name. They follow
what the seeded contacts' notes already say (the Chens referred Hemi Walker, Dana Okafor referred Rohan Mehta, the
Aroha Whānau Trust referred the Ngatas) and add two more, so the Ways in panel and the Warm Introduction agent have
something to show. Olivia Harris is left with none, to show a prospect nobody is recorded as knowing.

Only the firm whose slug is "demo"; idempotent: a link already on file is left as it is.

    python -m seed.introductions_demo_backfill
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.aurea_core import firm_time
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.models.crm import CrmContact
from app.models.graph import LegalEntity, Person, RelationshipEdge
from app.models.tenant import Firm

# (from type, from name, kind, to type, to name, closeness, how)
_LINKS = [
    ("person", "Wei Chen", "referred", "contact", "Hemi Walker", "known", "family friends; the Chens passed on his name"),
    ("person", "Anders Müller", "colleague", "contact", "James Whitaker", "known", "on the chamber of commerce board together"),
    ("contact", "Dana Okafor, CPA", "referred", "contact", "Rohan Mehta", None, "his accountant"),
    ("entity", "Aroha Whānau Trust", "referred", "contact", "Sam & Aroha Ngata", "close", "whānau members of the trust"),
    ("person", "Binh Nguyen", "knows", "contact", "Grace Liu", "met", "she is the Nguyens' family GP"),
]


async def backfill() -> None:
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one_or_none()
        if firm is None:
            print("No demo firm; nothing to do.")
            return
        people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
        entities = {e.name: e for e in (await s.execute(select(LegalEntity).where(LegalEntity.firm_id == firm.id))).scalars().all()}
        contacts = {c.full_name: c for c in (await s.execute(select(CrmContact).where(CrmContact.firm_id == firm.id))).scalars().all()}
        pick = {"person": people, "entity": entities, "contact": contacts}
        edges = (await s.execute(select(RelationshipEdge).where(RelationshipEdge.firm_id == firm.id))).scalars().all()
        have = {(e.kind, e.from_id, e.to_id) for e in edges}
        today = firm_time.today(firm).isoformat()
        added = skipped = 0
        for ftype, fname, kind, ttype, tname, close, how in _LINKS:
            a, b = pick[ftype].get(fname), pick[ttype].get(tname)
            if a is None or b is None:
                print(f"  skipped {fname} -> {tname}: not at this firm")
                skipped += 1
                continue
            if (kind, a.id, b.id) in have or (kind, b.id, a.id) in have:
                continue
            s.add(RelationshipEdge(firm_id=firm.id, kind=kind, from_type=ftype, from_id=a.id, to_type=ttype, to_id=b.id,
                                   attributes={"closeness": close, "how": how, "source": "demonstration",
                                               "evidence": "Demonstration data: invented for the demo firm, as its clients are",
                                               "recorded_by": "Demonstration seed", "recorded_on": today}))
            added += 1
        await s.commit()
        print(f"\nDemo introductions: {added} link(s) added, {skipped} skipped.\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
