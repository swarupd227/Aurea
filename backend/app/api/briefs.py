"""The signed-in person's own brief profile, and what they open on a brief (B-203).

Changing the profile goes through the conversation's `set_brief_profile` and its Confirm card. These routes read the
profile and its suggestions for the Meetings page, and record a section opened or closed on a brief, which only ever
becomes a suggestion.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import brief_profile
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/briefs", tags=["briefs"])
RMS = (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.BRANCH_LEADER)


class SectionEvent(BaseModel):
    kind: str
    section: str
    action: str


@router.get("/profile")
async def read_profile(user: User = Depends(require_roles(*RMS)), firm: Firm = Depends(current_firm),
                       session: AsyncSession = Depends(get_db)) -> dict:
    out = await brief_profile.get(session, firm.id, user.id)
    out["suggestions"] = await brief_profile.suggestions(session, firm.id, user.id, out)
    out["sections"] = {kind: [{"key": k, "label": label} for k, label in s] for kind, s in brief_profile.SECTIONS.items()}
    return out


class ProfileIn(BaseModel):
    lead: list[str] | None = None
    length: str | None = None
    strengths: list[str] | None = None


@router.put("/profile")
async def set_profile(body: ProfileIn, user: User = Depends(require_roles(*RMS)), firm: Firm = Depends(current_firm),
                      session: AsyncSession = Depends(get_db)) -> dict:
    """The page's form; in the work pane this is the conversation's set_brief_profile and its Confirm card."""
    try:
        return await brief_profile.save(session, firm.id, user, lead=body.lead, length=body.length, strengths=body.strengths)
    except brief_profile.BriefProfileError as exc:
        raise HTTPException(422, str(exc))


@router.post("/events")
async def record_event(body: SectionEvent, user: User = Depends(require_roles(*RMS)), firm: Firm = Depends(current_firm),
                       session: AsyncSession = Depends(get_db)) -> dict:
    try:
        await brief_profile.record_event(session, firm.id, user.id, kind=body.kind, section=body.section, action=body.action)
    except brief_profile.BriefProfileError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True}
