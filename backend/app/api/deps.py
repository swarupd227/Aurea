"""Shared API dependencies."""
from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.identity import User
from app.models.tenant import Firm


async def current_firm(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Firm:
    firm = await db.get(Firm, user.firm_id)
    if firm is None:
        raise HTTPException(status_code=404, detail="Firm not found")
    return firm
