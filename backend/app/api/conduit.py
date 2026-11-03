"""Conduit API — connector registry, configuration, and sync (spec §11)."""
from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
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

REDACTED = "••••••••"


def _secret_keys(provider_key: str) -> set[str]:
    pdef = get_provider_def(provider_key)
    return {f.key for f in (pdef.config_schema if pdef else []) if f.secret}


def _redact(connector: Connector) -> dict:
    secrets = _secret_keys(connector.provider_key)
    cfg = dict(connector.config or {})
    for k in secrets:
        if cfg.get(k):
            cfg[k] = REDACTED
    pdef = get_provider_def(connector.provider_key)
    return {
        "id": str(connector.id), "domain": connector.domain, "provider_key": connector.provider_key,
        "display_name": connector.display_name, "status": connector.status,
        "use_mock": connector.use_mock, "config": cfg, "sync_cron": connector.sync_cron,
        "supports_live": pdef.supports_live if pdef else False,
        "last_synced_at": connector.last_synced_at.isoformat() if connector.last_synced_at else None,
        "last_error": connector.last_error,
    }


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
    rows = (
        await db.execute(select(Connector).where(Connector.firm_id == firm.id))
    ).scalars().all()
    return [_redact(c) for c in rows]


@router.post("/connectors")
async def create_connector(
    body: ConnectorCreate, user: User = SetUp,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    pdef = get_provider_def(body.provider_key)
    if not pdef:
        raise HTTPException(status_code=404, detail="Unknown provider")
    c = Connector(
        firm_id=firm.id, domain=pdef.domain, provider_key=pdef.key,
        display_name=pdef.display_name, status=ConnectorStatus.MOCK, use_mock=True,
        sync_cron=pdef.default_cron, config={},
    )
    db.add(c)
    await db.flush()
    return _redact(c)


@router.patch("/connectors/{connector_id}")
async def update_connector(
    connector_id: uuid.UUID, body: ConnectorUpdate,
    user: User = SetUp,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    c = await db.get(Connector, connector_id)
    if not c or c.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Connector not found")
    if body.config is not None:
        # Merge; ignore redacted placeholders so we never overwrite a secret with '••••'.
        merged = dict(c.config or {})
        for k, v in body.config.items():
            if v == REDACTED:
                continue
            merged[k] = v
        c.config = merged
    if body.use_mock is not None:
        c.use_mock = body.use_mock
        c.status = ConnectorStatus.MOCK if body.use_mock else ConnectorStatus.CONFIGURED
    if body.sync_cron is not None:
        c.sync_cron = body.sync_cron
    if body.display_name is not None:
        c.display_name = body.display_name
    if body.enabled is not None:
        c.status = ConnectorStatus.DISABLED if not body.enabled else (
            ConnectorStatus.MOCK if c.use_mock else ConnectorStatus.CONFIGURED
        )
    # Going live and new credentials are on the record; the values of secrets are not.
    db.add(AuditEvent(firm_id=firm.id, actor_id=user.id, actor_email=user.email, event_type="connector.updated",
                      subject=c.display_name, detail={"config_keys": sorted((body.config or {}).keys()), "use_mock": body.use_mock,
                                                       "enabled": body.enabled, "sync_cron": body.sync_cron}))
    await db.flush()
    return _redact(c)


@router.post("/connectors/{connector_id}/sync")
async def sync_connector(
    connector_id: uuid.UUID, user: User = Syncs,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    c = await db.get(Connector, connector_id)
    if not c or c.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Connector not found")
    sync = await run_sync(db, c)
    return {
        "status": sync.status, "records_ingested": sync.records_ingested, "detail": sync.detail,
        "connector": _redact(c),
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
