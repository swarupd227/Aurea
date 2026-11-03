"""Who may change the platform (B-96, first part): the access gaps found before the admin pages become agentic.

    python -m tests.verify_admin_controls

Against a throwaway SQLite database, this checks that:

  - a firm's administrator passes every role check in their firm, and the platform owner passes none: it
    signs in to an arbitrary firm and manages firms from its own page;
  - compliance reads the platform's settings and keeps its own controls (the regulatory rules), while
    users, roles, agent controls and firm settings are the admin's alone;
  - nobody grants the platform owner's role, changes their own role or switches themselves off;
  - an agent's tier stays within the licence, an agent conduct surveillance paused stays paused while its
    flag is open, a schedule is a valid cron, and each change is an AutonomyChange made by a person;
  - a mandate type's tier is held to the licence; the governance settings are not reachable through the
    generic firm settings; a client is not created directly: it starts as an onboarding case;
  - a model portfolio is the portfolio team's and research's, and one that mandates follow is not deleted;
    a connector is operations' to set up and switch live;
  - a firm switched off signs its people out and stops them signing in, and the switch is said, not toggled.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import admin, auth, conduit, portfolios, superadmin
from app.aurea_core import agent_controls
from app.core import security
from app.core.db import Base
from app.core.security import create_access_token, hash_password, require_roles, staff_user
from app.models.enums import AgentKey, AutonomyTier, UserRole
from app.models.governance import AutonomyChange, SurveillanceFlag
from app.models.graph import Household, Mandate, Person, RelationshipEdge
from app.models.identity import AuditEvent, User
from app.models.portfolio import ModelPortfolio
from app.models.tenant import AgentConfig, Firm

PASS, FAIL = [], []
R = UserRole


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def outcome(coro) -> str:
    try:
        await coro
        return "allowed"
    except (HTTPException, agent_controls.ControlError) as exc:
        return f"refused {getattr(exc, 'status_code', '')}".strip()


def guard(dep):
    """The checker inside a Depends(require_roles(...))."""
    return dep.dependency


async def main() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Tui", slug="tui", base_currency="NZD", jurisdiction="NZ", settings={})
            licensed = Firm(id=uuid.uuid4(), name="Ganga", slug="ganga", base_currency="INR", jurisdiction="IN",
                            settings={"licences": ["investment_adviser"]})
            other = Firm(id=uuid.uuid4(), name="Other", slug="other", base_currency="NZD", jurisdiction="NZ", settings={})
            s.add_all([firm, licensed, other])
            await s.flush()
            people = {}
            for key, role, f in (("ada", R.ADMIN, firm), ("cora", R.COMPLIANCE, firm), ("ann", R.ADVISER, firm),
                                 ("ola", R.OPERATIONS, firm), ("pete", R.PORTFOLIO_TEAM, firm), ("sam", R.SUPERADMIN, firm),
                                 ("zed", R.ADVISER, other), ("gita", R.ADMIN, licensed)):
                people[key] = User(id=uuid.uuid4(), firm_id=f.id, email=f"{key}@t.test", full_name=key.title(),
                                   hashed_password=hash_password("pw"), role=role, is_active=True)
                s.add(people[key])
            await s.flush()
            ada, cora, ann, ola, pete, sam, zed, gita = (people[k] for k in ("ada", "cora", "ann", "ola", "pete", "sam", "zed", "gita"))

            print("\n=== The administrator passes every check in their firm; the platform owner passes none ===")
            check("an admin passes an operations check", await outcome(guard(conduit.SetUp)(user=ada)), "allowed")
            check("the platform owner does not", await outcome(guard(conduit.SetUp)(user=sam)), "refused 403")
            check("nor the admin-only check", await outcome(guard(admin.AdminOnly)(user=sam)), "refused 403")
            check("nor a staff route", await outcome(staff_user(user=sam)), "refused 403")
            check("the platform owner is not staff of a firm", R.SUPERADMIN in security.STAFF_ROLES, False)
            check("its own routes still admit it", await outcome(superadmin._superadmin_only(user=sam)), "allowed")

            print("\n=== Compliance reads the settings and keeps its own controls ===")
            check("compliance reads the admin pages", await outcome(guard(admin.AdminDep)(user=cora)), "allowed")
            check("compliance changes none of them", await outcome(guard(admin.AdminOnly)(user=cora)), "refused 403")
            rule_guard = inspect.signature(admin.set_rule).parameters["user"].default.dependency
            check("the regulatory rules are compliance's own", await outcome(rule_guard(user=cora)), "allowed")
            check("and an adviser's not", await outcome(rule_guard(user=ann)), "refused 403")

            print("\n=== Nobody grants the platform owner's role, or changes their own ===")
            check("an admin cannot create a platform owner", await outcome(admin.create_user(
                admin.UserCreate(email="x@t.test", full_name="X", role=R.SUPERADMIN), actor=ada, firm=firm, db=s)), "refused 403")
            check("nor make one", await outcome(admin.update_user(ann.id, admin.UserUpdate(role=R.SUPERADMIN), actor=ada, firm=firm, db=s)),
                  "refused 403")
            check("nor change their own role", await outcome(admin.update_user(ada.id, admin.UserUpdate(role=R.ADVISER), actor=ada, firm=firm, db=s)),
                  "refused 400")
            check("nor switch themselves off", await outcome(admin.update_user(ada.id, admin.UserUpdate(is_active=False), actor=ada, firm=firm, db=s)),
                  "refused 400")
            await admin.update_user(ann.id, admin.UserUpdate(role=R.PARAPLANNER), actor=ada, firm=firm, db=s)
            await s.flush()
            logged = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "user.role_changed"))).scalars().all()
            check("a role change is on the audit trail", [(e.subject, e.detail) for e in logged], [("ann@t.test", {"from": "adviser", "to": "paraplanner"})])
            ann.role = R.ADVISER

            print("\n=== An agent's controls ===")
            change = lambda f, **kw: agent_controls.change(s, f, AgentKey.DRIFT_REBALANCING, actor_id=ada.id, actor_email=ada.email, **kw)  # noqa: E731
            check("a tier above the licence is refused", await outcome(change(licensed, default_tier=AutonomyTier.TIER_3)), "refused")
            check("a bad cron is refused", await outcome(change(firm, schedule_cron="every tuesday")), "refused")
            await change(firm, default_tier=AutonomyTier.TIER_2, reason="Ready for proposals")
            moved = (await s.execute(select(AutonomyChange).where(AutonomyChange.firm_id == firm.id))).scalars().all()
            check("a tier change is an AutonomyChange made by a person", [(m.to_tier, m.automatic, "Ready for proposals" in m.reason) for m in moved],
                  [("tier_2", False, True)])
            cfg = (await s.execute(select(AgentConfig).where(AgentConfig.firm_id == firm.id))).scalar_one()
            cfg.paused, cfg.paused_reason = True, "Auto-paused by conduct surveillance: suitability"
            flag = SurveillanceFlag(firm_id=firm.id, target_agent_key="drift_rebalancing", severity="high", category="suitability",
                                    finding="x", auto_paused_agent=True)
            s.add(flag)
            await s.flush()
            check("an agent surveillance paused is not resumed while its flag is open", await outcome(change(firm, paused=False)), "refused")
            check("through the admin route too", await outcome(admin.update_agent_config(
                AgentKey.DRIFT_REBALANCING, admin.AgentConfigUpdate(paused=False), user=ada, firm=firm, db=s)), "refused 422")
            flag.resolved = True
            await s.flush()
            await change(firm, paused=False, reason="Flag resolved by compliance")
            check("once the flag is resolved it may be", (cfg.paused, cfg.paused_reason), (False, None))
            resumed = (await s.execute(select(AutonomyChange).where(AutonomyChange.firm_id == firm.id).order_by(AutonomyChange.created_at))).scalars().all()
            check("and the resumption is on the record", "Flag resolved by compliance" in resumed[-1].reason, True)

            print("\n=== The rest of the admin page's writes ===")
            mt = admin.MandateTypeCreate(slug="discretionary_plus", label="D+", default_autonomy_tier=AutonomyTier.TIER_3)
            check("a mandate type's tier is held to the licence", await outcome(admin.create_mandate_type(mt, actor=gita, firm=licensed, db=s)), "refused 422")
            for key in ("compliance", "foundation", "cmas", "pii"):
                check(f"the {key} settings are not reachable through the firm's settings",
                      await outcome(admin.update_firm(admin.FirmUpdate(settings={key: {}}), user=ada, firm=firm, db=s)), "refused 422")
            check("creating a client directly is retired: a client starts as an onboarding case",
                  await outcome(admin.create_client(actor=ada)), "refused 410")
            kept = Household(id=uuid.uuid4(), firm_id=firm.id, name="Kept")
            s.add(kept)
            await s.flush()
            owner = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=kept.id, full_name="Kept Client")
            s.add(owner)
            await s.flush()
            s.add(Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=owner.id, name="Kept mandate", mandate_type="advisory",
                          suitability={}, constraints={}))

            print("\n=== Model portfolios and connectors ===")
            check("an adviser cannot build a model", await outcome(guard(portfolios.AdminDep)(user=ann)), "refused 403")
            check("the portfolio team can", await outcome(guard(portfolios.AdminDep)(user=pete)), "allowed")
            mp = ModelPortfolio(id=uuid.uuid4(), firm_id=firm.id, name="Balanced", drift_band=0.05)
            s.add(mp)
            await s.flush()
            followed = (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id))).scalars().first()
            followed.model_portfolio_id = mp.id
            await s.flush()
            check("a model mandates follow is not deleted", await outcome(portfolios.delete_model_portfolio(mp.id, user=pete, firm=firm, db=s)),
                  "refused 409")
            check("an adviser cannot set up a connector", await outcome(guard(conduit.SetUp)(user=ann)), "refused 403")
            check("operations can", await outcome(guard(conduit.SetUp)(user=ola)), "allowed")
            check("the portfolio team may sync one", await outcome(guard(conduit.Syncs)(user=pete)), "allowed")

            print("\n=== A firm switched off ===")
            token = create_access_token(ann)
            await s.commit()
            check("before, its people are signed in", (await security.get_current_user(token=token, db=s)).id, ann.id)
            done = await superadmin.toggle_firm_status(str(firm.id), {"is_active": False}, actor=sam, db=s)
            again = await superadmin.toggle_firm_status(str(firm.id), {"is_active": False}, actor=sam, db=s)
            check("the switch is said, not toggled", (done["is_active"], again["is_active"]), (False, False))
            await s.commit()
            check("its people are signed out", await outcome(security.get_current_user(token=token, db=s)), "refused 401")
            check("and cannot sign in", await outcome(auth._authenticate(s, "ann@t.test", "pw")), "refused 401")
            check("the platform owner is not one of them", (await security.get_current_user(token=create_access_token(sam), db=s)).id, sam.id)
            check("a malformed firm id is not found", await outcome(superadmin.toggle_firm_status("not-a-uuid", {"is_active": True}, actor=sam, db=s)),
                  "refused 404")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:\n  " + "\n  ".join(FAIL))
        sys.exit(1)
