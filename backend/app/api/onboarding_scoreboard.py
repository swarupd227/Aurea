"""The onboarding scoreboard, for staff. Survey answers are recorded through the conversation's tool."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/onboarding-scoreboard", tags=["onboarding-scoreboard"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.COMPLIANCE, UserRole.OPERATIONS, UserRole.BRANCH_LEADER))


async def scoreboard(db: AsyncSession, firm: Firm) -> dict:
    from app.api.onboarding import _first_fills, states_overview
    from app.aurea_core import firm_time, onboarding_scoreboard, transfer_file
    from app.models.onboarding import OnboardingCase, OnboardingParty
    from app.models.onboarding_feedback import OnboardingFeedback

    cases = list((await db.execute(select(OnboardingCase).where(OnboardingCase.firm_id == firm.id))).scalars().all())
    parties = list((await db.execute(select(OnboardingParty).where(OnboardingParty.firm_id == firm.id))).scalars().all())
    feedback = list((await db.execute(select(OnboardingFeedback).where(OnboardingFeedback.firm_id == firm.id))).scalars().all())
    staff = (await db.execute(select(User.id).where(User.firm_id == firm.id, User.role == UserRole.OPERATIONS))).scalars().all()
    stalled = int((firm.settings or {}).get("onboarding_stalled_days") or onboarding_scoreboard.STALLED_DAYS)
    return onboarding_scoreboard.build(overview=await states_overview(db, firm), cases=cases, fills=await _first_fills(db, cases), parties=parties,
                                       rejects_by_code=(await transfer_file.for_case(db, firm.id))["rejects_by_code"], feedback=feedback,
                                       operations_staff=len(staff), today=firm_time.today(firm), stalled_days=stalled)


@router.get("")
async def read(user: User = ReaderDep, firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    return await scoreboard(session, firm)
