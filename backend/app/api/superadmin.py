"""Superadmin API — platform-level multi-tenant firm management.

Only users with role=SUPERADMIN can access these endpoints. The superadmin is a
platform operator who can create and manage tenant firms. Each new firm is auto-provisioned
with default segments, mandate types, notification configs, connectors, and agent configs."""
from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db, utcnow
from app.core.security import get_current_user, hash_password
from app.models.connectors import Connector
from app.models.enums import AgentKey, AutonomyTier, ConnectorStatus, UserRole
from app.models.identity import AuditEvent, User, UserInviteToken
from app.models.tenant import AgentConfig, Firm, FirmSegment, MandateTypeConfig, NotificationConfig

router = APIRouter(prefix="/api/superadmin", tags=["superadmin"])

# Default provisioning data (mirrors db_bootstrap.py)
_DEFAULT_SEGMENTS = [
    ("private_wealth", "Private Wealth", 75, 500_000, "High-net-worth individuals and families"),
    ("mass_affluent", "Mass Affluent", 100, 100_000, "Emerging affluent clients"),
    ("for_purpose", "For Purpose", 50, None, "Charities, foundations, iwi organisations"),
    ("institutional", "Institutional", 25, 5_000_000, "Institutional investors and family offices"),
    ("next_gen", "Next Gen", 100, None, "Next-generation heirs and young investors"),
]
_DEFAULT_MANDATE_TYPES = [
    ("discretionary", "Discretionary", AutonomyTier.TIER_2, "Full discretion within mandate guardrails"),
    ("advisory", "Advisory", AutonomyTier.TIER_1, "Recommendations require client or adviser approval"),
    ("execution_only", "Execution Only", AutonomyTier.TIER_1, "Client-directed, no advice provided"),
]
_DEFAULT_NOTIF_CONFIGS = [
    ("high_severity_flag", "email", True, ["admin"], {"throttle_minutes": 30}),
    ("high_severity_flag", "in_app", True, [], {}),
    ("agent_paused", "email", True, ["admin"], {}),
    ("agent_paused", "in_app", True, [], {}),
    ("recommendation_pending", "in_app", True, [], {}),
    ("daily_digest", "email", False, ["admin"], {"digest_time": "08:00"}),
    ("daily_digest", "in_app", False, [], {}),
]


async def _superadmin_only(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.SUPERADMIN:
        raise HTTPException(status_code=403, detail="Superadmin access required.")
    return user


@router.get("/firms")
async def list_firms(
    actor: User = Depends(_superadmin_only),
    db: AsyncSession = Depends(get_db),
):
    firms = (await db.execute(select(Firm).order_by(Firm.name))).scalars().all()
    result = []
    for f in firms:
        user_count = len((await db.execute(select(User).where(User.firm_id == f.id))).scalars().all())
        result.append({
            "id": str(f.id), "slug": f.slug, "name": f.name,
            "legal_name": f.legal_name, "jurisdiction": f.jurisdiction,
            "base_currency": f.base_currency, "is_active": f.is_active,
            "user_count": user_count,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        })
    return result


class FirmCreate(BaseModel):
    name: str
    slug: str | None = None
    legal_name: str | None = None
    jurisdiction: str = "NZ"
    regulator: str | None = "FMA"
    base_currency: str = "NZD"
    admin_email: str
    admin_name: str


@router.post("/firms", status_code=201)
async def create_firm(
    body: FirmCreate,
    actor: User = Depends(_superadmin_only),
    db: AsyncSession = Depends(get_db),
):
    """Create a new tenant firm with full auto-provisioning of defaults."""
    slug = (body.slug or body.name.lower().replace(" ", "_").replace("-", "_"))[:64]
    slug = "".join(c if c.isalnum() or c == "_" else "_" for c in slug)

    if (await db.execute(select(Firm).where(Firm.slug == slug))).scalar_one_or_none():
        raise HTTPException(400, f"A firm with slug '{slug}' already exists.")

    admin_email = body.admin_email.lower().strip()
    if (await db.execute(select(User).where(User.email == admin_email))).scalar_one_or_none():
        raise HTTPException(400, f"A user with email '{admin_email}' already exists.")

    # 1. Create firm
    firm = Firm(
        slug=slug, name=body.name, legal_name=body.legal_name,
        jurisdiction=body.jurisdiction, regulator=body.regulator,
        base_currency=body.base_currency,
        branding={}, settings={}, model_config_json={}, llm_config={},
        is_active=True,
    )
    db.add(firm)
    await db.flush()

    # 2. Default client segments
    for seg_slug, label, fee_bps, min_aum, desc in _DEFAULT_SEGMENTS:
        db.add(FirmSegment(
            firm_id=firm.id, slug=seg_slug, label=label,
            fee_tier_bps=fee_bps, min_aum_usd=min_aum, description=desc, is_active=True,
        ))

    # 3. Default mandate types
    for mt_slug, label, tier, desc in _DEFAULT_MANDATE_TYPES:
        db.add(MandateTypeConfig(
            firm_id=firm.id, slug=mt_slug, label=label,
            default_autonomy_tier=tier, description=desc,
        ))

    # 4. Default notification configs
    for ev, ch, en, recip, cfg in _DEFAULT_NOTIF_CONFIGS:
        db.add(NotificationConfig(
            firm_id=firm.id, event_type=ev, channel=ch,
            enabled=en, recipients=recip, config=cfg,
        ))

    # 5. Default connectors (market data + one per domain)
    from app.conduit.registry import default_connectors
    for pdef in default_connectors():
        db.add(Connector(
            firm_id=firm.id, domain=pdef.domain, provider_key=pdef.key,
            display_name=pdef.display_name, status=ConnectorStatus.MOCK,
            use_mock=True, sync_cron=pdef.default_cron, config={},
        ))

    # 6. Default agent configs (all enabled at Tier 1)
    for key in AgentKey:
        db.add(AgentConfig(
            firm_id=firm.id, agent_key=key,
            enabled=True, default_tier=AutonomyTier.TIER_1, config={},
        ))

    # 7. Admin user + invite token
    admin_user = User(
        firm_id=firm.id, email=admin_email, full_name=body.admin_name,
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        role=UserRole.ADMIN, is_active=False,
    )
    db.add(admin_user)
    await db.flush()

    tok = UserInviteToken(
        firm_id=firm.id, user_id=admin_user.id,
        token=secrets.token_urlsafe(48), token_type="invite",
        expires_at=utcnow() + timedelta(days=7),
    )
    db.add(tok)

    invite_url = f"{settings.frontend_url}/accept-invite?token={tok.token}"

    db.add(AuditEvent(
        firm_id=firm.id, actor_id=actor.id, actor_email=actor.email,
        event_type="superadmin.firm_created", subject=slug,
        detail={"admin_email": admin_email, "name": body.name},
    ))

    import structlog
    structlog.get_logger().info("firm_provisioned", slug=slug, admin=admin_email, invite_url=invite_url)

    return {
        "id": str(firm.id), "slug": slug, "name": body.name,
        "admin_email": admin_email,
        "invite_url": invite_url,
        "invite_token": tok.token,
    }


@router.patch("/firms/{firm_id}/status")
async def toggle_firm_status(
    firm_id: str,
    actor: User = Depends(_superadmin_only),
    db: AsyncSession = Depends(get_db),
):
    """Activate or deactivate a tenant firm."""
    from uuid import UUID
    firm = await db.get(Firm, UUID(firm_id))
    if not firm:
        raise HTTPException(404, "Firm not found.")
    firm.is_active = not firm.is_active
    db.add(AuditEvent(
        firm_id=firm.id, actor_id=actor.id, actor_email=actor.email,
        event_type="superadmin.firm_status_changed", subject=firm.slug,
        detail={"is_active": firm.is_active},
    ))
    return {"id": str(firm.id), "slug": firm.slug, "is_active": firm.is_active}
