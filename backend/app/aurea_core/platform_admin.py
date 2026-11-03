"""Who changes the platform, and how: every write on the Admin and Connectors pages, in one place.

The admin routes (api/admin.py, api/portfolios.py, api/conduit.py) and the conversation's tools call the same
functions, so a rule holds whichever surface asks. Each write comes as a pair:

  - ``prepare_*`` reads what is on file, runs every check the write runs, and returns a ``Plan``: the record, and
    each field's from and to. It writes nothing, so a Confirm card can show it and refuse what doing it would refuse.
  - the write itself prepares again, applies the plan, puts it on the audit log (with the reason, when one is given)
    and returns an ``Outcome``: the record and the list of changes.

A refusal is an ``AdminError`` with a plain reason, and the HTTP status its route has always answered with.

The roles are the routes' and the tools' to check (``api/admin.AdminOnly`` and friends); this module decides what
may change, not who. What it never does: hand out an invite or reset token to anything but the admin's own route,
show a connector's configuration values, or touch an LLM key, the firm's jurisdiction or base currency from a tool.
"""
from __future__ import annotations

import re
import secrets
import time as _time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import delete as sql_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import agent_controls, licences
from app.core.config import settings as app_settings
from app.core.db import utcnow
from app.models.enums import AgentKey, AutonomyTier, ConnectorStatus, UserRole
from app.models.graph import Household, LegalEntity, Mandate, Person
from app.models.identity import AuditEvent, User, UserInviteToken
from app.models.knowledge import ResearchChunk, ResearchDocument
from app.models.tenant import AgentConfig, Firm, FirmSegment, MandateTypeConfig, NotificationConfig


class AdminError(Exception):
    """A change the platform refuses, with the reason a person reads and its route's HTTP status."""

    def __init__(self, reason: str, status: int = 422):
        super().__init__(reason)
        self.status = status


@dataclass
class Plan:
    """What a change would do, read now: the record and each field that would move. Nothing is written."""

    section: str
    record: Any
    label: str
    changes: list[dict] = field(default_factory=list)
    notes: list[tuple[str, str]] = field(default_factory=list)   # what follows from it, for the Confirm card
    values: dict = field(default_factory=dict)                    # what the write applies

    @property
    def record_id(self) -> str | None:
        rid = getattr(self.record, "id", None)
        return str(rid) if rid is not None else None


@dataclass
class Outcome:
    """What a change did: the record, as it now stands, and the fields that moved."""

    section: str
    record: Any
    label: str
    changes: list[dict]

    @property
    def record_id(self) -> str | None:
        rid = getattr(self.record, "id", None)
        return str(rid) if rid is not None else None


def _done(plan: Plan, record: Any = None) -> Outcome:
    return Outcome(plan.section, record if record is not None else plan.record, plan.label, plan.changes)


def _diff(changes: list[dict], name: str, before: Any, after: Any) -> None:
    if after is not None and before != after:
        changes.append({"field": name, "from": before, "to": after})


def audit(session: AsyncSession, firm_id: uuid.UUID, actor: User, event_type: str, subject: str | None = None,
          detail: dict | None = None) -> None:
    """One entry on the firm's audit log, made by a person."""
    session.add(AuditEvent(firm_id=firm_id, actor_id=actor.id, actor_email=actor.email, event_type=event_type,
                           subject=subject, detail=detail))


def _with_reason(detail: dict | None, reason: str | None) -> dict | None:
    reason = (reason or "").strip()
    if not reason:
        return detail
    return {**(detail or {}), "reason": reason}


# ── The firm's profile, branding and settings ────────────────────────────────────────────────────

FIRM_FIELDS = ("name", "legal_name", "jurisdiction", "regulator", "base_currency")
# Branding keys as stored, and the names a person (and a tool) uses for them.
BRANDING_NAMES = {"primary": "primary_color", "accent": "accent_color", "tagline": "tagline", "logo_text": "logo_text",
                  "logo_url": "logo_url"}
# Settings that only their own routes change, each with its own checks: the regulatory rules, the foundation
# policy, the capital market assumptions and PII handling.
RESERVED_SETTINGS = {"compliance", "foundation", "cmas", "pii"}
# The tasks a model is chosen for (app/llm/service.TASK_DEFAULTS).
MODEL_TASKS = ("advice", "narrative", "classify")


def model_gate_refusal(firm: Firm) -> str | None:
    """Why a model change is blocked now, or None: when the firm enforces the eval gate, a model or config
    change waits until the golden gates are green."""
    from app.core.foundation import policy as foundation_policy
    from app.provenance import eval_gates

    if foundation_policy(firm).get("enforce_eval_gate") and not eval_gates.run_gates()["all_green"]:
        return "Eval gates are not green — model/config change blocked by policy."
    return None


def prepare_firm(firm: Firm, *, fields: dict | None = None, branding: dict | None = None, settings: dict | None = None,
                 model_config: dict | None = None) -> Plan:
    changes: list[dict] = []
    for name in FIRM_FIELDS:
        _diff(changes, name, getattr(firm, name), (fields or {}).get(name))
    now = firm.branding or {}
    for key, value in (branding or {}).items():
        _diff(changes, BRANDING_NAMES.get(key, f"branding.{key}"), now.get(key), value)
    if settings is not None:
        if reserved := sorted(set(settings) & RESERVED_SETTINGS):
            raise AdminError(f"{', '.join(reserved)} are set through their own controls, not here.")
        for key, value in settings.items():
            _diff(changes, f"settings.{key}", (firm.settings or {}).get(key), value)
    if model_config is not None:
        if refused := model_gate_refusal(firm):
            raise AdminError(refused, 409)
        for task, model in model_config.items():
            _diff(changes, f"model.{task}", (firm.model_config_json or {}).get(task), model)
    return Plan("firm", firm, firm.name, changes,
                values={"fields": fields or {}, "branding": branding, "settings": settings, "model_config": model_config})


async def update_firm(session: AsyncSession, firm: Firm, actor: User, *, fields: dict | None = None,
                      branding: dict | None = None, settings: dict | None = None, model_config: dict | None = None,
                      reason: str | None = None) -> Outcome:
    """The firm's profile, branding, generic settings and model routing (PATCH /api/admin/firm)."""
    plan = prepare_firm(firm, fields=fields, branding=branding, settings=settings, model_config=model_config)
    given = {k: v for k, v in (fields or {}).items() if v is not None}
    for name, value in given.items():
        setattr(firm, name, value)
    if branding is not None:
        firm.branding = {**(firm.branding or {}), **branding}
    if settings is not None:
        firm.settings = {**(firm.settings or {}), **settings}
    if model_config is not None:
        firm.model_config_json = {**(firm.model_config_json or {}), **model_config}
    detail = {**given, **({"branding": branding} if branding is not None else {}),
              **({"model_config_json": model_config} if model_config is not None else {}),
              **({"settings_keys": sorted(settings)} if settings else {})}
    audit(session, firm.id, actor, "firm.updated", subject=firm.name, detail=_with_reason(detail, reason))
    await session.flush()
    return _done(plan)


async def update_branding(session: AsyncSession, firm: Firm, actor: User, branding: dict) -> Outcome:
    """Colours, logo text, tagline and logo (PATCH /api/admin/firm/branding). Only what is given changes."""
    return await update_firm(session, firm, actor, branding={k: v for k, v in branding.items() if v is not None})


def prepare_model_routing(firm: Firm, task: str, model: str) -> Plan:
    from app.llm.service import TASK_DEFAULTS

    task = str(task or "").strip().lower()
    if task not in MODEL_TASKS:
        raise AdminError(f"There is no task called '{task}'. Tasks: {', '.join(MODEL_TASKS)}.")
    model = str(model or "").strip()
    if not model:
        raise AdminError("Name the model.")
    if model.lower() == "default":
        model = TASK_DEFAULTS[task]
    elif not re.fullmatch(r"[A-Za-z0-9._:/@-]{2,100}", model):
        raise AdminError(f"'{model}' is not a model name.")
    if refused := model_gate_refusal(firm):
        raise AdminError(refused, 409)
    before = (firm.model_config_json or {}).get(task) or TASK_DEFAULTS[task]
    changes: list[dict] = []
    _diff(changes, task, before, model)
    return Plan("model_routing", firm, f"Model for {task}", changes, values={"task": task, "model": model})


async def set_model_routing(session: AsyncSession, firm: Firm, actor: User, *, task: str, model: str,
                            reason: str | None = None) -> Outcome:
    """The model one task runs on, held to the eval gate exactly as a change through the firm's settings is."""
    plan = prepare_model_routing(firm, task, model)
    await update_firm(session, firm, actor, model_config={plan.values["task"]: plan.values["model"]}, reason=reason)
    return _done(plan)


def cma_table(firm: Firm) -> dict:
    from app.aurea_core.planning import CMA, effective_cma

    return {"defaults": {k: list(v) for k, v in CMA.items()}, "overrides": (firm.settings or {}).get("cmas") or {},
            "effective": {k: list(v) for k, v in effective_cma(firm.settings).items()}}


def _clean_cmas(cmas: dict) -> dict:
    from app.aurea_core.planning import CMA

    clean = {}
    for cls, val in (cmas or {}).items():
        if cls in CMA and isinstance(val, (list, tuple)) and len(val) == 2:
            clean[cls] = [float(val[0]), float(val[1])]
    return clean


async def set_cmas(session: AsyncSession, firm: Firm, actor: User, cmas: dict, reason: str | None = None) -> dict:
    """Replace the firm's capital market assumption overrides (PUT /api/admin/foundation/cmas): only the planning
    asset classes, each [expected_return, volatility]."""
    clean = _clean_cmas(cmas)
    firm.settings = {**(firm.settings or {}), "cmas": clean}
    audit(session, firm.id, actor, "cmas.updated", subject=firm.name, detail=_with_reason({"classes": sorted(clean)}, reason))
    await session.flush()
    return clean


def prepare_cma(firm: Firm, *, asset_class: str, expected_return: float | None = None,
                volatility: float | None = None) -> Plan:
    from app.aurea_core.planning import CMA, effective_cma

    cls = str(asset_class or "").strip().lower()
    if cls not in CMA:
        raise AdminError(f"There is no planning asset class called '{cls}'. Classes: {', '.join(CMA)}.")
    if expected_return is None and volatility is None:
        raise AdminError("Give an expected return, a volatility or both.")
    now = list(effective_cma(firm.settings)[cls])
    after = [expected_return if expected_return is not None else now[0], volatility if volatility is not None else now[1]]
    if not -0.5 <= after[0] <= 0.5:
        raise AdminError("An expected return is a yearly fraction between -0.5 and 0.5 (0.07 for 7%).")
    if not 0 <= after[1] <= 1:
        raise AdminError("A volatility is a yearly fraction between 0 and 1 (0.16 for 16%).")
    changes: list[dict] = []
    _diff(changes, "expected_return", now[0], expected_return)
    _diff(changes, "volatility", now[1], volatility)
    overrides = {**((firm.settings or {}).get("cmas") or {}), cls: after}
    return Plan("cmas", firm, f"Capital market assumptions · {cls}", changes, values={"cmas": overrides})


async def set_cma(session: AsyncSession, firm: Firm, actor: User, *, asset_class: str, expected_return: float | None = None,
                  volatility: float | None = None, reason: str | None = None) -> Outcome:
    plan = prepare_cma(firm, asset_class=asset_class, expected_return=expected_return, volatility=volatility)
    await set_cmas(session, firm, actor, plan.values["cmas"], reason=reason)
    return _done(plan)


# ── An agent's switches ──────────────────────────────────────────────────────────────────────────

MARKET_DATA_KEY = "market_data"
SCHEDULE_PICKED_UP = "within 15 minutes"
PAUSED_BY_SURVEILLANCE = ("Conduct surveillance paused this agent and its flag is still open. Compliance or a branch "
                          "leader resolves the flag first; only then can the agent be resumed.")


def _agent_key(agent_key: Any) -> AgentKey:
    if str(agent_key or "").strip() == MARKET_DATA_KEY:
        raise AdminError("Prices are fetched at fixed times just after the market closes; switch the feed on or off "
                         "under Connectors.", 409)
    try:
        return AgentKey(str(agent_key or "").strip())
    except ValueError:
        raise AdminError(f"There is no agent called '{agent_key}'.")


def agent_name(agent_key: str) -> str:
    from app.agents.catalogue import CATALOGUE

    return (CATALOGUE.get(str(agent_key)) or {}).get("name") or str(agent_key).replace("_", " ").title()


async def prepare_agent_switches(session: AsyncSession, firm: Firm, agent_key: Any, *, enabled: bool | None = None,
                                 default_tier: Any = None, paused: bool | None = None, schedule_cron: str | None = None,
                                 schedule_enabled: bool | None = None) -> Plan:
    """Every check agent_controls.change makes, in its order, without writing: a tier within the licence, a valid
    cron, and no resuming an agent conduct surveillance paused while its flag is open."""
    from app.core import decision_rights

    key = _agent_key(agent_key)
    tier = None
    if default_tier is not None:
        try:
            tier = AutonomyTier(str(default_tier).strip().lower())
        except ValueError:
            raise AdminError(f"'{default_tier}' is not a tier. Tiers: tier_1, tier_2, tier_3.")
        if refused := licences.refusal(firm, tier):
            raise AdminError(refused)
    cron = None
    if schedule_cron is not None:
        try:
            cron = agent_controls.valid_cron(schedule_cron)
        except agent_controls.ControlError as exc:
            raise AdminError(str(exc))
    cfg = (await session.execute(
        select(AgentConfig).where(AgentConfig.firm_id == firm.id, AgentConfig.agent_key == key))).scalar_one_or_none()
    if paused is False and cfg is not None and cfg.paused and await agent_controls.open_pausing_flags(session, firm.id, key):
        raise AdminError(PAUSED_BY_SURVEILLANCE)

    now = {"enabled": cfg.enabled if cfg else True, "tier": str(cfg.default_tier) if cfg and cfg.default_tier else "tier_1",
           "paused": bool(cfg.paused) if cfg else False, "schedule_cron": cfg.schedule_cron if cfg else None,
           "schedule_enabled": bool(cfg.schedule_enabled) if cfg else False}
    changes: list[dict] = []
    _diff(changes, "enabled", now["enabled"], enabled)
    _diff(changes, "tier", now["tier"], str(tier) if tier else None)
    _diff(changes, "paused", now["paused"], paused)
    _diff(changes, "schedule_cron", now["schedule_cron"], cron)
    _diff(changes, "schedule_enabled", now["schedule_enabled"], schedule_enabled)
    notes: list[tuple[str, str]] = []
    if cron is not None or schedule_enabled is not None:
        notes.append(("Takes effect", f"Schedule picked up {SCHEDULE_PICKED_UP}"))
    if tier == AutonomyTier.TIER_3 and (decision_rights.moves_the_book(key) or decision_rights.moves_client_cash(key)):
        notes.append(("Runs a person starts", "Still stop at a proposal"))
    if tier is not None and (cap := licences.ceiling(firm)) is not None:
        notes.append(("Licence ceiling", str(cap.value)))
    return Plan("agents", cfg, agent_name(key), changes, notes,
                values={"agent_key": key, "default_tier": tier, "schedule_cron": cron})


async def set_agent_switches(session: AsyncSession, firm: Firm, actor: User, agent_key: Any, *, enabled: bool | None = None,
                             default_tier: Any = None, paused: bool | None = None, paused_reason: str | None = None,
                             schedule_cron: str | None = None, schedule_enabled: bool | None = None,
                             config: dict | None = None, reason: str | None = None) -> Outcome:
    """Whether an agent runs, its tier, pause and schedule (PATCH /api/admin/agents/{key}, /schedules/{key}),
    through aurea_core/agent_controls.change: an AutonomyChange made by a person, on the audit log."""
    plan = await prepare_agent_switches(session, firm, agent_key, enabled=enabled, default_tier=default_tier, paused=paused,
                                        schedule_cron=schedule_cron, schedule_enabled=schedule_enabled)
    try:
        cfg = await agent_controls.change(session, firm, plan.values["agent_key"], actor_id=actor.id, actor_email=actor.email,
                                          enabled=enabled, default_tier=plan.values["default_tier"], paused=paused,
                                          paused_reason=paused_reason, schedule_cron=schedule_cron,
                                          schedule_enabled=schedule_enabled, config=config, reason=reason)
    except agent_controls.ControlError as exc:
        raise AdminError(str(exc))
    return _done(plan, cfg)


async def list_agent_configs(session: AsyncSession, firm: Firm) -> list[dict]:
    rows = (await session.execute(select(AgentConfig).where(AgentConfig.firm_id == firm.id))).scalars().all()
    return [{"agent_key": c.agent_key, "enabled": c.enabled, "default_tier": c.default_tier, "paused": c.paused,
             "paused_reason": c.paused_reason, "schedule_cron": c.schedule_cron, "schedule_enabled": c.schedule_enabled,
             "config": c.config} for c in rows]


async def list_schedules(session: AsyncSession, firm: Firm) -> list[dict]:
    """Every job the worker runs on a timer for this firm: the agents' cadences and the price fetch."""
    from app.agents import schedules as agent_schedules
    from app.aurea_core import price_freshness
    from app.models.connectors import ConnectorSync
    from app.models.governance import AgentRun

    configs = {str(c.agent_key): c for c in (await session.execute(
        select(AgentConfig).where(AgentConfig.firm_id == firm.id))).scalars().all()}
    results = []
    for key, cfg in configs.items():
        if not cfg.schedule_cron and agent_schedules.default_for(key) is None:
            continue
        default = agent_schedules.default_for(key)
        run_count = (await session.execute(
            select(func.count()).select_from(AgentRun).where(AgentRun.firm_id == firm.id, AgentRun.agent_key == key)
        )).scalar() or 0
        last = (await session.execute(
            select(AgentRun).where(AgentRun.firm_id == firm.id, AgentRun.agent_key == key)
            .order_by(AgentRun.started_at.desc()).limit(1)
        )).scalar_one_or_none()
        results.append({
            "agent_key": key, "label": agent_name(key),
            "cron": cfg.schedule_cron, "default_cron": default[0] if default else None,
            "why": default[1] if default else None, "fixed": False,
            "last_run_at": last.started_at.isoformat() if last and last.started_at else None,
            "last_run_status": last.status if last else None, "run_count": run_count,
            "enabled": bool(cfg.enabled and cfg.schedule_enabled), "paused": bool(cfg.paused),
        })
    results.sort(key=lambda r: r["label"])

    conn = await price_freshness.market_connector(session, firm.id)
    last_sync = None
    n_sync = 0
    if conn is not None:
        last_sync = (await session.execute(
            select(ConnectorSync).where(ConnectorSync.connector_id == conn.id)
            .order_by(ConnectorSync.started_at.desc()).limit(1))).scalars().first()
        n_sync = (await session.execute(
            select(func.count()).select_from(ConnectorSync).where(ConnectorSync.connector_id == conn.id))).scalar() or 0
    times = " and ".join(f"{h:02d}:{m:02d}" for h, m, _ in price_freshness.SCHEDULE)
    results.insert(0, {
        "agent_key": MARKET_DATA_KEY, "label": "Market prices", "cron": f"Mon-Fri {times} UTC",
        "default_cron": None, "why": "Just after the NZX and US closes. Fixed: the worker builds these times from "
                                     "one definition, so they are changed in code, not here.",
        "fixed": True,
        "last_run_at": last_sync.started_at.isoformat() if last_sync and last_sync.started_at else None,
        "last_run_status": last_sync.status if last_sync else None, "run_count": n_sync,
        "enabled": bool(conn and not conn.use_mock), "paused": False,
    })
    return results


# ── Users ─────────────────────────────────────────────────────────────────────────────────────────

def user_out(u: User) -> dict:
    return {"id": str(u.id), "email": u.email, "full_name": u.full_name, "role": u.role, "title": u.title,
            "is_active": u.is_active, "created_at": u.created_at.isoformat() if u.created_at else None}


async def list_users(session: AsyncSession, firm: Firm) -> list[dict]:
    rows = (await session.execute(select(User).where(User.firm_id == firm.id).order_by(User.full_name))).scalars().all()
    return [user_out(u) for u in rows]


def _role(role: Any) -> UserRole | None:
    if role is None or str(role).strip() == "":
        return None
    try:
        parsed = UserRole(str(role).strip().lower())
    except ValueError:
        raise AdminError(f"'{role}' is not a role. Roles: {', '.join(r.value for r in UserRole if r != UserRole.SUPERADMIN)}.")
    if parsed == UserRole.SUPERADMIN:
        raise AdminError("The platform owner's role is not granted from inside a firm.", 403)
    return parsed


def make_token(session: AsyncSession, user: User, firm_id: uuid.UUID, token_type: str, days: int) -> UserInviteToken:
    """An invite or reset token. It reaches the admin's own route and never a conversation tool."""
    tok = UserInviteToken(firm_id=firm_id, user_id=user.id, token=secrets.token_urlsafe(32), token_type=token_type,
                          expires_at=utcnow() + timedelta(days=days))
    session.add(tok)
    return tok


async def prepare_invite(session: AsyncSession, firm: Firm, *, email: str, full_name: str, role: Any = UserRole.ADVISER,
                         title: str | None = None) -> Plan:
    parsed = _role(role) or UserRole.ADVISER
    email = str(email or "").lower().strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise AdminError(f"'{email}' is not an email address.")
    full_name = str(full_name or "").strip()
    if not full_name:
        raise AdminError("Give the person's full name.")
    if (await session.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise AdminError("A user with that email already exists.", 400)
    changes = [{"field": "email", "from": None, "to": email}, {"field": "full_name", "from": None, "to": full_name},
               {"field": "role", "from": None, "to": parsed.value}]
    _diff(changes, "title", None, (title or "").strip() or None)
    return Plan("users", None, full_name, changes, [("Signs in", "Once they accept the invite"), ("Invite link", "On the Users page")],
                values={"email": email, "full_name": full_name, "role": parsed, "title": (title or "").strip() or None})


async def invite_user(session: AsyncSession, firm: Firm, actor: User, *, email: str, full_name: str,
                      role: Any = UserRole.ADVISER, title: str | None = None) -> tuple[Outcome, UserInviteToken]:
    """A new person, inactive until they accept the invite (POST /api/admin/users). The token is returned for the
    admin's route alone; a tool drops it."""
    import structlog

    plan = await prepare_invite(session, firm, email=email, full_name=full_name, role=role, title=title)
    v = plan.values
    new_user = User(firm_id=firm.id, email=v["email"], full_name=v["full_name"],
                    hashed_password=_hash(secrets.token_urlsafe(32)), role=v["role"], title=v["title"], is_active=False)
    session.add(new_user)
    await session.flush()
    tok = make_token(session, new_user, firm.id, "invite", days=7)
    await session.flush()
    audit(session, firm.id, actor, "user.created", subject=new_user.email, detail={"role": v["role"]})
    structlog.get_logger().info("mock_email_invite", to=new_user.email, url=f"{app_settings.frontend_url}/accept-invite?token={tok.token}")
    return _done(plan, new_user), tok


def _hash(password: str) -> str:
    from app.core.security import hash_password

    return hash_password(password)


async def _firm_user(session: AsyncSession, firm: Firm, user_id: Any) -> User:
    try:
        uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        raise AdminError("User not found.", 404)
    target = await session.get(User, uid)
    if not target or target.firm_id != firm.id:
        raise AdminError("User not found.", 404)
    return target


async def prepare_user_change(session: AsyncSession, firm: Firm, actor: User, user_id: Any, *, full_name: str | None = None,
                              role: Any = None, title: str | None = None, is_active: bool | None = None) -> Plan:
    target = await _firm_user(session, firm, user_id)
    parsed = _role(role)
    if target.id == actor.id and (parsed not in (None, target.role) or is_active is False):
        raise AdminError("You cannot change your own role or deactivate your own account.", 400)
    changes: list[dict] = []
    _diff(changes, "full_name", target.full_name, full_name)
    _diff(changes, "role", str(target.role), parsed.value if parsed else None)
    _diff(changes, "title", target.title, title)
    _diff(changes, "active", bool(target.is_active), is_active)
    notes = [("Sessions", "Ended at once")] if is_active is False and target.is_active else []
    return Plan("users", target, f"{target.full_name} · {target.email}", changes, notes, values={"role": parsed})


async def change_user(session: AsyncSession, firm: Firm, actor: User, user_id: Any, *, full_name: str | None = None,
                      role: Any = None, title: str | None = None, is_active: bool | None = None,
                      reason: str | None = None) -> Outcome:
    """A person's name, role, title or whether they may sign in (PATCH /api/admin/users/{id}). Nobody grants the
    platform owner's role or changes their own; deactivating someone ends their sessions."""
    plan = await prepare_user_change(session, firm, actor, user_id, full_name=full_name, role=role, title=title,
                                     is_active=is_active)
    target, parsed = plan.record, plan.values["role"]
    if full_name is not None:
        target.full_name = full_name
    if parsed is not None and parsed != target.role:
        audit(session, firm.id, actor, "user.role_changed", subject=target.email,
              detail=_with_reason({"from": str(target.role), "to": str(parsed)}, reason))
        target.role = parsed
    if title is not None:
        target.title = title
    if is_active is not None and is_active != target.is_active:
        target.is_active = is_active
        if not is_active:
            await end_sessions(target.id)
        audit(session, firm.id, actor, "user.deactivated" if not is_active else "user.activated", subject=target.email,
              detail=_with_reason(None, reason))
    return _done(plan)


async def end_sessions(user_id: uuid.UUID) -> None:
    """Every token issued to this user before now stops working (get_current_user reads the mark). A deactivated
    user is refused on every request anyway, so with no Redis the mark is skipped rather than the change failing."""
    from app.core.security import _get_redis

    try:
        r = await _get_redis()
        await r.setex(f"fl:{str(user_id)}", app_settings.access_token_ttl_minutes * 60, str(int(_time.time())))
    except Exception:
        pass


# ── Segments and mandate types ───────────────────────────────────────────────────────────────────

def segment_out(s: FirmSegment) -> dict:
    return {"id": str(s.id), "slug": s.slug, "label": s.label, "fee_tier_bps": s.fee_tier_bps, "min_aum_usd": s.min_aum_usd,
            "description": s.description, "is_active": s.is_active}


async def list_segments(session: AsyncSession, firm: Firm) -> list[dict]:
    rows = (await session.execute(
        select(FirmSegment).where(FirmSegment.firm_id == firm.id).order_by(FirmSegment.label))).scalars().all()
    return [segment_out(s) for s in rows]


async def households_on_segment(session: AsyncSession, firm_id: uuid.UUID, slug: str) -> int:
    return (await session.execute(select(func.count(Household.id)).where(
        Household.firm_id == firm_id, Household.segment == slug))).scalar_one()


async def _owned(session: AsyncSession, model, firm: Firm, rid: Any, what: str):
    try:
        rid = rid if isinstance(rid, uuid.UUID) else uuid.UUID(str(rid))
    except (ValueError, TypeError):
        raise AdminError(f"{what} not found.", 404)
    row = await session.get(model, rid)
    if not row or row.firm_id != firm.id:
        raise AdminError(f"{what} not found.", 404)
    return row


SEGMENT_FIELDS = ("label", "fee_tier_bps", "min_aum_usd", "description", "is_active")


async def prepare_segment(session: AsyncSession, firm: Firm, *, segment_id: Any = None, slug: str | None = None,
                          label: str | None = None, fee_tier_bps: int | None = None, min_aum_usd: float | None = None,
                          description: str | None = None, is_active: bool | None = None) -> Plan:
    given = {"label": label, "fee_tier_bps": fee_tier_bps, "min_aum_usd": min_aum_usd, "description": description,
             "is_active": is_active}
    changes: list[dict] = []
    if segment_id:
        seg = await _owned(session, FirmSegment, firm, segment_id, "Segment")
        if slug is not None and slug != seg.slug:
            raise AdminError("A segment's slug does not change: households are filed under it. Add a new segment instead.")
        for name, value in given.items():
            _diff(changes, name, getattr(seg, name), value)
        record, the_slug, rate = seg, seg.slug, seg.fee_tier_bps
    else:
        slug, label = (slug or "").strip(), (label or "").strip()
        if not slug or not label:
            raise AdminError("A new segment needs its slug and its label.")
        if (await session.execute(select(FirmSegment).where(FirmSegment.firm_id == firm.id, FirmSegment.slug == slug))).scalar_one_or_none():
            raise AdminError("A segment with that slug already exists.", 400)
        changes.append({"field": "slug", "from": None, "to": slug})
        for name, value in {**given, "label": label}.items():
            _diff(changes, name, None, value)
        record, the_slug, rate = None, slug, None
    notes: list[tuple[str, str]] = []
    if fee_tier_bps is not None and fee_tier_bps != rate:
        if fee_tier_bps < 0 or fee_tier_bps > 1000:
            raise AdminError("A fee rate is in basis points a year, between 0 and 1,000.")
        notes.append(("Households on this segment", str(await households_on_segment(session, firm.id, the_slug))))
    return Plan("segments", record, label or (record.label if record else the_slug), changes, notes,
                values={"slug": the_slug, **{k: v for k, v in given.items() if v is not None}, "label": label or None})


async def save_segment(session: AsyncSession, firm: Firm, actor: User, *, segment_id: Any = None, slug: str | None = None,
                       label: str | None = None, fee_tier_bps: int | None = None, min_aum_usd: float | None = None,
                       description: str | None = None, is_active: bool | None = None, reason: str | None = None) -> Outcome:
    """Add a client segment (POST /api/admin/segments) or change one (PATCH /api/admin/segments/{id})."""
    plan = await prepare_segment(session, firm, segment_id=segment_id, slug=slug, label=label, fee_tier_bps=fee_tier_bps,
                                 min_aum_usd=min_aum_usd, description=description, is_active=is_active)
    detail = _with_reason({"changed": [c["field"] for c in plan.changes]} if reason else None, reason)
    if plan.record is None:
        seg = FirmSegment(firm_id=firm.id, slug=plan.values["slug"], label=plan.values["label"], fee_tier_bps=fee_tier_bps,
                          min_aum_usd=min_aum_usd, description=description,
                          **({"is_active": is_active} if is_active is not None else {}))
        session.add(seg)
        await session.flush()
        audit(session, firm.id, actor, "segment.created", subject=seg.label, detail=detail)
        return _done(plan, seg)
    seg = plan.record
    for name in SEGMENT_FIELDS:
        value = {"label": label, "fee_tier_bps": fee_tier_bps, "min_aum_usd": min_aum_usd, "description": description,
                 "is_active": is_active}[name]
        if value is not None:
            setattr(seg, name, value)
    audit(session, firm.id, actor, "segment.updated", subject=seg.label, detail=detail)
    return _done(plan)


async def deactivate_segment(session: AsyncSession, firm: Firm, actor: User, segment_id: Any) -> FirmSegment:
    seg = await _owned(session, FirmSegment, firm, segment_id, "Segment")
    seg.is_active = False
    audit(session, firm.id, actor, "segment.deactivated", subject=seg.label)
    return seg


def mandate_type_out(m: MandateTypeConfig) -> dict:
    return {"id": str(m.id), "slug": m.slug, "label": m.label, "default_autonomy_tier": m.default_autonomy_tier,
            "description": m.description}


async def list_mandate_types(session: AsyncSession, firm: Firm) -> list[dict]:
    rows = (await session.execute(select(MandateTypeConfig).where(MandateTypeConfig.firm_id == firm.id)
                                  .order_by(MandateTypeConfig.label))).scalars().all()
    return [mandate_type_out(m) for m in rows]


async def prepare_mandate_type(session: AsyncSession, firm: Firm, *, mandate_type_id: Any = None, slug: str | None = None,
                               label: str | None = None, default_autonomy_tier: Any = None,
                               description: str | None = None) -> Plan:
    tier = None
    if default_autonomy_tier is not None:
        try:
            tier = AutonomyTier(str(default_autonomy_tier).strip().lower())
        except ValueError:
            raise AdminError(f"'{default_autonomy_tier}' is not a tier. Tiers: tier_1, tier_2, tier_3.")
    changes: list[dict] = []
    if mandate_type_id:
        mtc = await _owned(session, MandateTypeConfig, firm, mandate_type_id, "Mandate type")
        if slug is not None and slug != mtc.slug:
            raise AdminError("A mandate type's slug does not change: mandates are filed under it. Add a new type instead.")
        record, the_slug = mtc, mtc.slug
        _diff(changes, "label", mtc.label, label)
        _diff(changes, "default_autonomy_tier", str(mtc.default_autonomy_tier), str(tier) if tier else None)
        _diff(changes, "description", mtc.description, description)
    else:
        slug, label = (slug or "").strip(), (label or "").strip()
        if not slug or not label:
            raise AdminError("A new mandate type needs its slug and its label.")
        if (await session.execute(select(MandateTypeConfig).where(
                MandateTypeConfig.firm_id == firm.id, MandateTypeConfig.slug == slug))).scalar_one_or_none():
            raise AdminError("A mandate type with that slug already exists.", 400)
        tier = tier or AutonomyTier.TIER_2
        record, the_slug = None, slug
        changes += [{"field": "slug", "from": None, "to": slug}, {"field": "label", "from": None, "to": label},
                    {"field": "default_autonomy_tier", "from": None, "to": str(tier)}]
        _diff(changes, "description", None, description)
    if tier is not None and (refused := licences.refusal(firm, tier)):
        raise AdminError(refused)
    return Plan("mandate_types", record, label or (record.label if record else the_slug), changes,
                values={"slug": the_slug, "label": label, "tier": tier, "description": description})


async def save_mandate_type(session: AsyncSession, firm: Firm, actor: User, *, mandate_type_id: Any = None,
                            slug: str | None = None, label: str | None = None, default_autonomy_tier: Any = None,
                            description: str | None = None, reason: str | None = None) -> Outcome:
    """Add a mandate type (POST /api/admin/mandate-types) or change one (PATCH ...); its tier is held to the licence."""
    plan = await prepare_mandate_type(session, firm, mandate_type_id=mandate_type_id, slug=slug, label=label,
                                      default_autonomy_tier=default_autonomy_tier, description=description)
    v = plan.values
    if plan.record is None:
        mtc = MandateTypeConfig(firm_id=firm.id, slug=v["slug"], label=v["label"], default_autonomy_tier=v["tier"],
                                description=description)
        session.add(mtc)
        await session.flush()
        audit(session, firm.id, actor, "mandate_type.created", subject=mtc.label, detail=_with_reason(None, reason))
        return _done(plan, mtc)
    mtc = plan.record
    given = {k: val for k, val in (("label", label), ("default_autonomy_tier", v["tier"]), ("description", description)) if val is not None}
    for name, value in given.items():
        setattr(mtc, name, value)
    audit(session, firm.id, actor, "mandate_type.updated", subject=mtc.label, detail=_with_reason(given, reason))
    return _done(plan)


# ── Mandates ──────────────────────────────────────────────────────────────────────────────────────

def mandate_out(m: Mandate, client_name: str | None = None) -> dict:
    return {"id": str(m.id), "name": m.name, "mandate_type": m.mandate_type, "suitability": m.suitability or {},
            "constraints": m.constraints or {}, "is_active": m.is_active, "client_name": client_name,
            "created_at": m.created_at.isoformat() if m.created_at else None}


async def list_mandates(session: AsyncSession, firm: Firm) -> list[dict]:
    mandates = (await session.execute(select(Mandate).where(Mandate.firm_id == firm.id).order_by(Mandate.name))).scalars().all()
    result = []
    for m in mandates:
        client_name = None
        if m.person_id:
            p = await session.get(Person, m.person_id)
            if p:
                client_name = p.full_name
        result.append(mandate_out(m, client_name))
    return result


async def household_of_mandate(session: AsyncSession, mandate: Mandate) -> Household | None:
    owner = (await session.get(Person, mandate.person_id) if mandate.person_id
             else await session.get(LegalEntity, mandate.entity_id) if mandate.entity_id else None)
    return await session.get(Household, owner.household_id) if owner and owner.household_id else None


SUITABILITY_FIELDS = ("risk_tolerance", "investment_horizon", "max_equity", "esg_preference")


async def prepare_mandate(session: AsyncSession, firm: Firm, mandate_id: Any, *, mandate_type: str | None = None,
                          risk_tolerance: str | None = None, investment_horizon: str | None = None,
                          max_equity: float | None = None, esg_preference: str | None = None,
                          is_active: bool | None = None) -> Plan:
    from app.aurea_core.risk_profile import CAPACITY_EQUITY_MAX, on_file

    m = await _owned(session, Mandate, firm, mandate_id, "Mandate")
    suit = m.suitability or {}
    given = {"risk_tolerance": risk_tolerance, "investment_horizon": investment_horizon, "max_equity": max_equity,
             "esg_preference": esg_preference}
    changes: list[dict] = []
    _diff(changes, "mandate_type", str(m.mandate_type), mandate_type)
    for name, value in given.items():
        _diff(changes, name, suit.get(name), value)
    _diff(changes, "active", bool(m.is_active), is_active)
    notes: list[tuple[str, str]] = []
    if max_equity is not None and max_equity != suit.get("max_equity"):
        if not 0 <= max_equity <= 1:
            raise AdminError("A maximum equity is a fraction between 0 and 1 (0.6 for 60%).")
        # Neither an IPS nor a risk profile may put a mandate's equity over its capacity ceiling, and neither may this:
        # a mandate over its ceiling breaches on the next rebalance (aurea_core/risk_profile.CAPACITY_EQUITY_MAX).
        _, capacity = on_file(suit)
        ceiling = CAPACITY_EQUITY_MAX.get(capacity or "")
        if ceiling is not None and max_equity > ceiling:
            raise AdminError(f"A maximum equity of {max_equity:.0%} is above the {ceiling:.0%} ceiling for {capacity} capacity.")
        if ceiling is not None:
            notes.append(("Capacity ceiling", f"{ceiling:.0%}"))
    household = await household_of_mandate(session, m)
    label = f"{household.name} · {m.name}" if household else m.name
    return Plan("mandates", m, label, changes, notes, values={"household": household})


async def update_mandate(session: AsyncSession, firm: Firm, actor: User, mandate_id: Any, *, mandate_type: str | None = None,
                         risk_tolerance: str | None = None, investment_horizon: str | None = None,
                         max_equity: float | None = None, esg_preference: str | None = None, is_active: bool | None = None,
                         reason: str | None = None) -> Outcome:
    """A mandate's type, suitability and whether it is active (PATCH /api/admin/mandates/{id})."""
    plan = await prepare_mandate(session, firm, mandate_id, mandate_type=mandate_type, risk_tolerance=risk_tolerance,
                                 investment_horizon=investment_horizon, max_equity=max_equity, esg_preference=esg_preference,
                                 is_active=is_active)
    m = plan.record
    if mandate_type is not None:
        m.mandate_type = mandate_type
    if is_active is not None:
        m.is_active = is_active
    given = {k: v for k, v in (("risk_tolerance", risk_tolerance), ("investment_horizon", investment_horizon),
                                ("max_equity", max_equity), ("esg_preference", esg_preference)) if v is not None}
    if given:
        m.suitability = {**(m.suitability or {}), **given}
    detail = {"mandate_type": m.mandate_type}
    if reason:
        detail["changed"] = {c["field"]: [c["from"], c["to"]] for c in plan.changes}
    audit(session, firm.id, actor, "mandate.updated", subject=m.name, detail=_with_reason(detail, reason))
    return _done(plan)


# ── Notifications ─────────────────────────────────────────────────────────────────────────────────

# The events the Notifications tab offers (frontend/app/(app)/admin/page.tsx NOTIF_EVENTS) and its channels.
NOTIFICATION_EVENTS = ("high_severity_flag", "agent_paused", "recommendation_pending", "daily_digest")
NOTIFICATION_CHANNELS = ("email", "in_app")
# A recipient is one of this firm's active people by email, or one of its staff roles, as the defaults a new firm
# is given name "admin" (api/superadmin._DEFAULT_NOTIF_CONFIGS). Either way an alert stays inside the firm.
RECIPIENT_ROLES = {r.value for r in UserRole if r not in (UserRole.CLIENT, UserRole.SUPERADMIN)}


def notification_out(n: NotificationConfig) -> dict:
    return {"id": str(n.id), "event_type": n.event_type, "channel": n.channel, "enabled": n.enabled,
            "recipients": n.recipients or [], "config": n.config or {}}


async def list_notifications(session: AsyncSession, firm: Firm) -> list[dict]:
    rows = (await session.execute(select(NotificationConfig).where(NotificationConfig.firm_id == firm.id)
                                  .order_by(NotificationConfig.event_type, NotificationConfig.channel))).scalars().all()
    return [notification_out(n) for n in rows]


async def outside_recipients(session: AsyncSession, firm: Firm, recipients: list[str]) -> list[str]:
    """The recipients that are not this firm's own people: neither an active user's email nor a staff role."""
    ours = {e.lower() for e in (await session.execute(
        select(User.email).where(User.firm_id == firm.id, User.is_active.is_(True)))).scalars().all()}
    return [r for r in recipients if r.strip().lower() not in ours and r.strip().lower() not in RECIPIENT_ROLES]


def _recipients(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else str(value or "").split(",")
    return list(dict.fromkeys(str(r).strip().lower() for r in raw if str(r).strip()))


async def _refuse_outsiders(session: AsyncSession, firm: Firm, recipients: list[str]) -> None:
    if outside := await outside_recipients(session, firm, recipients):
        raise AdminError(f"Alerts go only to this firm's people: {', '.join(outside)} is not one of them.")


async def update_notifications(session: AsyncSession, firm: Firm, actor: User, items: list[dict]) -> None:
    """Bulk upsert of the notification rows (PATCH /api/admin/notifications). Every recipient is the firm's own."""
    for item in items:
        await _refuse_outsiders(session, firm, _recipients(item.get("recipients") or []))
    for item in items:
        existing = (await session.execute(select(NotificationConfig).where(
            NotificationConfig.firm_id == firm.id, NotificationConfig.event_type == item["event_type"],
            NotificationConfig.channel == item["channel"]))).scalar_one_or_none()
        recipients = _recipients(item.get("recipients") or [])
        if existing:
            existing.enabled = item["enabled"]
            existing.recipients = recipients
            existing.config = item.get("config") or {}
        else:
            session.add(NotificationConfig(firm_id=firm.id, event_type=item["event_type"], channel=item["channel"],
                                           enabled=item["enabled"], recipients=recipients, config=item.get("config") or {}))
    audit(session, firm.id, actor, "notifications.updated")


async def prepare_notification(session: AsyncSession, firm: Firm, *, event_type: str, enabled: bool | None = None,
                               recipients: list[str] | None = None, channel: str = "email") -> Plan:
    event_type = str(event_type or "").strip().lower()
    if event_type not in NOTIFICATION_EVENTS:
        raise AdminError(f"There is no alert called '{event_type}'. Alerts: {', '.join(NOTIFICATION_EVENTS)}.")
    if channel not in NOTIFICATION_CHANNELS:
        raise AdminError(f"There is no channel called '{channel}'.")
    if enabled is None and recipients is None:
        raise AdminError("Say whether the alert is on, who it goes to, or both.")
    wanted = _recipients(recipients) if recipients is not None else None
    if wanted:
        await _refuse_outsiders(session, firm, wanted)
    row = (await session.execute(select(NotificationConfig).where(
        NotificationConfig.firm_id == firm.id, NotificationConfig.event_type == event_type,
        NotificationConfig.channel == channel))).scalar_one_or_none()
    changes: list[dict] = []
    _diff(changes, "enabled", bool(row.enabled) if row else False, enabled)
    now = list(row.recipients or []) if row else []
    if wanted is not None and wanted != now:
        changes.append({"field": "recipients", "from": now, "to": wanted})
    return Plan("notifications", row, f"{event_type.replace('_', ' ').capitalize()} · {channel}", changes,
                values={"event_type": event_type, "channel": channel, "recipients": wanted})


async def set_notification(session: AsyncSession, firm: Firm, actor: User, *, event_type: str, enabled: bool | None = None,
                           recipients: list[str] | None = None, channel: str = "email") -> Outcome:
    plan = await prepare_notification(session, firm, event_type=event_type, enabled=enabled, recipients=recipients,
                                      channel=channel)
    v, row = plan.values, plan.record
    if row is None:
        row = NotificationConfig(firm_id=firm.id, event_type=v["event_type"], channel=v["channel"], enabled=bool(enabled),
                                 recipients=v["recipients"] or [], config={})
        session.add(row)
    else:
        if enabled is not None:
            row.enabled = enabled
        if v["recipients"] is not None:
            row.recipients = v["recipients"]
    await session.flush()
    audit(session, firm.id, actor, "notifications.updated", subject=v["event_type"],
          detail={"changed": [c["field"] for c in plan.changes]})
    return _done(plan, row)


# ── Firm research ─────────────────────────────────────────────────────────────────────────────────

RESEARCH_TYPES = ("house_view", "research", "playbook")
RESEARCH_ACTIONS = {"submit": "under_review", "publish": "published", "reject": "draft", "delete": None}


def research_out(d: ResearchDocument) -> dict:
    return {"id": str(d.id), "title": d.title, "doc_type": d.doc_type, "author": d.author, "summary": d.summary,
            "tags": d.tags, "status": getattr(d, "status", "published"), "version": getattr(d, "version", 1),
            "published_by": getattr(d, "published_by", None),
            "published_at": d.published_at.isoformat() if getattr(d, "published_at", None) else None,
            "created_at": d.created_at.isoformat() if d.created_at else None}


async def list_research(session: AsyncSession, firm: Firm) -> list[dict]:
    rows = (await session.execute(select(ResearchDocument).where(ResearchDocument.firm_id == firm.id))).scalars().all()
    return [research_out(d) for d in rows]


def prepare_research(*, title: str, body: str, doc_type: str | None = "house_view", summary: str | None = None,
                     tags: list[str] | None = None) -> Plan:
    title, body = str(title or "").strip(), str(body or "").strip()
    if not title or not body:
        raise AdminError("A research document needs its title and its text.")
    changes = [{"field": "title", "from": None, "to": title}, {"field": "doc_type", "from": None, "to": doc_type or "house_view"},
               {"field": "status", "from": None, "to": "draft"}]
    _diff(changes, "tags", None, list(tags) if tags else None)
    return Plan("research", None, title, changes, [("Length", f"{len(body.split()):,} words")])


async def add_research(session: AsyncSession, firm: Firm, actor: User, *, title: str, body: str,
                       doc_type: str | None = "house_view", author: str | None = None, summary: str | None = None,
                       tags: list[str] | None = None) -> tuple[Outcome, int]:
    """A research document, as a draft, chunked for retrieval (POST /api/admin/research)."""
    from app.aurea_core import knowledge

    plan = prepare_research(title=title, body=body, doc_type=doc_type, summary=summary, tags=tags)
    doc = ResearchDocument(firm_id=firm.id, title=plan.label, doc_type=doc_type or "house_view", author=author,
                           summary=summary, body=str(body).strip(), tags=list(tags or []), status="draft")
    session.add(doc)
    await session.flush()
    n = await knowledge.ingest_document(session, doc)
    audit(session, firm.id, actor, "research.created", subject=doc.title)
    return _done(plan, doc), n


async def prepare_research_action(session: AsyncSession, firm: Firm, doc_id: Any, action: str) -> Plan:
    action = str(action or "").strip().lower()
    if action not in RESEARCH_ACTIONS:
        raise AdminError(f"'{action}' is not something done to research. Actions: {', '.join(RESEARCH_ACTIONS)}.")
    doc = await _owned(session, ResearchDocument, firm, doc_id, "Document")
    to = RESEARCH_ACTIONS[action]
    changes: list[dict] = []
    notes: list[tuple[str, str]] = []
    if action == "delete":
        changes.append({"field": "document", "from": doc.status, "to": "deleted"})
        notes.append(("Cannot be undone", "The document and its retrieval chunks are deleted"))
    else:
        _diff(changes, "status", doc.status, to)
        if action == "publish":
            _diff(changes, "version", doc.version or 0, (doc.version or 0) + 1)
    return Plan("research", doc, doc.title, changes, notes, values={"action": action, "to": to})


async def research_action(session: AsyncSession, firm: Firm, actor: User, doc_id: Any, action: str,
                          reason: str | None = None) -> Outcome:
    """Submit a document for review, publish it, send it back to draft, or delete it (the research routes).
    Nothing records who submitted a document, so publishing cannot yet be held to a second person (B-147)."""
    plan = await prepare_research_action(session, firm, doc_id, action)
    doc, act = plan.record, plan.values["action"]
    if act == "delete":
        title = doc.title
        await session.execute(sql_delete(ResearchChunk).where(ResearchChunk.document_id == doc.id))
        await session.delete(doc)
        audit(session, firm.id, actor, "research.deleted", subject=title, detail=_with_reason(None, reason))
        await session.flush()
        return _done(plan)
    doc.status = plan.values["to"]
    if act == "publish":
        doc.published_by = actor.email
        doc.published_at = utcnow()
        doc.version = (doc.version or 0) + 1
    done = {"submit": "submitted", "publish": "published", "reject": "rejected"}[act]
    audit(session, firm.id, actor, f"research.{done}", subject=doc.title, detail=_with_reason(None, reason))
    await session.flush()
    return _done(plan)


async def update_research(session: AsyncSession, firm: Firm, actor: User, doc_id: Any, **fields) -> ResearchDocument:
    """A document's title, type, author or summary (PATCH /api/admin/research/{id})."""
    doc = await _owned(session, ResearchDocument, firm, doc_id, "Research document")
    for name in ("title", "doc_type", "author", "summary"):
        if fields.get(name) is not None:
            setattr(doc, name, fields[name])
    audit(session, firm.id, actor, "research.updated", subject=doc.title)
    return doc


# ── Model portfolios ──────────────────────────────────────────────────────────────────────────────

def model_out(mp, targets: list | None = None) -> dict:
    return {"id": str(mp.id), "name": mp.name, "description": mp.description, "drift_band": float(mp.drift_band),
            "benchmark_symbol": getattr(mp, "benchmark_symbol", None),
            "targets": [{"asset_class": t.asset_class, "target_weight": float(t.target_weight)} for t in (targets or [])],
            "created_at": mp.created_at.isoformat() if mp.created_at else None}


async def list_model_portfolios(session: AsyncSession, firm: Firm) -> list[dict]:
    from app.models.portfolio import ModelPortfolio, TargetAllocation

    rows = (await session.execute(select(ModelPortfolio).where(ModelPortfolio.firm_id == firm.id)
                                  .order_by(ModelPortfolio.created_at))).scalars().all()
    by_model: dict = {}
    if rows:
        for t in (await session.execute(select(TargetAllocation).where(
                TargetAllocation.model_id.in_([mp.id for mp in rows])))).scalars().all():
            by_model.setdefault(t.model_id, []).append(t)
    return [model_out(mp, by_model.get(mp.id, [])) for mp in rows]


async def mandates_following(session: AsyncSession, firm_id: uuid.UUID, model_id: uuid.UUID) -> list[Mandate]:
    return list((await session.execute(select(Mandate).where(
        Mandate.firm_id == firm_id, Mandate.model_portfolio_id == model_id))).scalars().all())


async def prepare_model_portfolio(session: AsyncSession, firm: Firm, *, model_id: Any = None, name: str | None = None,
                                  description: str | None = None, drift_band: float | None = None,
                                  benchmark_symbol: str | None = None) -> Plan:
    from app.models.portfolio import ModelPortfolio

    if drift_band is not None and not 0 < drift_band <= 1:
        raise AdminError("A drift band is a fraction above 0 and at most 1 (0.05 for 5%).")
    bench = benchmark_symbol.strip() if isinstance(benchmark_symbol, str) else benchmark_symbol
    changes: list[dict] = []
    notes: list[tuple[str, str]] = []
    if model_id:
        mp = await _owned(session, ModelPortfolio, firm, model_id, "Model portfolio")
        _diff(changes, "name", mp.name, name)
        _diff(changes, "description", mp.description, description)
        _diff(changes, "drift_band", float(mp.drift_band), float(drift_band) if drift_band is not None else None)
        if bench is not None and (bench or None) != (mp.benchmark_symbol or None):
            changes.append({"field": "benchmark_symbol", "from": mp.benchmark_symbol, "to": bench or None})
            notes.append(("Composites", "Disclose the benchmark change"))
        if drift_band is not None and float(drift_band) != float(mp.drift_band):
            notes.append(("Mandates following", str(len(await mandates_following(session, firm.id, mp.id)))))
        return Plan("model_portfolios", mp, mp.name, changes, notes)
    name = (name or "").strip()
    if not name:
        raise AdminError("A new model needs its name.")
    changes.append({"field": "name", "from": None, "to": name})
    _diff(changes, "description", None, description)
    changes.append({"field": "drift_band", "from": None, "to": float(drift_band if drift_band is not None else 0.05)})
    _diff(changes, "benchmark_symbol", None, bench or None)
    return Plan("model_portfolios", None, name, changes, [("Targets", "None yet")])


async def save_model_portfolio(session: AsyncSession, firm: Firm, actor: User, *, model_id: Any = None,
                               name: str | None = None, description: str | None = None, drift_band: float | None = None,
                               benchmark_symbol: str | None = None,
                               benchmark_reason: str = "Changed by an administrator in model portfolio settings.") -> Outcome:
    """Add a model portfolio (POST /api/admin/model-portfolios) or change one (PATCH ...). A benchmark change goes on
    the ledger through composites.record_benchmark_change, because a composite on the model discloses it."""
    from app.aurea_core import composites
    from app.models.portfolio import ModelPortfolio

    plan = await prepare_model_portfolio(session, firm, model_id=model_id, name=name, description=description,
                                         drift_band=drift_band, benchmark_symbol=benchmark_symbol)
    if plan.record is None:
        mp = ModelPortfolio(firm_id=firm.id, name=plan.label, description=description,
                            drift_band=drift_band if drift_band is not None else 0.05, benchmark_symbol=benchmark_symbol)
        session.add(mp)
        await session.flush()
        audit(session, firm.id, actor, "model_portfolio.created", subject=mp.name)
        return _done(plan, mp)
    mp = plan.record
    if name is not None:
        mp.name = name
    if description is not None:
        mp.description = description
    if drift_band is not None:
        mp.drift_band = drift_band
    if benchmark_symbol is not None:
        await composites.record_benchmark_change(session, mp, to=benchmark_symbol.strip(),
                                                 actor=actor.full_name or actor.email, reason=benchmark_reason)
    audit(session, firm.id, actor, "model_portfolio.updated", subject=mp.name,
          detail={"changed": [c["field"] for c in plan.changes]})
    await session.flush()
    return _done(plan)


async def prepare_model_targets(session: AsyncSession, firm: Firm, model_id: Any, targets: list[dict]) -> Plan:
    from app.aurea_core.risk_profile import CAPACITY_EQUITY_MAX, on_file
    from app.models.portfolio import ModelPortfolio, TargetAllocation

    mp = await _owned(session, ModelPortfolio, firm, model_id, "Model portfolio")
    total = sum(float(t["target_weight"]) for t in targets)
    if targets and not (0.995 <= total <= 1.005):
        raise AdminError(f"Weights must sum to 1.0 (got {total:.4f}).", 400)
    now: dict[str, float] = {}
    for t in (await session.execute(select(TargetAllocation).where(TargetAllocation.model_id == mp.id))).scalars().all():
        now[str(t.asset_class)] = now.get(str(t.asset_class), 0.0) + float(t.target_weight)
    after: dict[str, float] = {}
    for t in targets:
        after[str(t["asset_class"])] = after.get(str(t["asset_class"]), 0.0) + float(t["target_weight"])
    changes = [{"field": cls, "from": now.get(cls), "to": after.get(cls)}
               for cls in dict.fromkeys([*now, *after]) if round(now.get(cls, 0.0), 6) != round(after.get(cls, 0.0), 6)]
    following = await mandates_following(session, firm.id, mp.id)
    over = [m for m in following
            if (ceiling := CAPACITY_EQUITY_MAX.get(on_file(m.suitability)[1] or "")) is not None
            and after.get("equity", 0.0) > ceiling]
    # A mandate over its capacity ceiling pauses the rebalancer for the whole firm, so a model is not retargeted
    # past the ceiling of a mandate that follows it - the same line a risk profile and an IPS are held to.
    if over and after.get("equity", 0.0) > now.get("equity", 0.0):
        raise AdminError(f"{len(over)} mandate(s) following this model would hold more equity than their client's capacity "
                         f"allows ({', '.join(m.name for m in over[:3])}). Move them to another model first.", 422)
    notes = [("Mandates following", str(len(following)))]
    return Plan("model_portfolios", mp, mp.name, changes, notes, values={"total": total})


async def set_model_targets(session: AsyncSession, firm: Firm, actor: User, model_id: Any, targets: list[dict]) -> Outcome:
    """Replace a model's target weights (PUT /api/admin/model-portfolios/{id}/targets); they sum to 1 ± 0.005."""
    from app.models.portfolio import TargetAllocation

    plan = await prepare_model_targets(session, firm, model_id, targets)
    mp = plan.record
    for ta in (await session.execute(select(TargetAllocation).where(TargetAllocation.model_id == mp.id))).scalars().all():
        await session.delete(ta)
    for t in targets:
        session.add(TargetAllocation(firm_id=firm.id, model_id=mp.id, asset_class=t["asset_class"],
                                     target_weight=float(t["target_weight"])))
    audit(session, firm.id, actor, "model_portfolio.targets_set", subject=mp.name,
          detail={"targets": {c["field"]: c["to"] for c in plan.changes}})
    await session.flush()
    return _done(plan)


async def prepare_model_delete(session: AsyncSession, firm: Firm, model_id: Any) -> Plan:
    from app.models.portfolio import ModelPortfolio

    mp = await _owned(session, ModelPortfolio, firm, model_id, "Model portfolio")
    # Deleting cascades to its composites and sleeves and leaves its mandates with no model; refuse while any follows it.
    following = len(await mandates_following(session, firm.id, mp.id))
    if following:
        raise AdminError(f"{following} mandate(s) follow this model. Move them to another model first.", 409)
    return Plan("model_portfolios", mp, mp.name, [{"field": "model", "from": mp.name, "to": "deleted"}],
                [("Cannot be undone", "The model, its targets, composites and sleeves are deleted")])


async def delete_model_portfolio(session: AsyncSession, firm: Firm, actor: User, model_id: Any) -> Outcome:
    plan = await prepare_model_delete(session, firm, model_id)
    audit(session, firm.id, actor, "model_portfolio.deleted", subject=plan.record.name)
    await session.delete(plan.record)
    await session.flush()
    return _done(plan)


# ── Connectors ────────────────────────────────────────────────────────────────────────────────────

REDACTED = "••••••••"


def secret_keys(provider_key: str) -> set[str]:
    from app.conduit.registry import get_provider_def

    pdef = get_provider_def(provider_key)
    return {f.key for f in (pdef.config_schema if pdef else []) if f.secret}


def connector_out(connector) -> dict:
    """A connector with its secrets redacted, as the Connectors page reads it."""
    from app.conduit.registry import get_provider_def

    cfg = dict(connector.config or {})
    for k in secret_keys(connector.provider_key):
        if cfg.get(k):
            cfg[k] = REDACTED
    pdef = get_provider_def(connector.provider_key)
    return {"id": str(connector.id), "domain": connector.domain, "provider_key": connector.provider_key,
            "display_name": connector.display_name, "status": connector.status, "use_mock": connector.use_mock,
            "config": cfg, "sync_cron": connector.sync_cron, "supports_live": pdef.supports_live if pdef else False,
            "last_synced_at": connector.last_synced_at.isoformat() if connector.last_synced_at else None,
            "last_error": connector.last_error}


async def list_connectors(session: AsyncSession, firm: Firm) -> list:
    from app.models.connectors import Connector

    return list((await session.execute(select(Connector).where(Connector.firm_id == firm.id))).scalars().all())


def prepare_add_connector(provider_key: str) -> Plan:
    from app.conduit.registry import get_provider_def

    pdef = get_provider_def(str(provider_key or "").strip())
    if not pdef:
        raise AdminError("Unknown provider", 404)
    return Plan("connectors", None, pdef.display_name,
                [{"field": "provider", "from": None, "to": pdef.key}, {"field": "live", "from": None, "to": False},
                 {"field": "sync_cron", "from": None, "to": pdef.default_cron}],
                [("Data", "Mock until someone switches it to a real feed")], values={"pdef": pdef})


async def add_connector(session: AsyncSession, firm: Firm, actor: User, provider_key: str) -> Outcome:
    """A connector from the catalogue, starting as a mock (POST /api/conduit/connectors)."""
    from app.models.connectors import Connector

    plan = prepare_add_connector(provider_key)
    pdef = plan.values["pdef"]
    c = Connector(firm_id=firm.id, domain=pdef.domain, provider_key=pdef.key, display_name=pdef.display_name,
                  status=ConnectorStatus.MOCK, use_mock=True, sync_cron=pdef.default_cron, config={})
    session.add(c)
    await session.flush()
    audit(session, firm.id, actor, "connector.created", subject=c.display_name, detail={"provider": pdef.key})
    return _done(plan, c)


def _status_after(c, use_mock: bool | None, enabled: bool | None) -> tuple[bool, str]:
    """The (use_mock, status) a change leaves, in the order the route has always applied them."""
    mock, status = c.use_mock, str(c.status)
    if use_mock is not None:
        mock = use_mock
        status = ConnectorStatus.MOCK if use_mock else ConnectorStatus.CONFIGURED
    if enabled is not None:
        status = ConnectorStatus.DISABLED if not enabled else (ConnectorStatus.MOCK if mock else ConnectorStatus.CONFIGURED)
    return mock, str(status)


async def prepare_connector(session: AsyncSession, firm: Firm, connector_id: Any, *, use_mock: bool | None = None,
                            enabled: bool | None = None, sync_cron: str | None = None, display_name: str | None = None,
                            config: dict | None = None) -> Plan:
    from app.conduit.registry import get_provider_def
    from app.models.connectors import Connector

    c = await _owned(session, Connector, firm, connector_id, "Connector")
    mock, status = _status_after(c, use_mock, enabled)
    changes: list[dict] = []
    _diff(changes, "live", not c.use_mock, not mock if use_mock is not None else None)
    _diff(changes, "enabled", str(c.status) != ConnectorStatus.DISABLED,
          status != ConnectorStatus.DISABLED if enabled is not None else None)
    _diff(changes, "status", str(c.status), status if (use_mock is not None or enabled is not None) else None)
    _diff(changes, "sync_cron", c.sync_cron, sync_cron)
    _diff(changes, "display_name", c.display_name, display_name)
    if config:
        # Which settings changed, never their values.
        changes.append({"field": "config", "from": None, "to": sorted(k for k, v in config.items() if v != REDACTED)})
    notes: list[tuple[str, str]] = []
    pdef = get_provider_def(c.provider_key)
    if use_mock is False and c.use_mock:
        notes.append(("Real feed", f"{pdef.display_name if pdef else c.provider_key}: syncs reach the provider"
                      if pdef and pdef.supports_live else "Not built for this provider: syncs stay mock"))
    return Plan("connectors", c, c.display_name, changes, notes, values={"supports_live": bool(pdef and pdef.supports_live)})


async def set_connector(session: AsyncSession, firm: Firm, actor: User, connector_id: Any, *, use_mock: bool | None = None,
                        enabled: bool | None = None, sync_cron: str | None = None, display_name: str | None = None,
                        config: dict | None = None) -> Outcome:
    """A connector's feed, switch, cadence, name or settings (PATCH /api/conduit/connectors/{id}). A redacted
    placeholder never overwrites a secret, and the audit log names which settings changed, never their values."""
    plan = await prepare_connector(session, firm, connector_id, use_mock=use_mock, enabled=enabled, sync_cron=sync_cron,
                                   display_name=display_name, config=config)
    c = plan.record
    if config is not None:
        merged = dict(c.config or {})
        for k, v in config.items():
            if v == REDACTED:
                continue
            merged[k] = v
        c.config = merged
    if use_mock is not None:
        c.use_mock = use_mock
        c.status = ConnectorStatus.MOCK if use_mock else ConnectorStatus.CONFIGURED
    if sync_cron is not None:
        c.sync_cron = sync_cron
    if display_name is not None:
        c.display_name = display_name
    if enabled is not None:
        c.status = ConnectorStatus.DISABLED if not enabled else (ConnectorStatus.MOCK if c.use_mock else ConnectorStatus.CONFIGURED)
    audit(session, firm.id, actor, "connector.updated", subject=c.display_name,
          detail={"config_keys": sorted((config or {}).keys()), "use_mock": use_mock, "enabled": enabled, "sync_cron": sync_cron})
    await session.flush()
    return _done(plan)


async def prepare_sync(session: AsyncSession, firm: Firm, connector_id: Any) -> Plan:
    from app.models.connectors import Connector
    from app.models.enums import ConnectorDomain

    c = await _owned(session, Connector, firm, connector_id, "Connector")
    live_prices = c.domain == ConnectorDomain.MARKET_RESEARCH_DATA and not c.use_mock
    return Plan("connectors", c, c.display_name, [],
                [("Mode", "Live" if not c.use_mock else "Mock"),
                 ("Writes", "Prices from the real feed" if live_prices else "A sync record only")],
                values={"live_prices": live_prices})


async def sync_connector(session: AsyncSession, firm: Firm, actor: User, connector_id: Any):
    """Run one sync now (POST /api/conduit/connectors/{id}/sync). A live market-data feed writes prices."""
    from app.conduit.service import run_sync

    plan = await prepare_sync(session, firm, connector_id)
    c = plan.record
    before = c.last_synced_at.isoformat() if c.last_synced_at else None
    sync = await run_sync(session, c)
    after = c.last_synced_at.isoformat() if c.last_synced_at else None
    plan.changes = [{"field": "last_synced_at", "from": before, "to": after},
                    {"field": "records_ingested", "from": None, "to": sync.records_ingested}]
    audit(session, firm.id, actor, "connector.synced", subject=c.display_name, detail={"status": sync.status})
    return _done(plan), sync


# ── Assignments and the audit log (reads) ───────────────────────────────────────────────────────

async def list_assignments(session: AsyncSession, firm: Firm) -> list[dict]:
    from app.aurea_core.advisers import members
    from app.models.graph import RelationshipEdge

    edges = (await session.execute(select(RelationshipEdge).where(
        RelationshipEdge.firm_id == firm.id, RelationshipEdge.kind == "adviser").order_by(RelationshipEdge.created_at.desc()))).scalars().all()
    # An edge to a person or an entity, as the seed and onboarding write them, names that member's household.
    people, entities = await members(session, firm.id)
    out = []
    for e in edges:
        adviser = await session.get(User, e.from_id) if e.from_type == "user" else None
        home = e.to_id if e.to_type == "household" else people.get(e.to_id) if e.to_type == "person" else entities.get(e.to_id)
        household = await session.get(Household, home) if home else None
        out.append({"id": str(e.id), "household_id": str(home or e.to_id),
                    "household_name": household.name if household else str(e.to_id), "adviser_id": str(e.from_id),
                    "adviser_name": adviser.full_name if adviser else "—", "adviser_email": adviser.email if adviser else None,
                    "created_at": e.created_at.isoformat() if e.created_at else None})
    return out


async def audit_log(session: AsyncSession, firm: Firm, *, limit: int = 100, event_type: str | None = None) -> list[dict]:
    from app.models.governance import AutonomyChange, LedgerEntry

    rows = []
    q = select(AuditEvent).where(AuditEvent.firm_id == firm.id)
    if event_type:
        q = q.where(AuditEvent.event_type == event_type)
    for r in (await session.execute(q.order_by(AuditEvent.created_at.desc()).limit(limit))).scalars().all():
        rows.append({"ts": r.created_at.isoformat(), "category": "client" if r.event_type.startswith("client.") else "access",
                     "event_type": r.event_type, "actor": r.actor_email, "subject": r.subject, "detail": r.detail})
    if not event_type or event_type.startswith("recommendation"):
        for r in (await session.execute(
                select(LedgerEntry).where(LedgerEntry.firm_id == firm.id, LedgerEntry.event_type.in_(["decision", "recommendation"]))
                .order_by(LedgerEntry.created_at.desc()).limit(50))).scalars().all():
            rows.append({"ts": r.created_at.isoformat(), "category": "recommendation",
                         "event_type": f"recommendation.{r.event_type}", "actor": r.actor or "agent",
                         "subject": r.content.get("subject") if r.content else None, "detail": {"agent_key": r.agent_key}})
    if not event_type or event_type.startswith("autonomy"):
        for r in (await session.execute(select(AutonomyChange).where(AutonomyChange.firm_id == firm.id)
                                        .order_by(AutonomyChange.created_at.desc()).limit(30))).scalars().all():
            rows.append({"ts": r.created_at.isoformat(), "category": "autonomy", "event_type": "autonomy.tier_changed",
                         "actor": "adaptive" if r.automatic else "admin", "subject": r.agent_key,
                         "detail": {"from": r.from_tier, "to": r.to_tier, "reason": r.reason}})
    rows.sort(key=lambda x: x["ts"], reverse=True)
    return rows[:limit]


# ── One section of the settings, as rows (the conversation's read) ──────────────────────────────

SECTIONS = ("firm", "users", "agents", "schedules", "segments", "mandate_types", "mandates", "notifications", "research",
            "model_portfolios", "connectors", "cmas", "model_routing", "assignments", "audit", "usage")
# Who reads which section: the admin and compliance read every one; operations its connectors; the portfolio team
# and research their models and the research they ground on.
SECTION_READERS = {UserRole.ADMIN: set(SECTIONS), UserRole.COMPLIANCE: set(SECTIONS), UserRole.OPERATIONS: {"connectors"},
                   UserRole.PORTFOLIO_TEAM: {"model_portfolios", "research"}, UserRole.RESEARCH_CIO: {"model_portfolios", "research"}}


def _col(key: str, label: str, kind: str = "text") -> dict:
    return {"key": key, "label": label, "kind": kind}


def _day(iso: str | None) -> str | None:
    return iso[:10] if iso else None


async def read_section(session: AsyncSession, firm: Firm, section: str) -> dict:
    """One section of the platform's settings as figures and short values: columns and rows, no prose, no token,
    no connector setting and no key."""
    section = str(section or "").strip().lower()
    if section not in SECTIONS:
        raise AdminError(f"There is no settings section called '{section}'. Sections: {', '.join(SECTIONS)}.")
    if section == "firm":
        b = firm.branding or {}
        from app.llm.service import firm_llm_creds, llm_service

        fields = [("Name", firm.name), ("Legal name", firm.legal_name), ("Jurisdiction", firm.jurisdiction),
                  ("Regulator", firm.regulator), ("Base currency", firm.base_currency), ("Primary colour", b.get("primary")),
                  ("Accent colour", b.get("accent")), ("Logo text", b.get("logo_text")), ("Tagline", b.get("tagline")),
                  ("Model provider connected", "Yes" if llm_service.enabled(firm_llm_creds(firm)) else "No")]
        return {"section": section, "title": "Firm profile", "columns": [_col("field", "Field"), _col("value", "Value")],
                "rows": [{"id": None, "field": k, "value": v} for k, v in fields]}
    if section == "users":
        rows = [{"id": u["id"], "name": u["full_name"], "email": u["email"], "role": str(u["role"]), "title": u["title"],
                 "active": bool(u["is_active"]), "since": _day(u["created_at"])} for u in await list_users(session, firm)]
        return {"section": section, "title": "Users", "rows": rows,
                "columns": [_col("name", "Name"), _col("email", "Email"), _col("role", "Role", "chip"), _col("title", "Title"),
                            _col("active", "Active", "bool"), _col("since", "Since", "date")]}
    if section == "agents":
        rows = [{"id": str(c["agent_key"]), "agent": agent_name(c["agent_key"]), "enabled": bool(c["enabled"]),
                 "tier": str(c["default_tier"]), "paused": bool(c["paused"]), "schedule": c["schedule_cron"],
                 "scheduled": bool(c["schedule_enabled"])} for c in await list_agent_configs(session, firm)]
        rows.sort(key=lambda r: r["agent"])
        return {"section": section, "title": "Agents", "rows": rows,
                "columns": [_col("agent", "Agent"), _col("enabled", "Enabled", "bool"), _col("tier", "Tier", "chip"),
                            _col("paused", "Paused", "bool"), _col("schedule", "Schedule"), _col("scheduled", "Scheduled", "bool")]}
    if section == "schedules":
        rows = [{"id": r["agent_key"], "job": r["label"], "cron": r["cron"], "default": r["default_cron"],
                 "enabled": r["enabled"], "paused": r["paused"], "last_run": _day(r["last_run_at"]),
                 "last_status": str(r["last_run_status"]) if r["last_run_status"] else None, "runs": r["run_count"]}
                for r in await list_schedules(session, firm)]
        return {"section": section, "title": "Schedules", "rows": rows,
                "columns": [_col("job", "Job"), _col("cron", "Cron"), _col("default", "Default"), _col("enabled", "On", "bool"),
                            _col("paused", "Paused", "bool"), _col("last_run", "Last run", "date"),
                            _col("last_status", "Status", "chip"), _col("runs", "Runs")]}
    if section == "segments":
        rows = [{"id": s["id"], "slug": s["slug"], "label": s["label"], "fee": s["fee_tier_bps"], "min_aum": s["min_aum_usd"],
                 "households": await households_on_segment(session, firm.id, s["slug"]), "active": bool(s["is_active"])}
                for s in await list_segments(session, firm)]
        return {"section": section, "title": "Client segments", "rows": rows,
                "columns": [_col("label", "Segment"), _col("slug", "Slug"), _col("fee", "Fee", "bps"),
                            _col("min_aum", "Minimum (USD)", "money"), _col("households", "Households"), _col("active", "Active", "bool")]}
    if section == "mandate_types":
        rows = [{"id": m["id"], "slug": m["slug"], "label": m["label"], "tier": str(m["default_autonomy_tier"])}
                for m in await list_mandate_types(session, firm)]
        return {"section": section, "title": "Mandate types", "rows": rows,
                "columns": [_col("label", "Type"), _col("slug", "Slug"), _col("tier", "Default tier", "chip")]}
    if section == "mandates":
        rows = [{"id": m["id"], "client": m["client_name"], "mandate": m["name"], "type": str(m["mandate_type"]),
                 "tolerance": m["suitability"].get("risk_tolerance"), "horizon": m["suitability"].get("investment_horizon"),
                 "max_equity": m["suitability"].get("max_equity"), "active": bool(m["is_active"])}
                for m in await list_mandates(session, firm)]
        return {"section": section, "title": "Mandates", "rows": rows,
                "columns": [_col("client", "Client"), _col("mandate", "Mandate"), _col("type", "Type", "chip"),
                            _col("tolerance", "Risk tolerance", "chip"), _col("horizon", "Horizon"),
                            _col("max_equity", "Max equity", "pct"), _col("active", "Active", "bool")]}
    if section == "notifications":
        rows = [{"id": n["id"], "event": n["event_type"], "channel": n["channel"], "enabled": bool(n["enabled"]),
                 "recipients": ", ".join(n["recipients"]) or None} for n in await list_notifications(session, firm)]
        return {"section": section, "title": "Notifications", "rows": rows,
                "columns": [_col("event", "Alert"), _col("channel", "Channel", "chip"), _col("enabled", "On", "bool"),
                            _col("recipients", "Recipients")]}
    if section == "research":
        rows = [{"id": d["id"], "title": d["title"], "type": d["doc_type"], "status": d["status"], "version": d["version"],
                 "published_by": d["published_by"], "added": _day(d["created_at"])} for d in await list_research(session, firm)]
        return {"section": section, "title": "Firm research", "rows": rows,
                "columns": [_col("title", "Title"), _col("type", "Type", "chip"), _col("status", "Status", "chip"),
                            _col("version", "Version"), _col("published_by", "Published by"), _col("added", "Added", "date")]}
    if section == "model_portfolios":
        rows = []
        for mp in await list_model_portfolios(session, firm):
            weights = {t["asset_class"]: t["target_weight"] for t in mp["targets"]}
            rows.append({"id": mp["id"], "name": mp["name"], "drift_band": mp["drift_band"], "benchmark": mp["benchmark_symbol"],
                         "equity": weights.get("equity"), "fixed_income": weights.get("fixed_income"), "cash": weights.get("cash"),
                         "following": len(await mandates_following(session, firm.id, uuid.UUID(mp["id"])))})
        return {"section": section, "title": "Model portfolios", "rows": rows,
                "columns": [_col("name", "Model"), _col("drift_band", "Drift band", "pct"), _col("benchmark", "Benchmark"),
                            _col("equity", "Equity", "pct"), _col("fixed_income", "Fixed income", "pct"), _col("cash", "Cash", "pct"),
                            _col("following", "Mandates following")]}
    if section == "connectors":
        rows = [{"id": str(c.id), "provider": c.provider_key, "name": c.display_name, "live": not c.use_mock,
                 "status": str(c.status), "last_sync": c.last_synced_at.isoformat()[:10] if c.last_synced_at else None,
                 "sync_cron": c.sync_cron} for c in await list_connectors(session, firm)]
        return {"section": section, "title": "Connectors", "rows": rows,
                "columns": [_col("name", "Connector"), _col("provider", "Provider"), _col("live", "Live", "bool"),
                            _col("status", "Status", "chip"), _col("last_sync", "Last sync", "date"), _col("sync_cron", "Sync cron")]}
    if section == "cmas":
        t = cma_table(firm)
        rows = [{"id": cls, "asset_class": cls, "expected_return": er, "volatility": vol, "overridden": cls in t["overrides"]}
                for cls, (er, vol) in t["effective"].items()]
        return {"section": section, "title": "Capital market assumptions", "rows": rows,
                "columns": [_col("asset_class", "Asset class"), _col("expected_return", "Expected return", "pct"),
                            _col("volatility", "Volatility", "pct"), _col("overridden", "Firm's own", "bool")]}
    if section == "model_routing":
        from app.llm.service import TASK_DEFAULTS

        own = firm.model_config_json or {}
        rows = [{"id": task, "task": task, "model": own.get(task) or TASK_DEFAULTS[task], "default": TASK_DEFAULTS[task],
                 "overridden": bool(own.get(task))} for task in MODEL_TASKS]
        return {"section": section, "title": "Model routing", "rows": rows,
                "columns": [_col("task", "Task"), _col("model", "Model"), _col("default", "Default"), _col("overridden", "Firm's own", "bool")]}
    if section == "assignments":
        rows = [{"id": a["id"], "household": a["household_name"], "adviser": a["adviser_name"], "since": _day(a["created_at"])}
                for a in await list_assignments(session, firm)]
        return {"section": section, "title": "Adviser assignments", "rows": rows,
                "columns": [_col("household", "Household"), _col("adviser", "Adviser"), _col("since", "Since", "date")]}
    if section == "audit":
        rows = [{"id": None, "when": _day(r["ts"]), "category": r["category"], "event": r["event_type"], "actor": r["actor"],
                 "subject": r["subject"]} for r in await audit_log(session, firm, limit=50)]
        return {"section": section, "title": "Audit log", "rows": rows,
                "columns": [_col("when", "When", "date"), _col("category", "Category", "chip"), _col("event", "Event"),
                            _col("actor", "By"), _col("subject", "Subject")]}
    from app.llm import usage as llm_usage

    u = await llm_usage.summary(session, firm.id)
    fields = [("Model calls", u["calls"]), ("Tokens", u["total_tokens"]), ("Estimated cost (USD)", u["est_cost"]),
              ("Fallback rate", f"{u['fallback_rate']:.0%}"), ("Entities masked", u["redacted_entities"])]
    return {"section": section, "title": "AI usage", "columns": [_col("field", "Measure"), _col("value", "Value")],
            "rows": [{"id": None, "field": k, "value": v} for k, v in fields]}
