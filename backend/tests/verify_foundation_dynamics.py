"""Prove Dynamics 365 as a firm's CRM (B-163), and the Foundation tools around it.

An administrator sets the CRM's owner, with a reason, behind a Confirm card; operations syncs it. The mock reads a
Dataverse-shaped fixture into the pipeline's own tables, marked with where each row came from, and a second sync
changes nothing. The pipeline and a prospect's brief read the synced records unchanged. Astra's CRM writes are refused
naming Dynamics 365, except an activity, which leaves as a command and appears only when a sync brings it back. A record
changed here and in Dynamics 365 is a break with its evidence, neither overwritten; untouched here, the copy follows.
The live client signs in with client credentials and never puts a secret in a message. A firm on native CRM keeps
today's behaviour exactly.

Runs against a throwaway SQLite database.

    python -m tests.verify_foundation_dynamics
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
import uuid
from datetime import timedelta

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import admin, conduit, crm as crm_api
from app.aurea_core import pipeline
from app.conduit.registry import get_provider_def
from app.core.db import Base, utcnow
from app.core.tools import TOOLS, ToolChangeState, tools_for_role
from app.foundation import owners
from app.foundation.adapters import dynamics
from app.foundation.adapters.base import AdapterError
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.connectors import Connector, ConnectorSync
from app.models.crm import CrmActivity, CrmContact, CrmOpportunity
from app.models.enums import ConnectorDomain, ConnectorStatus, UserRole
from app.models.foundation import ExternalRef, FoundationBreak, FoundationCommand
from app.models.identity import AuditEvent, User
from app.models.tenant import Firm

PASS, FAIL = [], []
R = UserRole
SECRET = "s3cr3t-client-value"


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {str(got)[:150]}, want {str(want)[:150]}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, HTTPException, AdapterError) as e:
        text = str(getattr(e, "detail", e))
        ok = all(c.lower() in text.lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {text[:130]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def rows_of(preview) -> dict:
    return {r["label"]: r["value"] for r in (preview or {}).get("rows", [])}


async def crm_counts(s, firm) -> tuple:
    return tuple([(await s.execute(select(func.count()).select_from(m).where(m.firm_id == firm.id))).scalar_one()
                  for m in (CrmContact, CrmOpportunity, CrmActivity)])


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(db, expire_on_commit=False)() as s:
            firm = Firm(id=uuid.uuid4(), name="Kauri", slug="kauri", base_currency="NZD", jurisdiction="NZ", settings={})
            plain = Firm(id=uuid.uuid4(), name="Plain", slug="plain", base_currency="NZD", jurisdiction="NZ", settings={})
            s.add_all([firm, plain])
            await s.flush()
            people = {}
            for key, role, f, email in (("ada", R.ADMIN, firm, "ada@kauri.test"), ("ola", R.OPERATIONS, firm, "ola@kauri.test"),
                                        ("mere", R.ADVISER, firm, "d365.adviser@contoso.example"),
                                        ("cora", R.COMPLIANCE, firm, "cora@kauri.test"), ("bea", R.BRANCH_LEADER, firm, "bea@kauri.test"),
                                        ("pat", R.ADVISER, plain, "pat@plain.test")):
                people[key] = User(id=uuid.uuid4(), firm_id=f.id, email=email, full_name=key.title(), role=role,
                                   hashed_password="x", is_active=True)
                s.add(people[key])
            conn_row = Connector(id=uuid.uuid4(), firm_id=firm.id, domain=ConnectorDomain.CRM, provider_key="crm.dynamics",
                                 display_name="Microsoft Dynamics 365", status=ConnectorStatus.MOCK, use_mock=True, config={})
            plain_conn = Connector(id=uuid.uuid4(), firm_id=plain.id, domain=ConnectorDomain.CRM, provider_key="crm.dynamics",
                                   display_name="Microsoft Dynamics 365", status=ConnectorStatus.MOCK, use_mock=True, config={})
            s.add_all([conn_row, plain_conn])
            await s.flush()
            ex = lambda u: ToolExecutors(s, u.id, u.role, u.firm_id)  # noqa: E731

            print("\n=== The tools, and who holds them ===")
            check("the read is operations', the admin's, compliance's and a branch leader's",
                  {r.value for r in TOOLS["read_foundation_sources"].roles_required}, {"operations", "admin", "compliance", "branch_leader"})
            check("a sync is operations' (and the admin's), behind a Confirm",
                  ({r.value for r in TOOLS["sync_foundation_source"].roles_required}, TOOLS["sync_foundation_source"].change_state),
                  ({"operations", "admin"}, ToolChangeState.PAUSE_FOR_CONFIRMATION))
            check("an owner is the admin's alone, behind a Confirm",
                  ({r.value for r in TOOLS["set_foundation_owner"].roles_required}, TOOLS["set_foundation_owner"].change_state),
                  ({"admin"}, ToolChangeState.PAUSE_FOR_CONFIRMATION))
            check("no Foundation tool takes a secret or a config",
                  [k for k in ("read_foundation_sources", "sync_foundation_source", "set_foundation_owner")
                   if any(i.name in ("config", "client_secret", "api_key") for i in TOOLS[k].inputs)], [])
            check("a client and an adviser hold none", [k for k in ("read_foundation_sources", "sync_foundation_source", "set_foundation_owner")
                                                       if k in tools_for_role(R.CLIENT) or k in tools_for_role(R.ADVISER)], [])
            pdef = get_provider_def("crm.dynamics")
            check("the connector is built live, with its secret marked secret",
                  (pdef.supports_live, [f.key for f in pdef.config_schema if f.secret]), (True, ["client_secret"]))

            print("\n=== A firm on native CRM keeps today's behaviour ===")
            sync = await ToolExecutors(s, people["pat"].id, R.ADMIN, plain.id).sync_connector({"connector_id": str(plain_conn.id)})
            check("its Dynamics 365 connector's sync brings in nothing", (await crm_counts(s, plain), "0 record" in sync["message"]),
                  ((0, 0, 0), True))
            pc = CrmContact(firm_id=plain.id, full_name="Native Nell", contact_type="prospect", owner_user_id=people["pat"].id, is_active=True)
            s.add(pc)
            await s.flush()
            logged = await ex(people["pat"]).log_crm_activity({"contact_id": str(pc.id), "activity_type": "call", "detail": "Rang"})
            check("an activity is written at once, as before", ("activity_id" in logged, (await crm_counts(s, plain))[2]), (True, 1))
            await refused("its sync of a native domain is refused", ToolExecutors(s, people["pat"].id, R.ADMIN, plain.id)
                          .preview_change("sync_foundation_source", {"domain": "crm"}), "Astra's own tables", "nothing to pull")

            print("\n=== The admin sets Dynamics 365 as the CRM ===")
            ada, ola = ex(people["ada"]), ex(people["ola"])
            await refused("not without a reason", ada.preview_change("set_foundation_owner", {"domain": "crm", "owner": "crm.dynamics"}), "why")
            await refused("not by operations", ola.preview_change("set_foundation_owner", {"domain": "crm", "owner": "crm.dynamics", "reason": "x"}),
                          "administrator")
            await refused("not a product that cannot own the domain",
                          ada.preview_change("set_foundation_owner", {"domain": "positions", "owner": "crm.dynamics", "reason": "x"}), "native")
            await refused("not a domain there is not", ada.preview_change("set_foundation_owner", {"domain": "cash", "owner": "native", "reason": "x"}),
                          "no domain")
            card = rows_of(await ada.preview_change("set_foundation_owner", {"domain": "crm", "owner": "crm.dynamics",
                                                                             "reason": "The client's CRM is Dynamics 365"}))
            check("the card names the change and what follows", (card.get("Owner"), card.get("Differences"), card.get("Writes")),
                  ("Astra → Dynamics 365", "Reported as breaks, nothing overwritten", "Activities go to Dynamics 365"))
            check("and the card wrote nothing", owners.owner(firm, "crm"), "native")
            done = await ada.set_foundation_owner({"domain": "crm", "owner": "crm.dynamics", "reason": "The client's CRM is Dynamics 365"})
            check("Confirm sets it", (owners.owner(firm, "crm"), done["section"]), ("crm.dynamics", "foundation"))
            ev = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "foundation.owner_set"))).scalars().one()
            check("on the audit log with the reason", (ev.detail["after"], ev.detail["reason"]), ("crm.dynamics", "The client's CRM is Dynamics 365"))
            check("the other domains stay native", [d for d, o in owners.owners(firm).items() if o != "native"], ["crm"])
            await refused("set again, it is already so", ada.preview_change("set_foundation_owner",
                          {"domain": "crm", "owner": "crm.dynamics", "reason": "x"}), "already")

            print("\n=== Operations syncs it ===")
            card = rows_of(await ola.preview_change("sync_foundation_source", {"domain": "crm"}))
            check("the card says mock and what it writes", (card.get("Mode"), "break" in (card.get("Writes") or "")), ("Mock", True))
            await refused("not by compliance", ex(people["cora"]).preview_change("sync_foundation_source", {"domain": "crm"}), "operations")
            first = await ola.sync_foundation_source({"domain": "crm"})
            check("contacts, opportunities and activities come in", await crm_counts(s, firm), (4, 4, 6))
            check("the result is the record and its counts", {c["field"]: c["to"] for c in first["changes"]}.get("created"), 14)
            sync = (await s.execute(select(ConnectorSync).where(ConnectorSync.connector_id == conn_row.id)
                                    .order_by(ConnectorSync.created_at.desc()))).scalars().first()
            found = sync.detail["foundation"]
            check("what had no place is skipped and named", (found["domains"]["crm"]["skipped_count"], "account" in found["domains"]["crm"]["skipped"][0]),
                  (1, True))
            aroha = (await s.execute(select(CrmContact).where(CrmContact.firm_id == firm.id, CrmContact.full_name == "Aroha Ngata"))).scalar_one()
            check("a synced contact says where it came from", (aroha.source_system, aroha.external_id, aroha.as_of is not None, aroha.source),
                  ("crm.dynamics", dynamics._C[0], True, "referral"))
            check("its owner is the adviser with that email in Dynamics 365", aroha.owner_user_id, people["mere"].id)
            opps = {o.title: o for o in (await s.execute(select(CrmOpportunity).where(CrmOpportunity.firm_id == firm.id))).scalars().all()}
            check("the stages map from the sales process", sorted((t.split(" - ")[0], o.stage) for t, o in opps.items()),
                  [("Aroha Ngata", "proposal"), ("Daniel Okafor", "lead"), ("Hannah Whitfield", "qualified"), ("Ravi Menon", "lost")])
            check("a probability nobody gave is none", opps["Daniel Okafor - retirement rollover"].probability_pct, None)
            check("a lost deal has its reason", opps["Ravi Menon - new relationship"].lost_reason, "Out-sold in Dynamics 365")
            again = await ola.sync_foundation_source({"domain": "crm"})
            got = {c["field"]: c["to"] for c in again["changes"]}
            check("a second sync changes nothing", (got["created"], got["updated"], got["breaks"], await crm_counts(s, firm)), (0, 0, 0, (4, 4, 6)))

            print("\n=== The pipeline reads the synced records unchanged ===")
            o = await pipeline.overview(s, firm, people["bea"])
            check("the funnel", (o["open"]["count"], o["lost"]["count"], [x["count"] for x in o["stages"]]), (3, 1, [1, 1, 1]))
            check("an adviser reads the deals of the contacts Dynamics 365 says are theirs",
                  sorted(r["contact"] for r in (await pipeline.overview(s, firm, people["mere"]))["opportunities"]), ["Aroha Ngata", "Daniel Okafor"])
            brief = await pipeline.prospect_brief(s, firm, aroha.id)
            check("a prospect's brief", (brief["contact"]["name"], len(brief["opportunities"]), brief["activity_count"]), ("Aroha Ngata", 1, 2))
            hannah = (await s.execute(select(CrmContact).where(CrmContact.full_name == "Hannah Whitfield"))).scalar_one()
            check("a meeting booked in Dynamics 365 shows as booked", len((await pipeline.prospect_brief(s, firm, hannah.id))["booked"]), 1)

            print("\n=== Astra's CRM writes, with Dynamics 365 the owner ===")
            mere = ex(people["mere"])
            await refused("adding a contact is refused naming the owner", mere.preview_change("create_crm_contact", {"full_name": "New Person"}),
                          "Dynamics 365 owns", "add the contact")
            await refused("  and at Confirm", mere.create_crm_contact({"full_name": "New Person"}), "Dynamics 365")
            await refused("opening a deal", mere.create_crm_opportunity({"contact_id": str(aroha.id), "title": "x"}), "Dynamics 365")
            deal = opps["Aroha Ngata - business sale proceeds"]
            await refused("moving a deal", mere.preview_change("update_crm_opportunity_stage", {"opportunity_id": str(deal.id), "stage": "qualified"}),
                          "Dynamics 365")
            await refused("the routes refuse the same",
                          crm_api.create_contact(crm_api.ContactIn(full_name="X"), user=people["mere"], firm=firm, db=s), "Dynamics 365")
            check("nothing was written", await crm_counts(s, firm), (4, 4, 6))
            args = {"contact_id": str(aroha.id), "opportunity_id": str(deal.id), "activity_type": "call",
                    "detail": "Confirmed the December settlement date"}
            card = rows_of(await mere.preview_change("log_crm_activity", args))
            check("an activity's card says where it is recorded", (card.get("Recorded in"), card.get("Here")),
                  ("Dynamics 365 · mock", "Once the next sync brings it back"))
            sent = await mere.log_crm_activity(args)
            cmd = await s.get(FoundationCommand, uuid.UUID(sent["command_id"]))
            check("it leaves as a command, pending", (cmd.status, cmd.kind, cmd.source, cmd.external_id is not None), ("pending", "crm.activity.create", "crm.dynamics", True))
            check("and no activity is written here yet", (await crm_counts(s, firm))[2], 6)
            nell = CrmContact(firm_id=firm.id, full_name="Local Only", contact_type="prospect", is_active=True)
            s.add(nell)
            await s.flush()
            await refused("on a contact Dynamics 365 does not have, it is refused",
                          mere.preview_change("log_crm_activity", {"contact_id": str(nell.id), "detail": "x"}), "not in Dynamics 365")
            src = await ex(people["ola"]).read_foundation_sources({})
            crm_row = next(d for d in src["domains"] if d["domain"] == "crm")
            check("the read shows the command waiting", (crm_row["pending_commands"], crm_row["owner"], crm_row["adapter"]), (1, "crm.dynamics", "Dynamics 365"))
            await ola.sync_foundation_source({"domain": "crm"})
            await s.refresh(cmd)
            row = await s.get(CrmActivity, cmd.local_id) if cmd.local_id else None
            check("the next sync brings it back and confirms it", (cmd.status, row is not None and row.detail, row and row.source_system),
                  ("confirmed", "Confirmed the December settlement date", "crm.dynamics"))
            check("  on the deal, in the adviser's name", (row.opportunity_id, row.logged_by_user_id), (deal.id, people["mere"].id))
            third = await ola.sync_foundation_source({"domain": "crm"})
            check("and the one after changes nothing", ({c["field"]: c["to"] for c in third["changes"]}["created"], (await crm_counts(s, firm))[2]), (0, 7))
            route = await crm_api.log_activity(crm_api.ActivityIn(contact_id=aroha.id, activity_type="note", detail="Prefers mornings"),
                                               user=people["mere"], firm=firm, db=s)
            check("the route sends the same command", (route["status"], (await crm_counts(s, firm))[2]), ("pending", 7))

            print("\n=== A difference is a break, never overwritten ===")
            deal.title = "Aroha - edited in Astra"
            await s.flush()
            await ola.sync_foundation_source({"domain": "crm"})
            await s.refresh(deal)
            brk = (await s.execute(select(FoundationBreak).where(FoundationBreak.firm_id == firm.id, FoundationBreak.status == "open"))).scalars().all()
            check("a record changed here is a break", [(b.entity_type, b.external_id) for b in brk], [("crm_opportunity", dynamics._O[0])])
            check("  with both values as evidence", [(f["field"], f["here"], f["source"]) for f in brk[0].fields],
                  [("title", "Aroha - edited in Astra", "Aroha Ngata - business sale proceeds")])
            check("  and the copy is not overwritten", deal.title, "Aroha - edited in Astra")
            src = await ex(people["cora"]).read_foundation_sources({})
            check("compliance reads the break", (next(d for d in src["domains"] if d["domain"] == "crm")["open_breaks"], src["breaks"][0]["entity"]),
                  (1, "crm_opportunity"))
            deal.title = "Aroha Ngata - business sale proceeds"
            await s.flush()
            await ola.sync_foundation_source({"domain": "crm"})
            await s.refresh(brk[0])
            check("put back, the next sync clears it", brk[0].status, "cleared")
            real = dynamics.fixture

            def moved():
                raw = real()
                raw["opportunities"][1]["estimatedvalue"] = 515000.0
                raw["opportunities"][1]["stepname"] = "2-Develop"
                raw["opportunities"][1]["modifiedon"] = "2026-11-19T09:00:00Z"
                return raw
            dynamics.fixture = moved
            try:
                got = {c["field"]: c["to"] for c in (await ola.sync_foundation_source({"domain": "crm"}))["changes"]}
            finally:
                dynamics.fixture = real
            daniel = opps["Daniel Okafor - retirement rollover"]
            await s.refresh(daniel)
            check("untouched here, the copy follows Dynamics 365", (got["updated"], float(daniel.estimated_aum), daniel.stage), (1, 515000.0, "qualified"))

            print("\n=== Live: the Dataverse Web API ===")
            try:
                dynamics.DataverseClient({"tenant_id": "t", "client_secret": ""})
                FAIL.append("missing settings are refused")
            except AdapterError as e:
                check("missing settings are named, not shown", ("Client secret" in str(e), "Environment URL" in str(e)), (True, True))
            try:
                dynamics.DataverseClient({"base_url": "http://x", "tenant_id": "t", "client_id": "c", "client_secret": SECRET})
                FAIL.append("http is refused")
            except AdapterError as e:
                check("an environment that is not https is refused", "https" in str(e) and SECRET not in str(e), True)
            seen = []

            def handler(req: httpx.Request) -> httpx.Response:
                seen.append(req)
                if "login.microsoftonline.com" in str(req.url):
                    body = req.content.decode()
                    return httpx.Response(200, json={"access_token": "tok"}) if SECRET in body else httpx.Response(401)
                if req.method == "POST":
                    return httpx.Response(201, json={"activityid": "11111111-2222-4333-8444-555555555555"})
                name = req.url.path.rsplit("/", 1)[-1]
                rows = dynamics.fixture()[name]
                if name == "contacts" and "skiptoken" not in str(req.url):
                    return httpx.Response(200, json={"value": rows[:2], "@odata.nextLink": str(req.url) + "&$skiptoken=2"})
                return httpx.Response(200, json={"value": rows[2:] if name == "contacts" else rows})

            cfg = {"base_url": "https://kauri.crm.dynamics.com", "tenant_id": "tenant-1", "client_id": "client-1", "client_secret": SECRET}
            client = dynamics.DataverseClient(cfg, transport=httpx.MockTransport(handler))
            raw = await client.read_all()
            check("it reads every page", len(raw["contacts"]), 4)
            check("with a bearer token and the lookup annotations",
                  all(r.headers.get("authorization") == "Bearer tok" and "lookuplogicalname" in r.headers.get("prefer", "")
                      for r in seen if "dynamics.com" in str(r.url)), True)
            check("and maps as the mock does", len(dynamics.to_contract(raw).records), 14)
            bad = dynamics.DataverseClient({**cfg, "client_secret": "wrong"}, transport=httpx.MockTransport(handler))
            try:
                await bad.read("contacts")
                FAIL.append("a refused sign-in raises")
            except AdapterError as e:
                check("a refused sign-in says so, with no secret", ("401" in str(e), "wrong" in str(e)), (True, False))
            body = dynamics.activity_body({"activity_type": "call", "detail": "Rang", "occurred_at": "2026-11-20T01:00:00+00:00",
                                           "contact_external_id": "c-1", "opportunity_external_id": "o-1"})
            check("an activity goes regarding its deal", body.get("regardingobjectid_opportunity@odata.bind"), "/opportunities(o-1)")
            made = await client.create_activity(body, "call")
            check("Dataverse creates it and returns its id", (made, seen[-1].url.path.endswith("/phonecalls")),
                  ("11111111-2222-4333-8444-555555555555", True))
            conn_row.use_mock, conn_row.config = False, cfg
            dynamics.DynamicsAdapter.transport = httpx.MockTransport(handler)
            try:
                live = await ola.sync_foundation_source({"domain": "crm"})
            finally:
                dynamics.DynamicsAdapter.transport = None
                conn_row.use_mock, conn_row.config = True, {}
            check("a live sync goes through the same import", {c["field"]: c["to"] for c in live["changes"]}["status"], "success")
            check("no secret reaches a result, a sync record or the audit log",
                  SECRET in json.dumps([live, [x.detail for x in (await s.execute(select(ConnectorSync))).scalars().all()],
                                        [x.detail for x in (await s.execute(select(AuditEvent))).scalars().all()]], default=str), False)

            print("\n=== The routes ===")
            role_of = lambda route: inspect.signature(route).parameters["user"].default.dependency  # noqa: E731
            await refused("operations may not set an owner on the route", role_of(admin.set_foundation_owner)(user=people["ola"]), "role")
            await refused("an adviser may not read the sources on the route", role_of(conduit.foundation_sources)(user=people["mere"]), "role")
            await refused("compliance may not sync on the route", role_of(conduit.sync_foundation_source)(user=people["cora"]), "role")
            await refused("a sync of a domain Astra owns", conduit.sync_foundation_source("positions", user=people["ola"], firm=firm, db=s), "nothing to pull")
            back = await admin.set_foundation_owner("crm", admin.FoundationOwnerIn(owner="native", reason="Back to Astra"),
                                                   user=people["ada"], firm=firm, db=s)
            check("the admin's route sets it back to native", back["owners"]["crm"], "native")
            o = await conduit.foundation_sources(user=people["bea"], firm=firm, db=s)
            check("the read route counts every CRM row (5 contacts, 4 deals, 8 activities with both commands back)",
                  (o["native"], next(d for d in o["domains"] if d["domain"] == "crm")["records"], o["commands"][0]["status"]), (True, 17, "confirmed"))
            check("with native back, an activity is written here again",
                  "activity_id" in await mere.log_crm_activity({"contact_id": str(aroha.id), "detail": "Rang again"}), True)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
