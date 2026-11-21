"""The ways in to a prospect, for the Growth pipeline page (B-200). In the work pane every change is the conversation's
`record_relationship` behind its Confirm card; on the page the form records directly, with the same checks."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import introductions
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/introductions", tags=["introductions"])
RMS = (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.BRANCH_LEADER)


class LinkIn(BaseModel):
    from_type: str
    from_id: str | None = None
    from_name: str | None = None
    to_type: str
    to_id: str | None = None
    to_name: str | None = None
    kind: str | None = None
    closeness: str | None = None
    how: str | None = None
    evidence: str


@router.get("")
async def ways_in(contact_id: str | None = None, person_id: str | None = None, name: str | None = None,
                  user: User = Depends(require_roles(*RMS)), firm: Firm = Depends(current_firm),
                  session: AsyncSession = Depends(get_db)) -> dict:
    try:
        return await introductions.paths(session, firm, contact_id=contact_id, person_id=person_id, name=name)
    except introductions.IntroductionError as exc:
        raise HTTPException(422, str(exc))


@router.get("/prospects")
async def prospects(user: User = Depends(require_roles(*RMS)), firm: Firm = Depends(current_firm),
                    session: AsyncSession = Depends(get_db)) -> dict:
    """An adviser reads the prospects they own, as the pipeline does; others read the firm's."""
    mine = user.role == UserRole.ADVISER
    out = await introductions.open_prospects(session, firm, user if mine else None)
    out["scope"] = "mine" if mine else "firm"
    return out


@router.post("/links")
async def record(body: LinkIn, user: User = Depends(require_roles(*RMS)), firm: Firm = Depends(current_firm),
                 session: AsyncSession = Depends(get_db)) -> dict:
    try:
        return await introductions.record_link(session, firm, user, **body.model_dump())
    except introductions.IntroductionError as exc:
        raise HTTPException(422, str(exc))


# ── The network map, a persona and an RM's own LinkedIn export (B-201 internal part, B-202) ─────────────────────

@router.get("/network")
async def network(contact_id: str | None = None, person_id: str | None = None, name: str | None = None,
                  user: User = Depends(require_roles(*RMS)), firm: Firm = Depends(current_firm),
                  session: AsyncSession = Depends(get_db)) -> dict:
    from app.aurea_core import network_map

    try:
        out = await network_map.build(session, firm, contact_id=contact_id, person_id=person_id, name=name)
    except introductions.IntroductionError as exc:
        raise HTTPException(422, str(exc))
    for n in out["nodes"]:
        n["you"] = n["type"] == "user" and n["id"] == str(user.id)
    return out


@router.get("/persona")
async def persona_of(contact_id: str | None = None, person_id: str | None = None, name: str | None = None,
                     user: User = Depends(require_roles(*RMS)), firm: Firm = Depends(current_firm),
                     session: AsyncSession = Depends(get_db)) -> dict:
    """Never a client's: staff only, and only where compliance switched profiling on with its basis."""
    from app.aurea_core import persona

    try:
        return await persona.read(session, firm, contact_id=contact_id, person_id=person_id, name=name)
    except persona.PersonaError as exc:
        raise HTTPException(422, str(exc))


class ExportIn(BaseModel):
    csv_text: str


@router.post("/linkedin/preview")
async def linkedin_preview(body: ExportIn, user: User = Depends(require_roles(*RMS)), firm: Firm = Depends(current_firm),
                           session: AsyncSession = Depends(get_db)) -> dict:
    """What importing the signed-in person's own export would link, before they confirm on the page."""
    from app.aurea_core import linkedin_import

    try:
        return linkedin_import.summary(await linkedin_import.prepare(session, firm, user, body.csv_text))
    except linkedin_import.LinkedInImportError as exc:
        raise HTTPException(422, str(exc))


@router.post("/linkedin")
async def linkedin_import_(body: ExportIn, user: User = Depends(require_roles(*RMS)), firm: Firm = Depends(current_firm),
                           session: AsyncSession = Depends(get_db)) -> dict:
    """The file stays on the page: an export is not pasted into a conversation, as the CSV import is not."""
    from app.aurea_core import linkedin_import

    try:
        return await linkedin_import.import_connections(session, firm, user, body.csv_text)
    except linkedin_import.LinkedInImportError as exc:
        raise HTTPException(422, str(exc))
