"""Seed a couple of example advisor-defined skills so the Skills page has content. Idempotent."""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.skill import Skill
from app.models.tenant import Firm

EXAMPLES = [
    {"name": "Cash drag finder",
     "description": "Spot clients sitting on too much cash.",
     "instruction": "Find clients holding more than 12% of their portfolio in cash and draft a short, "
                    "warm note suggesting we put it to work toward their goals.",
     "scope": "book", "output_kind": "note", "default_tier": "tier_1"},
    {"name": "Concentration watch",
     "description": "Flag single-name concentration risk.",
     "instruction": "Flag any client whose single largest holding is more than 25% of their portfolio "
                    "and suggest a tax-aware, staged diversification.",
     "scope": "book", "output_kind": "insight", "default_tier": "tier_1"},
    {"name": "Next-gen outreach",
     "description": "Engage heirs early.",
     "instruction": "Identify households that include a next-generation family member and propose a warm "
                    "introduction so we engage them before the wealth transfers.",
     "scope": "book", "output_kind": "note", "default_tier": "tier_1"},
]


async def seed_skills(session, firm) -> int:
    rows = {s.name: s for s in (await session.execute(
        select(Skill).where(Skill.firm_id == firm.id))).scalars().all()}
    added = 0
    for e in EXAMPLES:
        if e["name"] in rows:
            rows[e["name"]].visibility = "public"  # ensure examples are the firm library
            continue
        session.add(Skill(firm_id=firm.id, visibility="public", **e))
        added += 1
    await session.flush()
    return added


async def _main() -> None:
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one()
        n = await seed_skills(s, firm)
        await s.commit()
        print(f"skills_seed: added {n}")


if __name__ == "__main__":
    asyncio.run(_main())
