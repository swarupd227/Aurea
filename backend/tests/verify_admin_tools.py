"""The Admin and Connectors pages as conversation tools (B-96, second part).

    python -m tests.verify_admin_tools

Every write on the Admin page and the Connectors page is a tool behind a Confirm card, sharing one implementation
with its route (aurea_core/platform_admin.py). Against a throwaway SQLite database, this checks that:

  - each tool is held by the roles its route admits, and re-checks the role itself;
  - read_admin_settings reads a section by role, and never shows a token or a connector's settings;
  - every write's card names the record and each field's from and to, refuses a change that alters nothing, and
    refuses what the write would refuse, without writing; and every id must be the caller's firm's;
  - an invite's result carries no token; nobody grants the platform owner's role or changes their own;
  - an agent conduct surveillance paused is not resumed while its flag is open, and no tier goes over the licence,
    on the card and at Confirm;
  - a fee change names the households on the segment; an alert goes only to the firm's own people;
  - deleting research or a model cannot be undone and says so; a model mandates follow is not deleted;
  - a connector's settings never reach a card or a result; a model change waits for green eval gates;
  - each route still answers as it did.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import admin, conduit, portfolios
from app.aurea_core import platform_admin
from app.core.db import Base
from app.core.security import hash_password
from app.core.tools import OPEN_VIEWS, TOOLS, ToolChangeState, tools_for_role
from app.llm.embeddings import embedding_service
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.connectors import Connector
from app.models.enums import ConnectorDomain, ConnectorStatus, UserRole
from app.models.governance import AutonomyChange, SurveillanceFlag
from app.models.graph import Household, Mandate, Person
from app.models.identity import AuditEvent, User, UserInviteToken
from app.models.knowledge import ResearchDocument
from app.models.portfolio import ModelPortfolio, TargetAllocation
from app.models.tenant import AgentConfig, Firm, FirmSegment, NotificationConfig
from app.provenance import eval_gates

PASS, FAIL = [], []
R = UserRole

WRITES = {
    "update_firm_profile": {"admin"}, "set_agent_switches": {"admin"}, "invite_user": {"admin"}, "change_user": {"admin"},
    "save_segment": {"admin"}, "save_mandate_type": {"admin"}, "set_mandate_suitability": {"admin"},
    "set_notification": {"admin"}, "add_research": {"admin"}, "research_action": {"admin"}, "set_cmas": {"admin"},
    "set_model_routing": {"admin"},
    "save_model_portfolio": {"portfolio_team", "research_cio", "admin"}, "set_model_targets": {"portfolio_team", "research_cio", "admin"},
    "delete_model_portfolio": {"portfolio_team", "research_cio", "admin"},
    "add_connector": {"operations", "admin"}, "set_connector": {"operations", "admin"},
    "sync_connector": {"operations", "portfolio_team", "admin"},
}


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except ExecutorError as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:120]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def outcome(coro) -> str:
    try:
        await coro
        return "allowed"
    except HTTPException as exc:
        return f"refused {exc.status_code}"


def catalogue() -> None:
    print("\n=== Each tool is held by the roles its route admits ===")
    check("the read is held by admin, compliance, operations, the portfolio team and research",
          {r.value for r in TOOLS["read_admin_settings"].roles_required},
          {"admin", "compliance", "operations", "portfolio_team", "research_cio"})
    check("the read changes nothing", TOOLS["read_admin_settings"].change_state, ToolChangeState.NO)
    for key, roles in WRITES.items():
        check(f"{key} is held by {sorted(roles)}", {r.value for r in TOOLS[key].roles_required}, roles)
        check(f"{key} stops for a Confirm", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
    check("a client holds none of them", [k for k in [*WRITES, "read_admin_settings"] if k in tools_for_role(R.CLIENT)], [])
    check("compliance changes none of them", [k for k in WRITES if k in tools_for_role(R.COMPLIANCE)], [])
    check("no tool takes a key, a secret or a config", [k for k in [*WRITES, "read_admin_settings"]
                                                       if any(i.name in ("config", "api_key", "anthropic_api_key", "openai_api_key",
                                                                         "jurisdiction", "base_currency", "logo_url", "csv_data")
                                                              for i in TOOLS[k].inputs)], [])
    check("the admin view is the admin's, the connectors view operations'",
          (OPEN_VIEWS["admin"][:2], OPEN_VIEWS["admin"][2], OPEN_VIEWS["connectors"][:2], {r.value for r in OPEN_VIEWS["connectors"][2]}),
          (("/admin", "Firm & agents"), set(), ("/admin/connectors", "Connectors"), {"operations"}))


async def main() -> None:
    embedding_service._tried = True   # no embedding model in a test: chunks are stored without vectors
    ended: list[uuid.UUID] = []

    async def no_redis(user_id):
        ended.append(user_id)
    platform_admin.end_sessions = no_redis
    real_gates = eval_gates.run_gates
    eval_gates.run_gates = lambda: {"all_green": True, "passed": 1, "total": 1}

    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Tui", slug="tui", base_currency="NZD", jurisdiction="NZ", settings={},
                        branding={"primary": "#111111"}, llm_config={"anthropic_api_key": "sk-ant-secret-value"})
            licensed = Firm(id=uuid.uuid4(), name="Ganga", slug="ganga", base_currency="INR", jurisdiction="IN",
                            settings={"licences": ["investment_adviser"]})
            other = Firm(id=uuid.uuid4(), name="Other", slug="other", base_currency="NZD", jurisdiction="NZ", settings={})
            s.add_all([firm, licensed, other])
            await s.flush()
            people = {}
            for key, role, f in (("ada", R.ADMIN, firm), ("cora", R.COMPLIANCE, firm), ("ann", R.ADVISER, firm),
                                 ("ola", R.OPERATIONS, firm), ("pete", R.PORTFOLIO_TEAM, firm), ("rita", R.RESEARCH_CIO, firm),
                                 ("gita", R.ADMIN, licensed), ("zed", R.ADMIN, other)):
                people[key] = User(id=uuid.uuid4(), firm_id=f.id, email=f"{key}@t.test", full_name=key.title(),
                                   hashed_password=hash_password("pw"), role=role, is_active=True)
                s.add(people[key])
            await s.flush()
            ada, cora, ann, ola, pete, rita, gita, zed = (people[k] for k in ("ada", "cora", "ann", "ola", "pete", "rita", "gita", "zed"))
            s.add(UserInviteToken(firm_id=firm.id, user_id=ann.id, token="invite-token-secret-abc", token_type="invite",
                                  expires_at=__import__("app.core.db", fromlist=["utcnow"]).utcnow()))
            hh = Household(id=uuid.uuid4(), firm_id=firm.id, name="Chen Family", segment="private_wealth")
            hh2 = Household(id=uuid.uuid4(), firm_id=firm.id, name="Ng Family", segment="private_wealth")
            s.add_all([hh, hh2])
            await s.flush()
            person = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="Ann Chen")
            s.add(person)
            await s.flush()
            mandate = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=person.id, name="Chen Balanced", mandate_type="advisory",
                              suitability={"risk_tolerance": "balanced", "max_equity": 0.4, "capacity_for_loss": "low"},
                              constraints={}, is_active=True)
            seg = FirmSegment(id=uuid.uuid4(), firm_id=firm.id, slug="private_wealth", label="Private wealth", fee_tier_bps=100)
            conn = Connector(id=uuid.uuid4(), firm_id=firm.id, domain=ConnectorDomain.CUSTODY, provider_key="custody.generic",
                             display_name="Generic Custodian", status=ConnectorStatus.MOCK, use_mock=True,
                             config={"api_key": "custody-secret-key", "base_url": "https://custodian.example"})
            av = Connector(id=uuid.uuid4(), firm_id=firm.id, domain=ConnectorDomain.MARKET_RESEARCH_DATA,
                           provider_key="marketdata.alphavantage", display_name="Alpha Vantage", status=ConnectorStatus.MOCK,
                           use_mock=True, config={})
            theirs = Connector(id=uuid.uuid4(), firm_id=other.id, domain=ConnectorDomain.CUSTODY, provider_key="custody.generic",
                               display_name="Theirs", status=ConnectorStatus.MOCK, use_mock=True, config={})
            s.add_all([mandate, seg, conn, av, theirs])
            await s.flush()

            def ex(u):
                return ToolExecutors(s, u.id, u.role, u.firm_id)

            async def card(u, tool, inputs):
                preview = await ex(u).preview_change(tool, inputs)
                # Keyed as the fields are named, whatever the card shows them as ("Fixed income" is fixed_income).
                rows = {r["label"]: r["value"] for r in preview["rows"]}
                return {**rows, **{k.replace(" ", "_").lower(): v for k, v in rows.items()}}

            async def count(model, *where):
                return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()

            print("\n=== Reading the settings, by role ===")
            users = await ex(ada).execute("read_admin_settings", {"section": "users"})
            check("the read has exactly its shape", sorted(users), ["columns", "message", "rows", "section", "title"])
            check("each column says its kind", all(sorted(c) == ["key", "kind", "label"] for c in users["columns"]), True)
            check("each row carries its id", all("id" in r for r in users["rows"]), True)
            check("a user row holds no token", "invite-token-secret-abc" in json.dumps(users, default=str)
                  or any("token" in k for r in users["rows"] for k in r), False)
            for section in platform_admin.SECTIONS:
                got = await ex(cora).execute("read_admin_settings", {"section": section})
                check(f"compliance reads {section}", got["section"], section)
            firm_rows = await ex(ada).execute("read_admin_settings", {"section": "firm"})
            check("the firm section is a row per field", [c["key"] for c in firm_rows["columns"]], ["field", "value"])
            check("and shows no LLM key", "sk-ant-secret-value" in json.dumps(firm_rows, default=str), False)
            conns = await ex(ola).execute("read_admin_settings", {"section": "connectors"})
            check("operations reads connectors", len(conns["rows"]), 2)
            check("a connector row shows no setting", ("custody-secret-key" in json.dumps(conns, default=str),
                                                        "custodian.example" in json.dumps(conns, default=str)), (False, False))
            check("only provider, name, live, status, last sync and cron", sorted(k for k in conns["rows"][0] if k != "id"),
                  ["last_sync", "live", "name", "provider", "status", "sync_cron"])
            await refused("operations does not read users", ex(ola).execute("read_admin_settings", {"section": "users"}), "not this role's")
            check("the portfolio team reads models", (await ex(pete).execute("read_admin_settings", {"section": "model_portfolios"}))["section"],
                  "model_portfolios")
            check("research reads research", (await ex(rita).execute("read_admin_settings", {"section": "research"}))["section"], "research")
            await refused("research does not read the audit log", ex(rita).execute("read_admin_settings", {"section": "audit"}), "not this role's")
            await refused("an unknown section", ex(ada).execute("read_admin_settings", {"section": "secrets"}), "no settings section")
            seg_rows = await ex(ada).execute("read_admin_settings", {"section": "segments"})
            check("a segment row counts its households", seg_rows["rows"][0]["households"], 2)

            print("\n=== The firm's profile ===")
            shown = await card(ada, "update_firm_profile", {"name": "Tui Wealth", "primary_color": "#FFDD00"})
            check("names the record and each from and to", (shown["Record"], shown["name"], shown["primary_color"]),
                  ("Tui", "Tui → Tui Wealth", "#111111 → #FFDD00"))
            check("showing it changes nothing", firm.name, "Tui")
            await refused("a change that alters nothing", ex(ada).preview_change("update_firm_profile", {"name": "Tui"}), "already so")
            await refused("a colour that is not one", ex(ada).preview_change("update_firm_profile", {"primary_color": "yellow"}), "hex")
            await refused("compliance does not change it", ex(cora).preview_change("update_firm_profile", {"name": "X"}), "administrator")
            done = await ex(ada).execute("update_firm_profile", {"name": "Tui Wealth"})
            check("the result has exactly its shape", sorted(done), ["changes", "message", "record", "section"])
            check("and names what moved", (done["record"]["label"], done["changes"]), ("Tui", [{"field": "name", "from": "Tui", "to": "Tui Wealth"}]))
            check("the jurisdiction stays as it is", (firm.name, firm.jurisdiction, firm.base_currency), ("Tui Wealth", "NZ", "NZD"))

            print("\n=== An agent's switches ===")
            agent = {"agent_key": "drift_rebalancing", "tier": "tier_2", "reason": "Ready for proposals"}
            shown = await card(ada, "set_agent_switches", agent)
            check("names the agent and its tier from and to", ("Drift" in shown["Record"], shown["tier"], shown["Reason"]),
                  (True, "tier_1 → tier_2", "Ready for proposals"))
            await refused("a change needs its reason", ex(ada).preview_change("set_agent_switches", {**agent, "reason": ""}), "say why")
            await refused("a tier over the licence, on the card", ex(gita).preview_change("set_agent_switches", {**agent, "tier": "tier_3"}), "licensed")
            await refused("and at Confirm", ex(gita).execute("set_agent_switches", {**agent, "tier": "tier_3"}), "licensed")
            await refused("a cron that is not one", ex(ada).preview_change("set_agent_switches", {**agent, "schedule_cron": "every tuesday"}), "cron")
            await refused("an unknown agent", ex(ada).preview_change("set_agent_switches", {**agent, "agent_key": "nobody"}), "no agent")
            await ex(ada).execute("set_agent_switches", agent)
            moved = (await s.execute(select(AutonomyChange).where(AutonomyChange.firm_id == firm.id))).scalars().all()
            check("the change is an AutonomyChange made by a person", [(m.to_tier, m.automatic, "Ready for proposals" in m.reason) for m in moved],
                  [("tier_2", False, True)])
            await refused("the same tier again is already so", ex(ada).preview_change("set_agent_switches", agent), "already so")
            cfg = (await s.execute(select(AgentConfig).where(AgentConfig.firm_id == firm.id))).scalar_one()
            cfg.paused, cfg.paused_reason = True, "Auto-paused by conduct surveillance"
            flag = SurveillanceFlag(firm_id=firm.id, target_agent_key="drift_rebalancing", severity="high", category="suitability",
                                    finding="x", auto_paused_agent=True)
            s.add(flag)
            await s.flush()
            resume = {"agent_key": "drift_rebalancing", "paused": "false", "reason": "Looks fine"}
            await refused("no resuming while its flag is open, on the card", ex(ada).preview_change("set_agent_switches", resume), "flag is still open")
            await refused("and at Confirm", ex(ada).execute("set_agent_switches", resume), "flag is still open")
            check("it stays paused", cfg.paused, True)
            flag.resolved = True
            await s.flush()
            check("once resolved the card offers it", (await card(ada, "set_agent_switches", resume))["paused"], "Yes → No")
            await refused("compliance does not set it", ex(cora).preview_change("set_agent_switches", resume), "administrator")

            print("\n=== Inviting and changing people ===")
            invite = {"email": "New@T.test", "full_name": "New Person", "role": "paraplanner"}
            shown = await card(ada, "invite_user", invite)
            check("names the person and the role", (shown["Record"], shown["email"], shown["role"]), ("New Person", "— → new@t.test", "— → paraplanner"))
            await refused("the platform owner's role", ex(ada).preview_change("invite_user", {**invite, "role": "superadmin"}), "platform owner")
            await refused("an email already in use", ex(ada).preview_change("invite_user", {**invite, "email": "ann@t.test"}), "already exists")
            tokens_before = await count(UserInviteToken)
            done = await ex(ada).execute("invite_user", invite)
            tok = (await s.execute(select(UserInviteToken).order_by(UserInviteToken.created_at.desc()))).scalars().first()
            check("an invite is made", await count(UserInviteToken), tokens_before + 1)
            check("its result carries no token or link", (tok.token in json.dumps(done, default=str), "accept-invite" in json.dumps(done, default=str),
                                                          "token" in json.dumps(done).lower()), (False, False, False))
            check("and says where the link is", "Users page" in done["message"], True)
            new = (await s.execute(select(User).where(User.email == "new@t.test"))).scalar_one()
            check("the new person cannot sign in yet", new.is_active, False)

            shown = await card(ada, "change_user", {"user_id": str(ann.id), "role": "paraplanner", "reason": "Moved teams"})
            check("a role change names the person, from and to", (shown["Record"], shown["role"]), ("Ann · ann@t.test", "adviser → paraplanner"))
            await refused("nobody is made the platform owner", ex(ada).preview_change("change_user", {"user_id": str(ann.id), "role": "superadmin"}), "platform owner")
            await refused("nobody changes their own role", ex(ada).preview_change("change_user", {"user_id": str(ada.id), "role": "adviser"}), "your own role")
            await refused("or switches themselves off", ex(ada).execute("change_user", {"user_id": str(ada.id), "active": "false"}), "deactivate your own")
            await refused("another firm's person", ex(ada).preview_change("change_user", {"user_id": str(zed.id), "title": "X"}), "not found or access denied")
            await refused("the same role is already so", ex(ada).preview_change("change_user", {"user_id": str(ann.id), "role": "adviser"}), "already so")
            shown = await card(ada, "change_user", {"user_id": str(ola.id), "active": "false"})
            check("switching someone off says their sessions end", shown.get("Sessions"), "Ended at once")
            await ex(ada).execute("change_user", {"user_id": str(ola.id), "active": "false", "reason": "Left the firm"})
            check("and they do", (ola.is_active, ended), (False, [ola.id]))
            ola.is_active = True
            await ex(ada).execute("change_user", {"user_id": str(ann.id), "role": "paraplanner", "reason": "Moved teams"})
            logged = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "user.role_changed"))).scalars().all()
            check("a role change is on the audit log with its reason", [(e.subject, e.detail) for e in logged],
                  [("ann@t.test", {"from": "adviser", "to": "paraplanner", "reason": "Moved teams"})])
            ann.role = R.ADVISER

            print("\n=== Segments, mandate types and mandates ===")
            fee = {"segment_id": str(seg.id), "fee_tier_bps": 85, "reason": "Repriced"}
            shown = await card(ada, "save_segment", fee)
            check("a fee change names the rate and the households on it", (shown["Record"], shown["fee_tier_bps"], shown["Households on this segment"]),
                  ("Private wealth", "100 bps → 85 bps", "2"))
            await refused("a segment change needs its reason", ex(ada).preview_change("save_segment", {**fee, "reason": ""}), "say why")
            await refused("the same rate is already so", ex(ada).preview_change("save_segment", {**fee, "fee_tier_bps": 100}), "already so")
            await refused("a slug does not change", ex(ada).preview_change("save_segment", {**fee, "slug": "pw"}), "slug does not change")
            await refused("a new one needs its slug and label", ex(ada).preview_change("save_segment", {"label": "X", "reason": "r"}), "slug and its label")
            await refused("a slug already used", ex(ada).preview_change("save_segment", {"slug": "private_wealth", "label": "X", "reason": "r"}), "already exists")
            await ex(ada).execute("save_segment", fee)
            check("the rate is changed", seg.fee_tier_bps, 85)
            made = await ex(ada).execute("save_segment", {"slug": "family_office", "label": "Family office", "fee_tier_bps": 60, "reason": "New desk"})
            check("a new segment is made", (made["record"]["label"], await count(FirmSegment, FirmSegment.firm_id == firm.id)), ("Family office", 2))

            mt = {"slug": "discretionary_plus", "label": "D+", "default_autonomy_tier": "tier_3", "reason": "New offer"}
            await refused("a mandate type's tier is held to the licence", ex(gita).preview_change("save_mandate_type", mt), "licensed")
            shown = await card(ada, "save_mandate_type", mt)
            check("a new type names its tier", shown["default_autonomy_tier"], "— → tier_3")

            suit = {"mandate_id": str(mandate.id), "risk_tolerance": "growth", "reason": "Client review"}
            shown = await card(ada, "set_mandate_suitability", suit)
            check("names the client and the mandate, from and to", (shown["Record"], shown["risk_tolerance"]),
                  ("Chen Family · Chen Balanced", "balanced → growth"))
            await refused("max equity over the capacity ceiling", ex(ada).preview_change("set_mandate_suitability", {**suit, "max_equity": 0.7}),
                          "above the 40% ceiling")
            await refused("an unknown tolerance", ex(ada).preview_change("set_mandate_suitability", {**suit, "risk_tolerance": "reckless"}), "risk tolerance")
            await refused("a suitability change needs its reason", ex(ada).preview_change("set_mandate_suitability", {**suit, "reason": " "}), "say why")
            await refused("another firm's mandate", ex(zed).preview_change("set_mandate_suitability", suit), "not found or access denied")
            await ex(ada).execute("set_mandate_suitability", suit)
            check("the suitability is changed and the rest kept", mandate.suitability,
                  {"risk_tolerance": "growth", "max_equity": 0.4, "capacity_for_loss": "low"})

            print("\n=== Alerts ===")
            alert = {"event_type": "agent_paused", "enabled": "true", "recipients": ["ada@t.test", "compliance"]}
            shown = await card(ada, "set_notification", alert)
            check("names the alert and who gets it", (shown["Record"], shown["recipients"]), ("Agent paused · email", "— → ada@t.test, compliance"))
            await refused("an outside email", ex(ada).preview_change("set_notification", {**alert, "recipients": ["me@gmail.com"]}),
                          "only to this firm's people")
            await refused("another firm's person", ex(ada).preview_change("set_notification", {**alert, "recipients": ["zed@t.test"]}),
                          "only to this firm's people")
            await refused("an unknown alert", ex(ada).preview_change("set_notification", {**alert, "event_type": "lunch"}), "no alert")
            await ex(ada).execute("set_notification", alert)
            await refused("the same again is already so", ex(ada).preview_change("set_notification", alert), "already so")
            check("the route refuses an outside email too", await outcome(admin.update_notifications(
                [admin.NotifUpdate(event_type="daily_digest", channel="email", enabled=True, recipients=["x@evil.example"])],
                actor=ada, firm=firm, db=s)), "refused 422")

            print("\n=== Research ===")
            doc_in = {"title": "House view Q4", "body": "We prefer quality equity and short duration."}
            shown = await card(ada, "add_research", doc_in)
            check("a new document starts as a draft", shown["status"], "— → draft")
            added = await ex(ada).execute("add_research", doc_in)
            doc = await s.get(ResearchDocument, uuid.UUID(added["record"]["id"]))
            check("it is a draft", doc.status, "draft")
            from app.aurea_core import knowledge
            check("and no agent grounds on a draft", await knowledge.retrieve(s, firm.id, "quality equity duration"), [])
            check("submitting names the status from and to", (await card(ada, "research_action", {"doc_id": str(doc.id), "action": "submit"}))["status"],
                  "draft → under_review")
            await refused("rejecting a draft is already so", ex(ada).preview_change("research_action", {"doc_id": str(doc.id), "action": "reject"}), "already so")
            shown = await card(ada, "research_action", {"doc_id": str(doc.id), "action": "delete"})
            check("deleting says it cannot be undone", "Cannot be undone" in shown, True)
            await refused("another firm's document", ex(zed).preview_change("research_action", {"doc_id": str(doc.id), "action": "delete"}), "not found")
            await ex(ada).execute("research_action", {"doc_id": str(doc.id), "action": "publish"})
            check("once published it is the firm's view", [c["title"] for c in await knowledge.retrieve(s, firm.id, "quality equity duration")],
                  ["House view Q4"])
            await refused("publishing a published one is already so", ex(ada).preview_change("research_action", {"doc_id": str(doc.id), "action": "publish"}),
                          "already so")
            await ex(ada).execute("research_action", {"doc_id": str(doc.id), "action": "delete"})
            check("and it is gone", await count(ResearchDocument, ResearchDocument.firm_id == firm.id), 0)

            print("\n=== Model portfolios ===")
            mp = ModelPortfolio(id=uuid.uuid4(), firm_id=firm.id, name="Balanced", drift_band=0.05)
            s.add(mp)
            await s.flush()
            s.add_all([TargetAllocation(firm_id=firm.id, model_id=mp.id, asset_class="equity", target_weight=0.4),
                       TargetAllocation(firm_id=firm.id, model_id=mp.id, asset_class="fixed_income", target_weight=0.6)])
            mandate.model_portfolio_id = mp.id
            await s.flush()
            targets = {"model_id": str(mp.id), "targets": [{"asset_class": "equity", "target_weight": 0.6},
                                                           {"asset_class": "fixed_income", "target_weight": 0.4}]}
            await refused("raising equity past a following mandate's capacity is refused, as a risk profile is",
                          ex(pete).preview_change("set_model_targets", targets), "capacity")
            lower = {**targets, "targets": [{"asset_class": "equity", "target_weight": 0.3},
                                            {"asset_class": "fixed_income", "target_weight": 0.7}]}
            shown = await card(pete, "set_model_targets", lower)
            check("each class before and after, and who follows it",
                  (shown["equity"], shown["fixed_income"], shown["Mandates following"]),
                  ("40.0% → 30.0%", "60.0% → 70.0%", "1"))
            await refused("weights that do not sum to one", ex(pete).preview_change("set_model_targets", {**targets, "targets": [{"asset_class": "equity", "target_weight": 0.5}]}),
                          "sum to 1.0")
            await refused("a class that is not one", ex(pete).preview_change("set_model_targets", {**targets, "targets": [{"asset_class": "gold", "target_weight": 1}]}),
                          "not an asset class")
            await refused("the same targets are already so", ex(pete).preview_change("set_model_targets", {**targets, "targets": [
                {"asset_class": "equity", "target_weight": 0.4}, {"asset_class": "fixed_income", "target_weight": 0.6}]}), "already so")
            await refused("an adviser does not set them", ex(ann).preview_change("set_model_targets", targets), "portfolio team")
            shown = await card(rita, "save_model_portfolio", {"model_id": str(mp.id), "benchmark_symbol": "ACWI"})
            check("a benchmark change is named and its disclosure", (shown["benchmark_symbol"], shown["Composites"]),
                  ("— → ACWI", "Disclose the benchmark change"))
            await refused("a model mandates follow is not deleted, on the card", ex(pete).preview_change("delete_model_portfolio", {"model_id": str(mp.id)}),
                          "1 mandate(s) follow")
            await refused("nor at Confirm", ex(pete).execute("delete_model_portfolio", {"model_id": str(mp.id)}), "follow this model")
            mandate.model_portfolio_id = None
            await s.flush()
            shown = await card(pete, "delete_model_portfolio", {"model_id": str(mp.id)})
            check("deleting says it cannot be undone", "Cannot be undone" in shown, True)
            await ex(pete).execute("delete_model_portfolio", {"model_id": str(mp.id)})
            check("and it is gone", await s.get(ModelPortfolio, mp.id), None)

            print("\n=== Connectors ===")
            yahoo = Connector(id=uuid.uuid4(), firm_id=firm.id, domain=ConnectorDomain.MARKET_RESEARCH_DATA, provider_key="marketdata.yahoo",
                              display_name="Yahoo", status=ConnectorStatus.MOCK, use_mock=True, config={})
            s.add(yahoo)
            await s.flush()
            shown = await card(ola, "set_connector", {"connector_id": str(yahoo.id), "live": "true"})
            check("going live is named", ("syncs reach the provider" in shown["Real feed"], shown["live"]), (True, "No → Yes"))
            await refused("a provider with no real feed stays a mock", ex(ola).preview_change("set_connector", {"connector_id": str(conn.id), "live": "true"}),
                          "no real feed")
            shown = await card(ola, "set_connector", {"connector_id": str(conn.id), "sync_cron": "0 5 * * *"})
            check("the card shows no setting", ("custody-secret-key" in json.dumps(shown), "custodian.example" in json.dumps(shown)), (False, False))
            await refused("a live feed needs its key on file", ex(ola).preview_change("set_connector", {"connector_id": str(av.id), "live": "true"}),
                          "API key is not set")
            await refused("the same state is already so", ex(ola).preview_change("set_connector", {"connector_id": str(conn.id), "live": "false"}), "already so")
            await refused("another firm's connector", ex(ola).preview_change("set_connector", {"connector_id": str(theirs.id), "enabled": "false"}),
                          "not found or access denied")
            await refused("an adviser does not set one", ex(ann).preview_change("set_connector", {"connector_id": str(conn.id), "enabled": "false"}),
                          "operations")
            done = await ex(ola).execute("set_connector", {"connector_id": str(conn.id), "enabled": "false", "sync_cron": "0 6 * * *"})
            check("the result shows no setting", ("custody-secret-key" in json.dumps(done, default=str), "config" in json.dumps(done, default=str)),
                  (False, False))
            check("and is switched off with its cadence", (str(conn.status), conn.sync_cron), ("disabled", "0 6 * * *"))
            await refused("a switched-off connector is not synced", ex(ola).preview_change("sync_connector", {"connector_id": str(conn.id)}), "switched off")
            added = await ex(ola).execute("add_connector", {"provider_key": "pa.generic"})
            new_conn = await s.get(Connector, uuid.UUID(added["record"]["id"]))
            check("a new connector starts as a mock", (new_conn.use_mock, str(new_conn.status)), (True, "mock"))
            await refused("a custom one is set up on the page", ex(ola).preview_change("add_connector", {"provider_key": "custom.webhook"}), "Connectors page")
            shown = await card(pete, "sync_connector", {"connector_id": str(new_conn.id)})
            check("a sync names its mode", shown["Mode"], "Mock")
            synced = await ex(pete).execute("sync_connector", {"connector_id": str(new_conn.id)})
            check("the portfolio team may sync one", synced["section"], "connectors")

            print("\n=== Capital market assumptions and model routing ===")
            shown = await card(ada, "set_cmas", {"asset_class": "equity", "expected_return": 0.08, "reason": "House view"})
            check("names the class, from and to", (shown["Record"], shown["expected_return"]), ("Capital market assumptions · equity", "7.5% → 8.0%"))
            await refused("a class planning does not know", ex(ada).preview_change("set_cmas", {"asset_class": "crypto", "expected_return": 0.1, "reason": "r"}),
                          "no planning asset class")
            await refused("a percentage given as a whole number", ex(ada).preview_change("set_cmas", {"asset_class": "equity", "expected_return": 8, "reason": "r"}),
                          "fraction")
            await ex(ada).execute("set_cmas", {"asset_class": "equity", "expected_return": 0.08, "reason": "House view"})
            check("the override is kept", firm.settings["cmas"]["equity"], [0.08, 0.16])
            route = {"task": "advice", "model": "claude-sonnet-4-6", "reason": "Cheaper"}
            check("routing names the task, from and to", (await card(ada, "set_model_routing", route))["advice"].endswith("→ claude-sonnet-4-6"), True)
            eval_gates.run_gates = lambda: {"all_green": False, "passed": 0, "total": 1}
            await refused("a model change while the enforced gates are red, on the card", ex(ada).preview_change("set_model_routing", route), "eval gates")
            await refused("and at Confirm", ex(ada).execute("set_model_routing", route), "eval gates")
            check("the route refuses it too", await outcome(admin.update_firm(admin.FirmUpdate(model_config_json={"advice": "x"}), user=ada, firm=firm, db=s)),
                  "refused 409")
            eval_gates.run_gates = lambda: {"all_green": True, "passed": 1, "total": 1}
            await ex(ada).execute("set_model_routing", route)
            check("with green gates it is set", firm.model_config_json.get("advice"), "claude-sonnet-4-6")
            await refused("an unknown task", ex(ada).preview_change("set_model_routing", {**route, "task": "poetry"}), "no task")

            print("\n=== Opening the pages ===")
            check("the admin view opens on a tab", (await ex(ada).execute("open_view", {"view": "admin", "tab": "users"}))["view"], "/admin?tab=users")
            await refused("an unknown tab", ex(ada).execute("open_view", {"view": "admin", "tab": "secrets"}), "no 'secrets' tab")
            await refused("compliance does not open the admin view", ex(cora).execute("open_view", {"view": "admin"}), "not one this role opens")
            check("operations opens connectors", (await ex(ola).execute("open_view", {"view": "connectors"}))["view"], "/admin/connectors")

            print("\n=== Each route still answers as it did ===")
            check("PATCH firm", await admin.update_firm(admin.FirmUpdate(regulator="FMA NZ"), user=ada, firm=firm, db=s), {"ok": True})
            check("PATCH branding", (await admin.update_branding(admin.BrandingIn(tagline="Hi"), user=ada, firm=firm, db=s))["branding"]["tagline"], "Hi")
            check("PATCH agents", await admin.update_agent_config(__import__("app.models.enums", fromlist=["AgentKey"]).AgentKey.DRIFT_REBALANCING,
                                                                  admin.AgentConfigUpdate(enabled=True), user=ada, firm=firm, db=s), {"ok": True})
            made = await admin.create_user(admin.UserCreate(email="r@t.test", full_name="R"), actor=ada, firm=firm, db=s)
            check("POST users still gives the admin the invite", (bool(made["invite_token"]), "accept-invite" in made["invite_url"]), (True, True))
            check("PATCH users", (await admin.update_user(new.id, admin.UserUpdate(title="Planner"), actor=ada, firm=firm, db=s))["title"], "Planner")
            check("GET users", sorted((await admin.list_users(actor=ada, firm=firm, db=s))[0]), ["created_at", "email", "full_name", "id", "is_active", "role", "title"])
            seg_out = await admin.create_segment(admin.SegmentCreate(slug="core", label="Core", fee_tier_bps=90), actor=ada, firm=firm, db=s)
            check("POST segments", (seg_out["slug"], seg_out["fee_tier_bps"]), ("core", 90))
            check("PATCH mandates", (await admin.update_mandate(mandate.id, admin.MandateUpdate(investment_horizon="long"), actor=ada, firm=firm, db=s))
                  ["suitability"]["investment_horizon"], "long")
            check("PATCH mandates over the ceiling", await outcome(admin.update_mandate(mandate.id, admin.MandateUpdate(max_equity=0.9), actor=ada, firm=firm, db=s)),
                  "refused 422")
            check("PUT cmas", (await admin.set_cmas(admin.CmaIn(cmas={"equity": [0.07, 0.15], "bogus": [1, 1]}), user=ada, firm=firm, db=s)),
                  {"cmas": {"equity": [0.07, 0.15]}})
            check("PATCH notifications", await admin.update_notifications(
                [admin.NotifUpdate(event_type="daily_digest", channel="email", enabled=True, recipients=["admin"])], actor=ada, firm=firm, db=s), {"ok": True})
            doc_out = await admin.add_research(admin.ResearchIn(title="Note", body="Some text"), user=ada, firm=firm, db=s)
            check("POST research", sorted(doc_out), ["chunks", "id"])
            check("POST research submit", await admin.submit_research(uuid.UUID(doc_out["id"]), user=ada, firm=firm, db=s), {"ok": True, "status": "under_review"})
            check("DELETE research of another firm", await outcome(admin.delete_research(uuid.UUID(doc_out["id"]), actor=zed, firm=other, db=s)), "refused 404")
            mp2 = await portfolios.create_model_portfolio(portfolios.ModelPortfolioIn(name="Growth"), user=pete, firm=firm, db=s)
            check("POST model portfolio", (mp2["name"], mp2["drift_band"]), ("Growth", 0.05))
            check("PUT targets", await portfolios.set_targets(uuid.UUID(mp2["id"]), [portfolios.TargetWeightIn(asset_class="equity", target_weight=1.0)],
                                                              user=pete, firm=firm, db=s), {"ok": True, "targets": 1, "total_weight": 1.0})
            check("PUT targets that do not sum", await outcome(portfolios.set_targets(uuid.UUID(mp2["id"]), [portfolios.TargetWeightIn(asset_class="equity", target_weight=0.5)],
                                                                                    user=pete, firm=firm, db=s)), "refused 400")
            c_out = await conduit.create_connector(conduit.ConnectorCreate(provider_key="custody.fnz"), user=ola, firm=firm, db=s)
            check("POST connector", (c_out["use_mock"], c_out["status"]), (True, "mock"))
            patched = await conduit.update_connector(conn.id, conduit.ConnectorUpdate(config={"api_key": "new-secret"}), user=ola, firm=firm, db=s)
            check("PATCH connector redacts its secret", patched["config"]["api_key"], conduit.REDACTED)
            check("PATCH connector of another firm", await outcome(conduit.update_connector(theirs.id, conduit.ConnectorUpdate(enabled=False), user=ola, firm=firm, db=s)),
                  "refused 404")
            check("GET schedules", (await admin.get_schedules(user=ada, firm=firm, db=s))[0]["agent_key"], "market_data")
            check("PATCH schedules", (await admin.update_schedule("drift_rebalancing", admin.ScheduleUpdate(cron="0 7 * * 1-5"), actor=ada, firm=firm, db=s))
                  ["cron"], "0 7 * * 1-5")
            check("PATCH schedules for prices", await outcome(admin.update_schedule("market_data", admin.ScheduleUpdate(enabled=True), actor=ada, firm=firm, db=s)),
                  "refused 409")
            check("GET cmas", sorted(await admin.get_cmas(user=ada, firm=firm)), ["defaults", "effective", "overrides"])
            check("GET agents", sorted((await admin.list_agent_configs(user=ada, firm=firm, db=s))[0]),
                  ["agent_key", "config", "default_tier", "enabled", "paused", "paused_reason", "schedule_cron", "schedule_enabled"])
            check("GET research", sorted((await admin.list_research(user=ada, firm=firm, db=s))[0]),
                  sorted(["created_at", "doc_type", "id", "published_at", "published_by", "status", "summary", "tags", "title", "author", "version"]))
            check("POST research publish", (await admin.publish_research(uuid.UUID(doc_out["id"]), user=ada, firm=firm, db=s))["status"], "published")
            check("POST research reject", await admin.reject_research(uuid.UUID(doc_out["id"]), user=ada, firm=firm, db=s), {"ok": True, "status": "draft"})
            check("PATCH research", (await admin.update_research(uuid.UUID(doc_out["id"]), admin.ResearchUpdate(title="Note 2"), actor=ada, firm=firm, db=s))["ok"], True)
            check("DELETE research", await admin.delete_research(uuid.UUID(doc_out["id"]), actor=ada, firm=firm, db=s), {"ok": True})
            check("GET audit", bool(await admin.audit_log(limit=5, event_type=None, actor=ada, firm=firm, db=s)), True)
            check("GET mandates", (await admin.list_mandates(actor=ada, firm=firm, db=s))[0]["client_name"], "Ann Chen")
            check("GET mandate types", await admin.list_mandate_types(actor=ada, firm=firm, db=s), [])
            mt_out = await admin.create_mandate_type(admin.MandateTypeCreate(slug="adv", label="Advisory"), actor=ada, firm=firm, db=s)
            check("POST and PATCH mandate types", (await admin.update_mandate_type(uuid.UUID(mt_out["id"]), admin.MandateTypeUpdate(label="Advice"),
                                                                                actor=ada, firm=firm, db=s))["label"], "Advice")
            check("GET notifications", {n["event_type"] for n in await admin.get_notifications(actor=ada, firm=firm, db=s)}, {"agent_paused", "daily_digest"})
            check("DELETE segment", await admin.delete_segment(uuid.UUID(seg_out["id"]), actor=ada, firm=firm, db=s), {"ok": True})
            check("DELETE user", (await admin.delete_user(new.id, actor=ada, firm=firm, db=s), new.is_active), ({"ok": True}, False))
            check("DELETE own user", await outcome(admin.delete_user(ada.id, actor=ada, firm=firm, db=s)), "refused 400")
            check("GET assignments", await admin.list_assignments(actor=ada, firm=firm, db=s), [])
            check("GET model portfolios", [m["name"] for m in await portfolios.list_model_portfolios(firm=firm, db=s)], ["Growth"])
            check("PATCH model portfolio", await portfolios.update_model_portfolio(uuid.UUID(mp2["id"]), portfolios.ModelPortfolioIn(name="Growth+", benchmark_symbol="SPY"),
                                                                                user=pete, firm=firm, db=s), {"ok": True})
            check("DELETE model portfolio", await portfolios.delete_model_portfolio(uuid.UUID(mp2["id"]), user=pete, firm=firm, db=s), {"ok": True})
            check("GET connectors redacts", [c["config"].get("api_key") for c in await conduit.connectors(firm=firm, db=s) if c["provider_key"] == "custody.generic"],
                  [conduit.REDACTED])
            check("POST sync", sorted(await conduit.sync_connector(new_conn.id, user=ola, firm=firm, db=s)), ["connector", "detail", "records_ingested", "status"])
    finally:
        eval_gates.run_gates = real_gates
        await db.dispose()


if __name__ == "__main__":
    catalogue()
    asyncio.run(main())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:\n  " + "\n  ".join(FAIL))
        sys.exit(1)
