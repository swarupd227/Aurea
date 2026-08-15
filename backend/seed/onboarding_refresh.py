"""Top up an existing demo database with the richer onboarding cases.

`seed.run` short-circuits when the demo firm already exists, so a database seeded before
these cases were added will never receive them. This adds only what is missing.

    python -m seed.onboarding_refresh

Safe to run repeatedly — `_acquire_onboard` skips any case whose prospect name is already
present, and the acquired-book batch is guarded too.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.onboarding import BeneficialOwner, OnboardingCase
from app.models.tenant import Firm

from seed.run import _acquire_onboard

log = get_logger("aurea.seed.onboarding_refresh")

# Cases seeded before registration_type / risk tiers existed. Backfilling only fills
# fields that are still empty, plus created_at and sla_days so SLA state is meaningful.
# Agent output — proposal, screening, screening_log, status — is never touched.
_BACKFILL: dict[str, dict] = {
    "Daniel Okonkwo": {
        "registration_type": "individual",
        "aml_risk_tier": "low", "aml_risk_score": 18.0, "edd_status": "cdd",
        "sla_days": 5, "age_days": 2,          # 2 of 5 days -> on_track
        "intake_extra": {"beneficiary_name": "Amara Okonkwo"},
    },
    "Sokolov Family Trust": {
        "registration_type": "trust",
        "aml_risk_tier": "high", "aml_risk_score": 78.0, "edd_status": "edd_pending",
        "sla_days": 10, "age_days": 8,         # 8 of 10 days -> at_risk
        "intake_extra": {
            "source_of_funds": "Proceeds of 2019 sale of Sokolov Manufacturing OAO",
        },
        "beneficial_owners": [
            ("Viktor Sokolov", 55.0, True, "1962-04-11"),
            ("Anna Sokolov", 45.0, False, "1968-09-02"),
        ],
    },
}


async def _backfill(s, firm) -> list[str]:
    """Fill the gaps on cases that predate the richer seed. Returns names touched."""
    now = datetime.now(timezone.utc)
    touched: list[str] = []

    for name, spec in _BACKFILL.items():
        case = (await s.execute(
            select(OnboardingCase).where(
                OnboardingCase.firm_id == firm.id,
                OnboardingCase.prospect_name == name,
            )
        )).scalar_one_or_none()
        if not case:
            continue

        changed = []
        # Only fill what is genuinely empty — never overwrite a real value.
        for field in ("registration_type", "aml_risk_tier", "aml_risk_score", "edd_status"):
            if field in spec and getattr(case, field, None) in (None, ""):
                setattr(case, field, spec[field])
                changed.append(field)

        # Set deliberately so SLA state is demonstrable rather than all-breached.
        if spec.get("sla_days") and case.sla_days != spec["sla_days"]:
            case.sla_days = spec["sla_days"]
            changed.append("sla_days")
        if spec.get("age_days") is not None:
            case.created_at = now - timedelta(days=spec["age_days"])
            changed.append("created_at")

        # Merge intake keys without clobbering anything already captured.
        extra = spec.get("intake_extra") or {}
        if extra:
            intake = dict(case.intake or {})
            added = {k: v for k, v in extra.items() if not intake.get(k)}
            if added:
                intake.update(added)
                case.intake = intake          # reassign so SQLAlchemy sees the JSON change
                changed.append(f"intake({','.join(added)})")

        # Beneficial owners. Existing names are checked once, up front — querying inside
        # the loop lets autoflush surface the row just added and stop the remaining ones.
        wanted_bos = spec.get("beneficial_owners", [])
        if wanted_bos:
            have = set((await s.execute(
                select(BeneficialOwner.legal_name).where(BeneficialOwner.case_id == case.id)
            )).scalars().all())
            for bo_name, pct, control, dob in wanted_bos:
                if bo_name in have:
                    continue
                s.add(BeneficialOwner(firm_id=firm.id, case_id=case.id, legal_name=bo_name,
                                      ownership_pct=pct, is_control_person=control, dob=dob,
                                      address="Geneva, Switzerland"))
                changed.append(f"BO:{bo_name}")

        if changed:
            touched.append(name)
            log.info("backfill_case", name=name, fields=changed)
        else:
            log.info("backfill_skip", name=name, msg="already complete")

    return touched


async def refresh() -> None:
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one_or_none()
        if not firm:
            log.error("refresh_no_firm", msg="Demo firm not found — run seed.run first")
            return

        before = len((await s.execute(
            select(OnboardingCase.id).where(OnboardingCase.firm_id == firm.id)
        )).scalars().all())

        await _acquire_onboard(s, firm)
        touched = await _backfill(s, firm)
        await s.commit()
        if touched:
            print(f"\nBackfilled {len(touched)} pre-existing case(s): {', '.join(touched)}")

        cases = (await s.execute(
            select(OnboardingCase).where(OnboardingCase.firm_id == firm.id)
        )).scalars().all()
        bo_count = len((await s.execute(
            select(BeneficialOwner.id).where(BeneficialOwner.firm_id == firm.id)
        )).scalars().all())

        log.info("refresh_done", added=len(cases) - before, total=len(cases),
                 beneficial_owners=bo_count)

        print(f"\nOnboarding cases: {before} -> {len(cases)}  (+{len(cases) - before})")
        print(f"Beneficial owners: {bo_count}\n")
        print(f"{'prospect':34}{'registration':20}{'tier':8}{'status':10}")
        print("-" * 72)
        for c in sorted(cases, key=lambda x: x.prospect_name):
            print(f"{c.prospect_name:34}{str(c.registration_type or '—'):20}"
                  f"{str(c.aml_risk_tier or '—'):8}{str(c.status):10}")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(refresh())
