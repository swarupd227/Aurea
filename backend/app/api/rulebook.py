"""The firm's rulebook and agent controls, read. A change is compliance's (an administrator's for the platform settings),
through the conversation's tools behind a Confirm card or the Regulatory and Agent controls pages, with a reason."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import rulebook
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/rulebook", tags=["rulebook"])


@router.get("")
async def read(user: User = Depends(require_roles(UserRole.COMPLIANCE, UserRole.BRANCH_LEADER, UserRole.ADMIN)),
               firm: Firm = Depends(current_firm), session: AsyncSession = Depends(get_db)) -> dict:
    return await rulebook.for_firm(session, firm)
