"""The firm's autonomy policies, read. A change goes through the conversation's set_autonomy_policy, behind its Confirm card,
or the Admin page."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import autonomy
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/autonomy", tags=["autonomy"])
STAFF = (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.RESEARCH_CIO, UserRole.COMPLIANCE,
         UserRole.OPERATIONS, UserRole.BRANCH_LEADER, UserRole.ADMIN)


@router.get("/policies")
async def read(user: User = Depends(require_roles(*STAFF)), firm: Firm = Depends(current_firm),
               session: AsyncSession = Depends(get_db)) -> dict:
    return await autonomy.for_firm(session, firm)
