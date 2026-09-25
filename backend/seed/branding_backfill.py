"""Fix internal-codename leaks in already-seeded display data. CLAUDE.md: "Aurea"
survives only as the internal codename (the Python package, Azure resources, the
database) — "Nothing a user sees should say Aurea or Aurera." `seed/run.py` and
`composites_backfill.py` seed correctly named data for a *new* deployment; this repairs
the rows an already-seeded firm has.

The deployed demo firm turns out to predate the "Aurea" → "Astra for Wealth" rebrand
in the codebase entirely: `Firm.name`, `Firm.legal_name`, `Firm.branding.logo_text` and
the admin user's `full_name` were never migrated, because `seed/run.py` skips a firm
that already exists. Fixes those too, alongside the model-portfolio/composite/
trust-deed/research-author fixes from the first pass of this backfill (kept here as a
second hop, since a firm that already ran the first version has "Meridian Trustees
Ltd" on file, not the original "Aurera Trustees Ltd" — renamed again, to "Cornerstone
Trustees Ltd", to stop it colliding with the unrelated seeded "Meridian Capital
Partners LLC" onboarding case).

Idempotent: only touches rows that still carry one of the old strings.

    python -m seed.branding_backfill
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.composites import Composite
from app.models.identity import User
from app.models.knowledge import ResearchDocument
from app.models.onboarding import OnboardingDocument, OnboardingParty
from app.models.portfolio import ModelPortfolio
from app.models.tenant import Firm

log = get_logger("aurea.seed.branding_backfill")

_MODEL_RENAMES = {"Aurera Balanced": "Core Balanced", "Aurera Growth": "Core Growth"}
_AUTHOR_RENAMES = {
    "Aurera Investment Committee": "Investment Committee",
    "Aurera Advice Standards": "Advice Standards",
    "Aurera Research": "Research Desk",
}
# Applied in order, so a firm already on the first-pass value still lands on the final one.
_TRUSTEE_RENAMES = [("Aurera Trustees Ltd", "Cornerstone Trustees Ltd"),
                     ("Meridian Trustees Ltd", "Cornerstone Trustees Ltd")]
_FIRM_RENAMES = {"Aurera": "Astra for Wealth"}
_ADMIN_FULL_NAME_RENAMES = {"Aurea Administrator": "Platform Admin"}


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
            changed = False
            for old, new in _TRUSTEE_RENAMES:
                if old in (d.raw_text or ""):
                    d.raw_text = d.raw_text.replace(old, new)
                    changed = True
            if changed:
                counts["onboarding_documents"] = counts.get("onboarding_documents", 0) + 1

        old_trustee_names = {old for old, _ in _TRUSTEE_RENAMES}
        parties = (await s.execute(
            select(OnboardingParty).where(OnboardingParty.legal_name.in_(old_trustee_names))
        )).scalars().all()
        for p in parties:
            p.legal_name = "Cornerstone Trustees Ltd"
            counts["onboarding_parties"] = counts.get("onboarding_parties", 0) + 1

        for r in (await s.execute(select(ResearchDocument))).scalars().all():
            if r.author in _AUTHOR_RENAMES:
                r.author = _AUTHOR_RENAMES[r.author]
                counts["research_documents"] = counts.get("research_documents", 0) + 1

        for firm in (await s.execute(select(Firm))).scalars().all():
            changed = False
            if firm.name in _FIRM_RENAMES:
                firm.name = _FIRM_RENAMES[firm.name]
                changed = True
            if firm.legal_name in _FIRM_RENAMES:
                firm.legal_name = _FIRM_RENAMES[firm.legal_name]
                changed = True
            logo_text = (firm.branding or {}).get("logo_text")
            if logo_text in _FIRM_RENAMES:
                firm.branding = {**firm.branding, "logo_text": _FIRM_RENAMES[logo_text]}
                changed = True
            if changed:
                counts["firms"] = counts.get("firms", 0) + 1

        for u in (await s.execute(select(User))).scalars().all():
            if u.full_name in _ADMIN_FULL_NAME_RENAMES:
                u.full_name = _ADMIN_FULL_NAME_RENAMES[u.full_name]
                counts["users"] = counts.get("users", 0) + 1

        await s.commit()
        print(f"\nBranding backfill: {counts or 'nothing to fix'}\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
