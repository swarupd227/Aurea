"""Conduit API — connector registry, configuration, and sync (spec §11)."""
from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import platform_admin
from app.aurea_core.platform_admin import AdminError
from app.conduit.registry import CONNECTOR_REGISTRY, get_provider_def
from app.conduit.service import run_sync
from app.core.db import get_db
from app.core.security import get_current_user, staff_user, require_roles
from app.models.connectors import Connector, ConnectorSync
from app.models.enums import ConnectorStatus, UserRole
from app.models.identity import AuditEvent, User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/conduit", tags=["conduit"], dependencies=[Depends(staff_user)])

# A connector is operations' to set up and switch to a live feed, as the Connectors page is (frontend/lib/nav.ts);
# a sync is also the portfolio team's, as refreshing prices is. Every staff role could do all three before (B-96).
SetUp = Depends(require_roles(UserRole.OPERATIONS))
Syncs = Depends(require_roles(UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM))

REDACTED = platform_admin.REDACTED


def _secret_keys(provider_key: str) -> set[str]:
    return platform_admin.secret_keys(provider_key)


def _redact(connector: Connector) -> dict:
    return platform_admin.connector_out(connector)


def _refused(exc: AdminError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail="Connector not found" if exc.status == 404 else str(exc))


class ConnectorUpdate(BaseModel):
    config: dict | None = None
    use_mock: bool | None = None
    sync_cron: str | None = None
    display_name: str | None = None
    enabled: bool | None = None


class ConnectorCreate(BaseModel):
    provider_key: str


@router.get("/registry")
async def registry():
    """The full connector catalogue with config schemas (drives the Admin connector forms)."""
    out = []
    for p in CONNECTOR_REGISTRY:
        d = asdict(p)
        d["domain"] = p.domain.value
        out.append(d)
    return out


@router.get("/connectors")
async def connectors(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return [_redact(c) for c in await platform_admin.list_connectors(db, firm)]


@router.post("/connectors")
async def create_connector(
    body: ConnectorCreate, user: User = SetUp,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    try:
        done = await platform_admin.add_connector(db, firm, user, body.provider_key)
    except AdminError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    return _redact(done.record)


@router.patch("/connectors/{connector_id}")
async def update_connector(
    connector_id: uuid.UUID, body: ConnectorUpdate,
    user: User = SetUp,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    # Going live and new credentials are on the record; the values of secrets are not (aurea_core/platform_admin).
    try:
        done = await platform_admin.set_connector(db, firm, user, connector_id, **body.model_dump())
    except AdminError as exc:
        raise _refused(exc)
    return _redact(done.record)


@router.post("/connectors/{connector_id}/sync")
async def sync_connector(
    connector_id: uuid.UUID, user: User = Syncs,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    try:
        done, sync = await platform_admin.sync_connector(db, firm, user, connector_id)
    except AdminError as exc:
        raise _refused(exc)
    return {
        "status": sync.status, "records_ingested": sync.records_ingested, "detail": sync.detail,
        "connector": _redact(done.record),
    }


@router.get("/connectors/{connector_id}/syncs")
async def connector_syncs(
    connector_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    rows = (
        await db.execute(
            select(ConnectorSync).where(ConnectorSync.connector_id == connector_id)
            .order_by(ConnectorSync.created_at.desc()).limit(20)
        )
    ).scalars().all()
    return [
        {"status": s.status, "records_ingested": s.records_ingested, "detail": s.detail,
         "finished_at": s.finished_at.isoformat() if s.finished_at else None}
        for s in rows
    ]
