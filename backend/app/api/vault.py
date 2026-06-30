"""Client Document Vault API — adviser-uploaded documents shared with households."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.core.db import get_db
from app.core.security import get_current_user, staff_user
from app.models.graph import Household
from app.models.identity import User
from app.models.tenant import Firm
from app.models.vault import ClientDocument

router = APIRouter(prefix="/api/vault", tags=["vault"])


class DocCreate(BaseModel):
    household_id: uuid.UUID
    filename: str
    doc_type: str = "general"
    content_text: str | None = None
    tags: list[str] = []
    is_client_visible: bool = True
    size_bytes: int = 0


def _doc_dict(d: ClientDocument) -> dict:
    return {
        "id": str(d.id),
        "household_id": str(d.household_id),
        "uploaded_by": d.uploaded_by,
        "filename": d.filename,
        "doc_type": d.doc_type,
        "tags": d.tags,
        "is_client_visible": d.is_client_visible,
        "size_bytes": d.size_bytes,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


@router.get("/documents")
async def list_documents(
    household_id: uuid.UUID,
    user: User = Depends(get_current_user),
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    q = select(ClientDocument).where(
        ClientDocument.firm_id == firm.id,
        ClientDocument.household_id == household_id,
    )
    # Clients only see visible documents.
    if user.role == "client":
        q = q.where(ClientDocument.is_client_visible.is_(True))
    rows = (await db.execute(q.order_by(ClientDocument.created_at.desc()))).scalars().all()
    return [_doc_dict(r) for r in rows]


@router.post("/documents", dependencies=[Depends(staff_user)])
async def create_document(
    body: DocCreate,
    user: User = Depends(get_current_user),
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    hh = await db.get(Household, body.household_id)
    if not hh or hh.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Household not found")
    doc = ClientDocument(
        firm_id=firm.id,
        household_id=body.household_id,
        uploaded_by=user.email,
        filename=body.filename,
        doc_type=body.doc_type,
        content_text=body.content_text,
        tags=body.tags,
        is_client_visible=body.is_client_visible,
        size_bytes=body.size_bytes,
    )
    db.add(doc)
    await db.flush()
    return _doc_dict(doc)


@router.patch("/documents/{doc_id}", dependencies=[Depends(staff_user)])
async def update_document(
    doc_id: uuid.UUID,
    body: dict,
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    doc = await db.get(ClientDocument, doc_id)
    if not doc or doc.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Not found")
    for field in ("filename", "doc_type", "tags", "is_client_visible"):
        if field in body:
            setattr(doc, field, body[field])
    await db.flush()
    return _doc_dict(doc)


@router.delete("/documents/{doc_id}", dependencies=[Depends(staff_user)])
async def delete_document(
    doc_id: uuid.UUID,
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    doc = await db.get(ClientDocument, doc_id)
    if not doc or doc.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Not found")
    await db.delete(doc)
    return {"ok": True}
