"""Fix internal-codename leaks in already-seeded display data. CLAUDE.md: "Aurea"
survives only as the internal codename (the Python package, Azure resources, the
database) — "Nothing a user sees should say Aurea or Aurera." `seed/run.py` and
`composites_backfill.py` seed correctly named data for a *new* deployment; this repairs
the rows an already-seeded firm has (model portfolio names, the composite names/
inclusion criteria derived from them, trust-deed text, a party's legal name, and
research document authors).

Idempotent: only touches rows that still carry one of the old strings.

    python -m seed.branding_backfill
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.composites import Composite
from app.models.knowledge import ResearchDocument
from app.models.onboarding import OnboardingDocument, OnboardingParty
from app.models.portfolio import ModelPortfolio

log = get_logger("aurea.seed.branding_backfill")

_MODEL_RENAMES = {"Aurera Balanced": "Core Balanced", "Aurera Growth": "Core Growth"}
_AUTHOR_RENAMES = {
    "Aurera Investment Committee": "Investment Committee",
    "Aurera Advice Standards": "Advice Standards",
    "Aurera Research": "Research Desk",
}
_TRUSTEE_OLD = "Aurera Trustees Ltd"
_TRUSTEE_NEW = "Meridian Trustees Ltd"


async def backfill() -> None:
    async with SessionLocal() as s:
        counts: dict[str, int] = {}

        for m in (await s.execute(select(ModelPortfolio))).scalars().all():
            if m.name in _MODEL_RENAMES:
                m.name = _MODEL_RENAMES[m.name]
                counts["model_portfolios"] = counts.get("model_portfolios", 0) + 1

        for c in (await s.execute(select(Composite))).scalars().all():
            changed = False
            for old, new in _MODEL_RENAMES.items():
                if old in (c.name or ""):
                    c.name = c.name.replace(old, new)
                    changed = True
                if old in (c.inclusion_criteria or ""):
                    c.inclusion_criteria = c.inclusion_criteria.replace(old, new)
                    changed = True
            if changed:
                counts["composites"] = counts.get("composites", 0) + 1

        for d in (await s.execute(select(OnboardingDocument))).scalars().all():
            if _TRUSTEE_OLD in (d.raw_text or ""):
                d.raw_text = d.raw_text.replace(_TRUSTEE_OLD, _TRUSTEE_NEW)
                counts["onboarding_documents"] = counts.get("onboarding_documents", 0) + 1

        parties = (await s.execute(
            select(OnboardingParty).where(OnboardingParty.legal_name == _TRUSTEE_OLD)
        )).scalars().all()
        for p in parties:
            p.legal_name = _TRUSTEE_NEW
            counts["onboarding_parties"] = counts.get("onboarding_parties", 0) + 1

        for r in (await s.execute(select(ResearchDocument))).scalars().all():
            if r.author in _AUTHOR_RENAMES:
                r.author = _AUTHOR_RENAMES[r.author]
                counts["research_documents"] = counts.get("research_documents", 0) + 1

        await s.commit()
        print(f"\nBranding backfill: {counts or 'nothing to fix'}\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
