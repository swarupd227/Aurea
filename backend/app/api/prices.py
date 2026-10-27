"""How fresh the prices under the book are, and fetching them now.

Reading is open to staff. A refresh revalues holdings, which changes client balances and the
fees charged on them, so it is limited to operations, the portfolio team and administrators
(who already run "Sync now" on the connector). The agent-facing tool is the same action behind
a confirmation card.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import price_freshness
from app.core.db import get_db
from app.core.decision_rights import STAFF
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/prices", tags=["prices"])
StaffDep = Depends(require_roles(*STAFF))
REFRESHERS = frozenset({UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM, UserRole.ADMIN})


@router.get("/freshness")
async def freshness(user: User = StaffDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return {**await price_freshness.price_freshness(db, firm.id), "can_refresh": user.role in REFRESHERS}


@router.post("/refresh")
async def refresh(user: User = StaffDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    if user.role not in REFRESHERS:
        raise HTTPException(status_code=403, detail="Fetching prices revalues holdings, which changes client "
                            "balances and fees, so only operations, the portfolio team or an administrator may do it.")
    try:
        return await price_freshness.refresh_prices(db, firm.id)
    except price_freshness.RefreshRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc))
