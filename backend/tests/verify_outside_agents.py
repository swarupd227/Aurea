"""Outside agents call Astra's tools through MCP, as a named person (B-166).

    python -m tests.verify_outside_agents

Against a throwaway SQLite database, over HTTP to the real routes, with the Foundation's own MCP client
(foundation/adapters/mcp.MCPClient) as the outside agent. Checks that:

  - outside agents are off until the administrator switches them on, with a reason; off, no key is made and a key
    already made is refused; compliance does not hold the switch;
  - a key is made by staff only, named, lasting 1 to 90 days, shown once and kept only as a hash;
  - the agent speaks MCP: initialize, initialized, tools/list (the person's tools, less open_view, with
    confirmation_status), tools/call; no batch, no stream, no foreign Origin, no person's sign-in;
  - a read answers as the person, in their firm; a tool their role does not hold is refused and counted;
  - a change waits on a Confirm card for that person on the key's own conversation, changes nothing until they
    confirm it in Astra, is carried out once, and the agent reads the outcome; the agent cannot confirm with its key;
  - revoking (the person or the administrator, with a reason) stops the key at once and declines what it waits on;
    an expired key, or a person deactivated, is refused;
  - every call is on the audit log with the tool and whether it worked, never its inputs;
  - the conversation reads the keys and revokes one behind a Confirm card; a client holds neither tool.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.api import outside_agents as oa_api
from app.api import threads as threads_api
from app.aurea_core import outside_agents as oa
from app.aurea_core import rulebook
from app.core.db import Base, get_db, utcnow
from app.core.security import create_access_token
from app.core.tools import tools_for_role
from app.foundation.adapters.mcp import MCPClient, MCPError
from app.llm.executors import ExecutorError, ToolExecutors
from app.llm.gateway import Gateway, GatewayError
from app.models.ai_governance import EntitlementViolation as AiViolation
from app.models.crm import CrmContact
from app.models.enums import UserRole
from app.models.identity import AuditEvent
from app.models.outside_agent import OutsideAgentAccess
from app.models.thread import PendingAction, PendingActionStatus, Thread
from tests import _sim_fixture

PASS, FAIL = [], []
URL = "http://astra.test/api/mcp"


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (oa.OutsideAgentError, ExecutorError, GatewayError, MCPError, rulebook.RulebookError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:120]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def refused_now(label: str, fn, *contains: str) -> None:
    try:
        fn()
    except (oa.OutsideAgentError, rulebook.RulebookError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:120]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)

        api = FastAPI()
        api.include_router(oa_api.router)
        api.include_router(threads_api.router)

        async def own_db():
            async with Session() as s:
                yield s
        api.dependency_overrides[get_db] = own_db
        threads_api.SessionLocal = Session         # a confirmation settles on its own session

        def agent(token: str | None, **headers) -> MCPClient:
            h = dict(headers)
            if token:
                h["Authorization"] = f"Bearer {token}"
            return MCPClient(URL, headers=h, transport=httpx.ASGITransport(app=api))

        def http() -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://astra.test")

        async with Session() as s:
            fx = await _sim_fixture.build(s)
            firm, sophie, ops, admin = fx.firm, fx.adviser, fx.operations, fx.admin
            client = sophie.__class__(id=uuid.uuid4(), firm_id=firm.id, email="client@x", full_name="Wei Chen",
                                      role=UserRole.CLIENT, hashed_password="x", is_active=True)
            s.add(client)
            await s.commit()
            firm_id, sophie_id = firm.id, sophie.id

        print("\nOff until the administrator switches it on")
        async with Session() as s:
            firm = await s.get(type(fx.firm), firm_id)
            sophie = await s.get(type(fx.adviser), sophie_id)
            check("off by default", oa.switched_on(firm), False)
            await refused("no key while off", oa.issue(s, firm, sophie, label="My agent"), "off", "administrator")
            refused_now("compliance does not hold the switch", lambda: rulebook.prepare_setting(
                firm, role=UserRole.COMPLIANCE, key="outside_agents", value=True, reason="x"), "administrator")
            refused_now("the switch needs a reason", lambda: rulebook.prepare_setting(
                firm, role=UserRole.ADMIN, key="outside_agents", value=True, reason=""), "reason")
            await rulebook.set_setting(s, firm, actor=await s.get(type(admin), admin.id), key="outside_agents",
                                       value=True, reason="Pilot of the planning agent with two advisers")
            await s.commit()
            check("on after the administrator's change", oa.switched_on(firm), True)

        print("\nA key is a person's, named, shown once")
        async with Session() as s:
            firm = await s.get(type(fx.firm), firm_id)
            sophie = await s.get(type(fx.adviser), sophie_id)
            await refused("a client makes none", oa.issue(s, firm, await s.get(type(client), client.id), label="x"),
                          "staff")
            await refused("it is named", oa.issue(s, firm, sophie, label="  "), "name")
            await refused("at most 90 days", oa.issue(s, firm, sophie, label="x", days=365), "90")
            row, token = await oa.issue(s, firm, sophie, label="Planning agent", days=30)
            other_row, other_token = await oa.issue(s, firm, await s.get(type(ops), ops.id), label="Ops bot", days=7)
            await s.commit()
            key_id, other_id = row.id, other_row.id
            check("key starts with its prefix", token.startswith(oa.PREFIX), True)
            check("only its hash is kept", row.token_hash != token and token not in str(oa.out(row)), True)
            check("its last four shown", row.token_hint, token[-4:])
            mine = await oa.listing(s, firm, sophie)
            check("the person lists their own keys only", [k["label"] for k in mine], ["Planning agent"])
            check("the administrator lists the firm's, with whose",
                  sorted((k["label"], k["owner"]) for k in await oa.listing(s, firm, await s.get(type(admin), admin.id))),
                  [("Ops bot", "Grace Lim"), ("Planning agent", "Sophie Tran")])

        print("\nThe page's routes")
        async with http() as h:
            jwt = create_access_token(await _user(Session, sophie_id))
            r = await h.get("/api/outside-agents", headers={"Authorization": f"Bearer {jwt}"})
            check("the page lists the key without its value", (r.status_code, "key" in r.json()["keys"][0]),
                  (200, False))
            r = await h.post("/api/outside-agents", json={"label": "Second agent", "days": 5},
                             headers={"Authorization": f"Bearer {jwt}"})
            check("a key made on the page is shown once", (r.status_code, r.json()["key"].startswith(oa.PREFIX)),
                  (200, True))

        print("\nThe agent speaks MCP")
        async with agent(token) as mcp:
            check("protocol negotiated", mcp.protocol, "2025-06-18")
            check("server names itself Astra", mcp.server.get("name"), "Astra for Wealth")
            tools = await mcp.list_tools()
            names = {t["name"] for t in tools}
            adviser_tools = set(tools_for_role(UserRole.ADVISER)) - oa.NOT_OFFERED
            check("the person's tools, less open_view, with confirmation_status",
                  names, adviser_tools | {oa.STATUS_TOOL})
            by = {t["name"]: t for t in tools}
            check("a read is marked read-only", by["search_households"]["annotations"]["readOnlyHint"], True)
            check("a change says it waits on the person",
                  ("Sophie Tran" in by["create_crm_contact"]["description"],
                   by["create_crm_contact"]["annotations"]["readOnlyHint"]), (True, False))

            print("\nA read answers as the person, in their firm")
            found = await mcp.call_tool("search_households", {"query": "Chen"})
            check("finds the Chens", any("Chen" in (h.get("name") or "") for h in found.get("results") or []), True)
            await refused("another firm's household is not read",
                          mcp.call_tool("read_household", {"household_id": str(uuid.uuid4())}))
            forbidden = next(k for k, t in sorted(tools_for_role(UserRole.COMPLIANCE).items())
                             if UserRole.ADVISER not in t.roles_required)
            await refused(f"a tool the role does not hold ({forbidden})", mcp.call_tool(forbidden, {}), "may not")
            await refused("open_view is not offered", mcp.call_tool("open_view", {"view": "book"}), "beside")

            print("\nA change waits on the person's Confirm card")
            asked = await mcp.call_tool("create_crm_contact", {"full_name": "Hana Okoye", "source": "referral"})
            check("awaiting confirmation, put to Sophie", (asked["status"], asked["put_to"]),
                  ("awaiting_confirmation", "Sophie Tran"))
            pid = uuid.UUID(asked["confirmation_id"])
            st = await mcp.call_tool(oa.STATUS_TOOL, {"confirmation_id": str(pid)})
            check("its status is waiting", st["outcome"], "waiting")
            await refused("a confirmation from elsewhere is not read",
                          mcp.call_tool(oa.STATUS_TOOL, {"confirmation_id": str(uuid.uuid4())}), "no such")

        async with Session() as s:
            check("nothing changed yet", await _contacts(s, "Hana Okoye"), 0)
            p = await s.get(PendingAction, pid)
            thread = await s.get(Thread, p.thread_id)
            check("held for Sophie, on the key's own conversation",
                  (p.requires_user_id == sophie_id, thread.created_by_user_id == sophie_id, thread.title),
                  (True, True, "Outside agent: Planning agent"))

        async with http() as h:
            r = await h.post(f"/api/threads/pending-actions/{pid}/confirm", json={"confirmed": True},
                             headers={"Authorization": f"Bearer {token}"})
            check("the agent cannot confirm with its key", r.status_code, 401)
            r = await h.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                             headers={"Authorization": f"Bearer {jwt}"})
            check("a person's sign-in opens nothing at /api/mcp", r.status_code, 401)
            r = await h.post("/api/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}],
                             headers={"Authorization": f"Bearer {token}"})
            check("no batches", r.status_code, 400)
            r = await h.get("/api/mcp", headers={"Authorization": f"Bearer {token}"})
            check("no server stream", r.status_code, 405)
            r = await h.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                             headers={"Authorization": f"Bearer {token}", "Origin": "https://evil.example"})
            check("a foreign Origin is refused", r.status_code, 403)
            r = await h.post("/api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                             headers={"Authorization": f"Bearer {token}"})
            check("a notification is accepted", r.status_code, 202)

        async with Session() as s:
            done = await Gateway(s, sophie_id, UserRole.ADVISER, firm_id).confirm_pending_action(pid, True)
            check("Sophie confirms in Astra", done["status"], "confirmed")
        async with Session() as s:
            await refused("carried out once", Gateway(s, sophie_id, UserRole.ADVISER, firm_id)
                          .confirm_pending_action(pid, True), "already")
            check("the contact now exists, once", await _contacts(s, "Hana Okoye"), 1)

        async with agent(token) as mcp:
            st = await mcp.call_tool(oa.STATUS_TOOL, {"confirmation_id": str(pid)})
            check("the agent reads it done, with the result", (st["status"], st["outcome"], "result" in st),
                  ("confirmed", "done", True))
            asked = await mcp.call_tool("create_crm_contact", {"full_name": "Declined Person"})
            pid2 = uuid.UUID(asked["confirmation_id"])
        async with Session() as s:
            await Gateway(s, sophie_id, UserRole.ADVISER, firm_id).confirm_pending_action(pid2, False)
        async with agent(token) as mcp:
            st = await mcp.call_tool(oa.STATUS_TOOL, {"confirmation_id": str(pid2)})
            check("declined reads not done", (st["status"], st["outcome"]), ("rejected", "not done"))
            asked = await mcp.call_tool("create_crm_contact", {"full_name": "Left Waiting"})
            pid3 = uuid.UUID(asked["confirmation_id"])

        print("\nRevoking stops it at once")
        async with Session() as s:
            firm = await s.get(type(fx.firm), firm_id)
            await refused("someone else's key is not theirs to revoke",
                          oa.revoke(s, firm, await s.get(type(ops), ops.id), access_id=key_id, reason="x"),
                          "administrator")
            ex = ToolExecutors(s, sophie_id, UserRole.ADVISER, firm_id)
            await refused("revoking needs a reason", ex.preview_change("revoke_outside_agent", {"access_id": str(key_id)}),
                          "reason")
            preview = await ex.preview_change("revoke_outside_agent", {"access_id": str(key_id),
                                                                       "reason": "Pilot finished"})
            rows = {r["label"]: r["value"] for r in preview["rows"]}
            check("the Confirm card names the agent and the reason", (rows["Agent"], rows["Reason"]),
                  ("Planning agent", "Pilot finished"))
            listed = await ex.execute("read_outside_agents", {})
            check("the conversation reads the keys, never a key's value",
                  (listed["on"], any("key" in k for k in listed["keys"]), listed["active"]), (True, False, 2))
            out = await ex.execute("revoke_outside_agent", {"access_id": str(key_id), "reason": "Pilot finished"})
            await s.commit()
            check("revoked, by Sophie", (out["state"], out["revoked_by"]), ("revoked", "Sophie Tran"))
            check("what it waited on is declined", (await s.get(PendingAction, pid3)).status,
                  PendingActionStatus.REJECTED)
        await refused("a revoked key is refused", _open(agent(token)), "credentials")
        async with Session() as s:
            row = await s.get(OutsideAgentAccess, other_id)
            row.expires_at = utcnow() - timedelta(minutes=1)
            await s.commit()
        await refused("an expired key is refused", _open(agent(other_token)), "credentials")
        await refused("no key, no entry", _open(agent(None)), "credentials")

        async with Session() as s:
            firm = await s.get(type(fx.firm), firm_id)
            sophie = await s.get(type(fx.adviser), sophie_id)
            row, fresh = await oa.issue(s, firm, sophie, label="Third", days=1)
            await s.commit()
        async with agent(fresh) as mcp:
            await mcp.list_tools()
        async with Session() as s:
            (await s.get(type(fx.adviser), sophie_id)).is_active = False
            await s.commit()
        await refused("a person deactivated stops their key", _open(agent(fresh)), "credentials")
        async with Session() as s:
            (await s.get(type(fx.adviser), sophie_id)).is_active = True
            firm = await s.get(type(fx.firm), firm_id)
            await rulebook.set_setting(s, firm, actor=await s.get(type(admin), admin.id), key="outside_agents",
                                       value=False, reason="Pilot paused")
            await s.commit()
        await refused("switched off, every key is refused", _open(agent(fresh)), "credentials")

        print("\nOn the audit log, and a client holds none of it")
        async with Session() as s:
            events = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "outside_agent.tool_call")))\
                .scalars().all()
            check("every call is on the audit log", len(events) >= 9, True)
            check("with the tool and whether it worked, never its inputs",
                  all(set(e.detail) == {"access_id", "agent", "ok"} and "Hana" not in str(e.detail) for e in events),
                  True)
            check("a refused call is logged as not ok", any(e.detail["ok"] is False for e in events), True)
            v = (await s.execute(select(func.count()).select_from(AiViolation).where(
                AiViolation.subject_key == forbidden))).scalar()
            check("the forbidden tool is counted as a violation", v, 1)
            check("revoking is on the audit log", (await s.execute(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.event_type == "outside_agent.revoked"))).scalar(), 1)
        client_tools = set(tools_for_role(UserRole.CLIENT))
        check("a client holds neither tool", {"read_outside_agents", "revoke_outside_agent"} & client_tools, set())
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


async def _user(Session, uid):
    from app.models.identity import User

    async with Session() as s:
        return await s.get(User, uid)


async def _contacts(s, name: str) -> int:
    return (await s.execute(select(func.count()).select_from(CrmContact).where(CrmContact.full_name == name))).scalar()


async def _open(client: MCPClient) -> None:
    async with client as mcp:
        await mcp.list_tools()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
