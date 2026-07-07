"""Schema bootstrap. Enables pgvector and creates all tables from SQLAlchemy metadata.

For a greenfield, self-contained deployment this is simpler and more robust than running
Alembic migrations on first boot; Alembic can be layered on for schema evolution later."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.core.db import Base, engine
from app.core.logging import configure_logging, get_logger

# Importing models registers them on Base.metadata.
import app.models  # noqa: F401

log = get_logger("aurea.bootstrap")


_DEFAULT_SEGMENTS = [
    ("private_wealth",  "Private Wealth",  75,  500_000,  "High-net-worth individuals and families"),
    ("mass_affluent",   "Mass Affluent",   100, 100_000,  "Emerging affluent clients"),
    ("for_purpose",     "For Purpose",     50,  None,     "Charities, foundations, iwi organisations"),
    ("institutional",   "Institutional",   25,  5_000_000,"Institutional investors and family offices"),
    ("next_gen",        "Next Gen",        100, None,     "Next-generation heirs and young investors"),
]

_DEFAULT_MANDATE_TYPES = [
    ("discretionary",   "Discretionary",   "tier_2", "Full discretion to act within mandate guardrails"),
    ("advisory",        "Advisory",        "tier_1", "Recommendations require client or adviser approval"),
    ("execution_only",  "Execution Only",  "tier_1", "Client-directed, no advice provided"),
]

# (event_type, channel, enabled, recipients_json, config_json)
_DEFAULT_NOTIF_CONFIGS = [
    ("high_severity_flag",      "email",  True,  '["admin"]', '{"throttle_minutes": 30}'),
    ("high_severity_flag",      "in_app", True,  '[]',        '{}'),
    ("agent_paused",            "email",  True,  '["admin"]', '{}'),
    ("agent_paused",            "in_app", True,  '[]',        '{}'),
    ("recommendation_pending",  "in_app", True,  '[]',        '{}'),
    ("daily_digest",            "email",  False, '["admin"]', '{"digest_time": "08:00"}'),
    ("daily_digest",            "in_app", False, '[]',        '{}'),
]


async def bootstrap() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight, idempotent column additions (so existing DBs upgrade without a reseed).
        await conn.execute(text("ALTER TABLE firm ADD COLUMN IF NOT EXISTS llm_config JSON DEFAULT '{}'"))
        await conn.execute(text("ALTER TABLE skill ADD COLUMN IF NOT EXISTS visibility VARCHAR(16) DEFAULT 'private'"))
        await conn.execute(text("ALTER TABLE skill ADD COLUMN IF NOT EXISTS shared_with JSON DEFAULT '[]'"))
        # Wave F: MFA columns on app_user
        await conn.execute(text("ALTER TABLE app_user ADD COLUMN IF NOT EXISTS mfa_secret VARCHAR(64)"))
        await conn.execute(text("ALTER TABLE app_user ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE"))
        # Ensure server-side default exists even if column was created without one by create_all()
        await conn.execute(text("ALTER TABLE app_user ALTER COLUMN mfa_enabled SET DEFAULT FALSE"))
        # Wave G: Surveillance flag resolution + escalation
        await conn.execute(text("ALTER TABLE surveillance_flag ADD COLUMN IF NOT EXISTS resolution_note TEXT"))
        await conn.execute(text("ALTER TABLE surveillance_flag ADD COLUMN IF NOT EXISTS resolved_by VARCHAR(200)"))
        await conn.execute(text("ALTER TABLE surveillance_flag ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ"))
        await conn.execute(text("ALTER TABLE surveillance_flag ADD COLUMN IF NOT EXISTS escalated BOOLEAN NOT NULL DEFAULT FALSE"))
        await conn.execute(text("ALTER TABLE surveillance_flag ADD COLUMN IF NOT EXISTS escalated_to VARCHAR(200)"))
        await conn.execute(text("ALTER TABLE surveillance_flag ADD COLUMN IF NOT EXISTS escalated_at TIMESTAMPTZ"))
        await conn.execute(text("ALTER TABLE surveillance_flag ADD COLUMN IF NOT EXISTS escalation_note TEXT"))
        # Wave G: Task assignment
        await conn.execute(text("ALTER TABLE task ADD COLUMN IF NOT EXISTS assigned_to UUID REFERENCES app_user(id) ON DELETE SET NULL"))
        await conn.execute(text("ALTER TABLE task ADD COLUMN IF NOT EXISTS assigned_by UUID REFERENCES app_user(id) ON DELETE SET NULL"))
        # Wave G: Research document publishing workflow
        await conn.execute(text("ALTER TABLE research_document ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'published'"))
        await conn.execute(text("ALTER TABLE research_document ADD COLUMN IF NOT EXISTS published_by VARCHAR(200)"))
        await conn.execute(text("ALTER TABLE research_document ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ"))
        await conn.execute(text("ALTER TABLE research_document ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1"))
        # Wave H: Onboarding SLA
        await conn.execute(text("ALTER TABLE onboarding_case ADD COLUMN IF NOT EXISTS sla_days INTEGER NOT NULL DEFAULT 30"))
        # Wave H: Agent scheduler
        await conn.execute(text("ALTER TABLE agent_config ADD COLUMN IF NOT EXISTS schedule_cron VARCHAR(64)"))
        await conn.execute(text("ALTER TABLE agent_config ADD COLUMN IF NOT EXISTS schedule_enabled BOOLEAN NOT NULL DEFAULT FALSE"))
        # Wave I: Holding alerts on SurveillanceFlag
        await conn.execute(text("ALTER TABLE surveillance_flag ADD COLUMN IF NOT EXISTS kind VARCHAR(32)"))
        await conn.execute(text("ALTER TABLE surveillance_flag ADD COLUMN IF NOT EXISTS attributes JSONB NOT NULL DEFAULT '{}'::jsonb"))

        # Seed default segments and mandate type configs for each firm (idempotent).
        firms = (await conn.execute(text("SELECT id FROM firm"))).fetchall()
        for (firm_id,) in firms:
            for slug, label, fee_bps, min_aum, desc in _DEFAULT_SEGMENTS:
                await conn.execute(text(
                    "INSERT INTO firm_segment (id, firm_id, slug, label, fee_tier_bps, min_aum_usd, description, is_active, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :fid, :slug, :label, :fee, :aum, :desc, true, now(), now()) "
                    "ON CONFLICT (firm_id, slug) DO NOTHING"
                ), {"fid": firm_id, "slug": slug, "label": label, "fee": fee_bps, "aum": min_aum, "desc": desc})
            for slug, label, tier, desc in _DEFAULT_MANDATE_TYPES:
                await conn.execute(text(
                    "INSERT INTO mandate_type_config (id, firm_id, slug, label, default_autonomy_tier, description, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :fid, :slug, :label, :tier, :desc, now(), now()) "
                    "ON CONFLICT (firm_id, slug) DO NOTHING"
                ), {"fid": firm_id, "slug": slug, "label": label, "tier": tier, "desc": desc})
            for ev, ch, en, recip, cfg in _DEFAULT_NOTIF_CONFIGS:
                await conn.execute(text(
                    "INSERT INTO notification_config (id, firm_id, event_type, channel, enabled, recipients, config, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :fid, :ev, :ch, :en, CAST(:recip AS json), CAST(:cfg AS json), now(), now()) "
                    "ON CONFLICT (firm_id, event_type, channel) DO NOTHING"
                ), {"fid": firm_id, "ev": ev, "ch": ch, "en": en, "recip": recip, "cfg": cfg})

        # Seed superadmin user (idempotent — password: aurea-super)
        sa_exists = (await conn.execute(
            text("SELECT id FROM app_user WHERE email = 'superadmin@aurea.platform' LIMIT 1")
        )).fetchone()
        if not sa_exists:
            firm_row = (await conn.execute(text("SELECT id FROM firm LIMIT 1"))).fetchone()
            if firm_row:
                from app.core.security import hash_password as _hp
                await conn.execute(text(
                    "INSERT INTO app_user "
                    "(id, firm_id, email, full_name, hashed_password, role, is_active, mfa_enabled, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :fid, 'superadmin@aurea.platform', "
                    "'Platform Superadmin', :hp, 'superadmin', true, false, now(), now())"
                ), {"fid": firm_row[0], "hp": _hp("aurea-super")})

    log.info("schema_bootstrapped", tables=len(Base.metadata.tables))


def main() -> None:
    configure_logging()
    asyncio.run(bootstrap())


if __name__ == "__main__":
    main()
