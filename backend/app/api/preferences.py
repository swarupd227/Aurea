"""What people asked the agents to remember (B-204), for the Workforce page. Making or ending one is the
conversation's `remember_preference` / `end_preference` behind its Confirm card."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import decision_memory
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/preferences", tags=["preferences"])
READERS = (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.BRANCH_LEADER, UserRole.COMPLIANCE,
           UserRole.OPERATIONS, UserRole.RESEARCH_CIO, UserRole.ADMIN)


@router.get("")
async def read(user: User = Depends(require_roles(*READERS)), firm: Firm = Depends(current_firm),
               session: AsyncSession = Depends(get_db)) -> dict:
    prefs = await decision_memory.listing(session, firm)
    holds = sum(1 for p in prefs if p["kind"] == "hold")
    return {"preferences": prefs, "holds": holds, "notes": len(prefs) - holds}
