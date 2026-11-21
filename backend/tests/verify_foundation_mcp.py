"""Prove the wealth platform reached through MCP (B-164, B-165), against a test MCP server in this suite.

The MCP client speaks the wire protocol itself: initialize with protocol 2025-06-18, notifications/initialized, the
session id carried, a JSON or a server-sent-event answer, a JSON-RPC error and a tool error raised with the server's
words, a refused credential named without its value, an expired session re-opened. The adapter reads the Foundation
MCP profile v1: it refuses a server without the profile's tools, pages through a domain, skips what does not fit the
contract, and records the capabilities the server declares, which `owners` then reads. Through the tools an
administrator sets the owner and operations syncs: records arrive marked with their source, a second sync changes
nothing, and the Foundation read shows the owner, its platform, the last sync and as_of.

Orders out: with the positions owned by the platform, a direct order leaves as an `orders.place` command and the book
does not move; the order waits as placed, and the overview counts it. When the platform's trade syncs back the command
is confirmed and the fill recorded on the order, booked by the sync with no tax lot made here and nothing booked twice.
A refusal at the platform rejects the order and moves nothing. A platform that takes no orders, or owns the positions
without the activity, refuses the card naming itself. A native firm keeps the paper venue exactly.

Runs against a throwaway SQLite database, with the test server reached through httpx's ASGI transport.

    python -m tests.verify_foundation_mcp
"""
from __future__ import annotations

import asyncio
import copy
import json
import sys
import uuid
from datetime import date

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.conduit.execution import venue_for_firm
from app.conduit.registry import get_provider_def
from app.core.db import Base
from app.foundation import owners
from app.foundation.adapters import for_domain, get_adapter
from app.foundation.adapters import mcp
from app.foundation.adapters.base import AdapterError
from app.foundation.orders import route
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.connectors import Connector
from app.models.enums import AssetClass, ConnectorDomain, ConnectorStatus, MarketType, UserRole
from app.models.foundation import FoundationCommand
from app.models.graph import Account, Household, Mandate
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Price, TaxLot, Transaction
from app.models.tenant import Firm
from app.models.trading import Execution, Order

PASS, FAIL = [], []
R = UserRole
SECRET = "Bearer tok-9f8e7d-secret"
URL = "https://platform.test/mcp"
STAMP = "2026-11-10T09:00:00+00:00"


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {str(got)[:160]}, want {str(want)[:160]}")


async def refused(label: str, coro, *contains: str) -> str:
    try:
        await coro
    except (ExecutorError, HTTPException, AdapterError) as e:
        text = str(getattr(e, "detail", e))
        ok = all(c.lower() in text.lower() for c in contains) and SECRET not in text
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {text[:150]}")
        return text
    FAIL.append(label)
    print(f"  FAIL  {label}: was not refused")
    return ""


def rows_of(preview) -> dict:
    return {r["label"]: r["value"] for r in (preview or {}).get("rows", [])}


# ── A test MCP server speaking the Foundation profile ─────────────────────────────────────────────

def base_book() -> dict:
    return {
        "parties": [
            {"entity": "household", "external_id": "H1", "as_of": STAMP, "name": "The Test Household",
             "values": {"jurisdiction": "US"}},
            {"entity": "person", "external_id": "P1", "as_of": STAMP, "household_ref": "H1", "full_name": "Tess Tester",
             "date_of_birth": "1970-01-02"},
            {"entity": "crm_contact", "external_id": "C1", "full_name": "Not a party"},   # another domain's: skipped
        ],
        "accounts": [
            {"entity": "mandate", "external_id": "M1", "as_of": STAMP, "person_ref": "P1", "name": "Tester - Growth",
             "mandate_type": "advisory"},
            {"entity": "account", "external_id": "A1", "as_of": STAMP, "mandate_ref": "M1", "name": "Tester brokerage",
             "currency": "USD", "cash_balance": 10000.0, "custodian": "Test Platform"},
        ],
        "positions": [
            {"entity": "holding", "external_id": "A1-VTI", "as_of": STAMP, "account_ref": "A1", "symbol": "VTI",
             "quantity": 100.0, "market_value": 30000.0, "cost_basis": 20000.0},
            {"entity": "holding", "external_id": "BAD", "account_ref": "A1"},   # does not fit the contract: skipped
        ],
        "activity": [
            {"entity": "txn", "external_id": "T1", "as_of": STAMP, "account_ref": "A1", "symbol": "VTI", "txn_type": "buy",
             "quantity": 100.0, "price": 200.0, "amount": 20000.0, "trade_date": "2026-06-01", "lineage": {}},
        ],
    }


class TestServer:
    def __init__(self):
        self.book = base_book()
        self.sse = False
        self.page = 2
        self.profile = mcp.PROFILE
        self.tools = list(mcp.TOOLS)
        self.protocol = mcp.PROTOCOL
        self.commands = ["orders.place"]
        self.caps = {"tax_lots": False, "transactions_days": 90, "events": False}
        self.need_auth = SECRET
        self.drop_sessions = False
        self.sessions: set[str] = set()
        self.seen: list[tuple[str, dict]] = []      # (method, headers)
        self.asked_protocol = None
        self.orders: list[dict] = []
        self.refuse_symbol = "AAPL"

    def state(self) -> dict:
        data = copy.deepcopy(self.book)
        holdings = {(h.get("account_ref"), h.get("symbol")): h for h in data["positions"]}
        account = next(a for a in data["accounts"] if a["entity"] == "account")
        for o in self.orders:
            h = holdings.get((o["account_external_id"], o["symbol"]))
            if h is None:
                h = {"entity": "holding", "external_id": f"A1-{o['symbol']}", "account_ref": "A1", "symbol": o["symbol"],
                     "quantity": 0.0, "market_value": 0.0, "cost_basis": 0.0, "as_of": STAMP}
                data["positions"].append(h)
                holdings[("A1", o["symbol"])] = h
            gross = o["quantity"] * o["price"]
            sign = 1 if o["side"] == "buy" else -1
            h["quantity"] += sign * o["quantity"]
            h["cost_basis"] += sign * gross
            account["cash_balance"] -= sign * gross
            h["as_of"] = account["as_of"] = "2026-11-21T10:00:00+00:00"
            data["activity"].append({"entity": "txn", "external_id": f"T-{o['ext']}", "account_ref": "A1",
                                     "symbol": o["symbol"], "txn_type": o["side"], "quantity": o["quantity"],
                                     "price": o["price"], "amount": gross, "trade_date": "2026-11-21",
                                     "lineage": {"order_ref": o["ext"], "fees": 0, "price_source": "test"}})
        return data

    def call(self, name: str, args: dict):
        if name == "foundation_capabilities":
            return {"profile": self.profile, "contract_versions": ["1"], "domains": ["parties", "accounts", "positions", "activity"],
                    "capabilities": self.caps, "commands": self.commands, "platform": {"name": "Test Platform", "version": "9"},
                    "simulated": False}
        if name == "foundation_read":
            rows = self.state()[args["domain"]]
            start = int(args.get("cursor") or 0)
            nxt = start + self.page
            return {"records": rows[start:nxt], "next_cursor": str(nxt) if nxt < len(rows) else None}
        if name == "foundation_command":
            p = args["payload"]
            if p.get("symbol") == self.refuse_symbol:
                raise ValueError(f"{p['symbol']} is not tradable on this platform.")
            ext = f"ORD-{len(self.orders) + 1}"
            self.orders.append({**p, "ext": ext, "price": float(p.get("est_price") or 0)})
            return {"status": "accepted", "external_id": ext, "received_at": "2026-11-21T10:00:00+00:00"}
        raise KeyError(name)

    async def __call__(self, scope, receive, send):
        body = b""
        while True:
            m = await receive()
            body += m.get("body", b"")
            if not m.get("more_body"):
                break
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
        if scope["method"] == "DELETE":
            return await self._raw(send, 204, b"")
        if self.need_auth and headers.get("authorization") != self.need_auth:
            return await self._raw(send, 401, b'{"error":"unauthorised"}')
        req = json.loads(body)
        method = req.get("method")
        self.seen.append((method, headers))
        if "id" not in req:
            return await self._raw(send, 202, b"")
        rid = req["id"]
        if method == "initialize":
            self.asked_protocol = (req.get("params") or {}).get("protocolVersion")
            sid = uuid.uuid4().hex
            self.sessions.add(sid)
            return await self._reply(send, {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": self.protocol, "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-platform", "version": "9"}}}, extra=[(b"mcp-session-id", sid.encode())])
        if headers.get("mcp-session-id") not in self.sessions:
            return await self._raw(send, 404, b"")
        if self.drop_sessions:   # the server ends every session after one request
            self.sessions.discard(headers.get("mcp-session-id"))
        if method == "tools/list":
            tools = [{"name": t, "inputSchema": {"type": "object"}} for t in self.tools]
            half = len(tools) // 2
            cursor = (req.get("params") or {}).get("cursor")
            page = {"tools": tools[half:]} if cursor else {"tools": tools[:half], "nextCursor": "more"}
            return await self._reply(send, {"jsonrpc": "2.0", "id": rid, "result": page})
        if method == "tools/call":
            params = req.get("params") or {}
            try:
                out = self.call(params["name"], params.get("arguments") or {})
            except KeyError:
                return await self._reply(send, {"jsonrpc": "2.0", "id": rid,
                                                "error": {"code": -32602, "message": f"Unknown tool {params['name']}"}})
            except ValueError as exc:
                return await self._reply(send, {"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": str(exc)}], "isError": True}})
            # Structured content one way, text the other, so both readings are proved.
            result = ({"content": [{"type": "text", "text": json.dumps(out)}]} if params["name"] == "foundation_read"
                      else {"content": [{"type": "text", "text": "see structured"}], "structuredContent": out})
            return await self._reply(send, {"jsonrpc": "2.0", "id": rid, "result": result})
        return await self._reply(send, {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no such method"}})

    async def _raw(self, send, status, body, ctype=b"application/json", extra=None):
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", ctype)] + list(extra or [])})
        await send({"type": "http.response.body", "body": body})

    async def _reply(self, send, msg, extra=None):
        if self.sse:   # a stray notification first, then the answer, as a stream may carry
            text = ("event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
                    + "\n\nevent: message\ndata: " + json.dumps(msg) + "\n\n")
            return await self._raw(send, 200, text.encode(), b"text/event-stream", extra)
        return await self._raw(send, 200, json.dumps(msg).encode(), extra=extra)


async def main() -> int:
    server = TestServer()
    transport = httpx.ASGITransport(app=server)
    mcp.MCPAdapter.transport = transport
    saved_mock = mcp._MOCK_SERVER["factory"]
    mcp.register_mock_server(None)
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        print("\n=== The wire: JSON-RPC 2.0 over Streamable HTTP ===")
        async with mcp.MCPClient(URL, headers={"Authorization": SECRET}, transport=transport) as client:
            check("initialize asks for protocol 2025-06-18", server.asked_protocol, "2025-06-18")
            check("notifications/initialized is sent after it", [m for m, _ in server.seen][:2],
                  ["initialize", "notifications/initialized"])
            tools = await client.list_tools()
            check("tools/list follows its cursor", sorted(t["name"] for t in tools), sorted(mcp.TOOLS))
            sid_seen = server.seen[-1][1].get("mcp-session-id")
            check("the session id is carried, with the protocol version", (sid_seen == client.session_id,
                  server.seen[-1][1].get("mcp-protocol-version")), (True, "2025-06-18"))
            check("a structured answer is read", (await client.call_tool("foundation_capabilities", {}))["profile"], mcp.PROFILE)
            check("a text answer is read as JSON", len((await client.call_tool("foundation_read", {"domain": "parties"}))["records"]), 2)
            server.sse = True
            check("an event-stream answer is read, past a notification",
                  (await client.call_tool("foundation_capabilities", {}))["platform"]["name"], "Test Platform")
            server.sse = False
            await refused("a JSON-RPC error is raised with the server's words", client.call_tool("nope", {}), "unknown tool nope")
            await refused("a tool error is raised with its text", client.call_tool(
                "foundation_command", {"kind": "orders.place", "payload": {"symbol": "AAPL"}, "idempotency_key": "x"}),
                "not tradable")
            server.drop_sessions = True
            await client.call_tool("foundation_capabilities", {})   # answered, and the server ends the session
            server.drop_sessions = False
            got = await client.call_tool("foundation_capabilities", {})
            check("an expired session is opened again once", (got["profile"], [m for m, _ in server.seen][-4:]),
                  (mcp.PROFILE, ["tools/call", "initialize", "notifications/initialized", "tools/call"]))
        await refused("a refused credential is named, never its value",
                      mcp.MCPClient(URL, headers={"Authorization": "Bearer wrong"}, transport=transport).__aenter__(),
                      "refused the credentials", "401")
        server.protocol = "2024-11-05"
        await refused("a protocol it does not speak is refused",
                      mcp.MCPClient(URL, headers={"Authorization": SECRET}, transport=transport).__aenter__(), "2024-11-05")
        server.protocol = mcp.PROTOCOL
        check("an event stream's messages are split by event", len(mcp._sse_messages(
            'data: {"jsonrpc":"2.0","id":1,"result":{}}\n\ndata: {"jsonrpc":"2.0",\ndata: "id":2,"result":{}}\n\n')), 2)

        print("\n=== The adapter and its connector ===")
        adapter = get_adapter("foundation.mcp")
        check("built, serving parties, accounts, positions and activity",
              (adapter is not None, sorted(adapter.domains)), (True, ["accounts", "activity", "parties", "positions"]))
        check("not prices or the CRM", (adapter.serves("prices"), adapter.serves("crm")), (False, False))
        check("offered as an owner of positions", "foundation.mcp" in [a.key for a in for_domain("positions")], True)
        pdef = get_provider_def("foundation.mcp")
        check("the connector is live-capable, its URL is base_url and its auth value secret",
              (pdef.supports_live, [f.key for f in pdef.config_schema], [f.key for f in pdef.config_schema if f.secret]),
              (True, ["base_url", "auth_header", "auth_value"], ["auth_value"]))
        check("orders are the one command it takes", sorted(adapter.commands), ["orders.place"])

        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(db, expire_on_commit=False)() as s:
            firm = Firm(id=uuid.uuid4(), name="Atlas", slug="atlas", base_currency="USD", jurisdiction="US", settings={})
            plain = Firm(id=uuid.uuid4(), name="Plain", slug="plain", base_currency="USD", jurisdiction="US", settings={})
            s.add_all([firm, plain])
            await s.flush()
            people = {}
            for key, role in (("ada", R.ADMIN), ("ola", R.OPERATIONS), ("ann", R.ADVISER), ("cora", R.COMPLIANCE)):
                people[key] = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{key}@atlas.test", full_name=key.title(),
                                   role=role, hashed_password="x", is_active=True)
                s.add(people[key])
            for sym, px in (("VTI", 300.0), ("AAPL", 230.0)):
                inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=sym, name=sym, asset_class=AssetClass.EQUITY,
                                  market_type=MarketType.PUBLIC, currency="USD", market_symbol=sym)
                s.add(inst)
                await s.flush()
                s.add(Price(firm_id=firm.id, instrument_id=inst.id, as_of=date.today(), close=px, currency="USD",
                            source="yahoo", is_real=True))
            live = Connector(id=uuid.uuid4(), firm_id=firm.id, domain=ConnectorDomain.CUSTODY, provider_key="foundation.mcp",
                             display_name="Wealth platform (MCP)", status=ConnectorStatus.CONNECTED, use_mock=False,
                             config={"base_url": URL, "auth_header": "Authorization", "auth_value": SECRET})
            s.add(live)
            await s.flush()
            ex = lambda u: ToolExecutors(s, u.id, u.role, u.firm_id)  # noqa: E731
            ada, ola, ann = ex(people["ada"]), ex(people["ola"]), ex(people["ann"])

            live.config = {**live.config, "base_url": "http://platform.test/mcp"}
            await refused("a live URL must be https", adapter.pull(s, firm, live, "parties"), "https")
            live.config = {**live.config, "base_url": ""}
            await refused("a live connector needs its URL", adapter.pull(s, firm, live, "parties"), "url is not set")
            live.config = {**live.config, "base_url": URL}
            live.use_mock = True
            await refused("mock mode with no server set up is refused", adapter.pull(s, firm, live, "parties"), "mock mode")
            live.use_mock = False
            server.tools = ["foundation_capabilities", "foundation_read"]
            await refused("a server without the profile's tools is refused", adapter.pull(s, firm, live, "parties"),
                          "foundation_command")
            server.tools = list(mcp.TOOLS)
            server.profile = "someone-else/2"
            await refused("a server on another profile is refused", adapter.pull(s, firm, live, "parties"), "someone-else/2")
            server.profile = mcp.PROFILE
            check("before the server has said, nothing is claimed",
                  adapter.capabilities_for(firm), {"tax_lots": False, "transactions_days": False, "events": False})
            pulled = await adapter.pull(s, firm, live, "positions")
            check("a domain is read across its pages, the misfit skipped and named",
                  ([r.external_id for r in pulled.records], len(pulled.skipped), "BAD" in pulled.skipped[0]),
                  (["A1-VTI"], 1, True))
            pulled = await adapter.pull(s, firm, live, "parties")
            check("a record of another domain is skipped", ([r.entity for r in pulled.records], pulled.skipped[0][:12]),
                  (["household", "person"], "crm_contact "))
            check("every request carried the auth header", all(h.get("authorization") == SECRET for _, h in server.seen[-4:]), True)
            check("the server's capabilities are recorded on the firm", adapter.capabilities_for(firm),
                  {"tax_lots": False, "transactions_days": 90, "events": False})
            check("and its platform", adapter.declared(firm).get("platform"), {"name": "Test Platform", "version": "9"})

            print("\n=== The administrator sets the owner; operations syncs ===")
            card = rows_of(await ada.preview_change("set_foundation_owner", {"domain": "positions", "owner": "foundation.mcp",
                                                                             "reason": "WealthSmart owns the book"}))
            check("the card names the platform, its writes and its missing lots",
                  (card.get("Owner"), card.get("Writes"), card.get("Tax lots")),
                  ("Astra → Wealth platform (MCP)", "Orders go to Wealth platform (MCP)",
                   "None supplied: tax and rebalancing read not assessed"))
            for d in ("parties", "accounts", "positions", "activity"):
                await ada.set_foundation_owner({"domain": d, "owner": "foundation.mcp", "reason": "WealthSmart owns the book"})
            check("each domain is owned by it", [owners.owner(firm, d) for d in ("parties", "accounts", "positions", "activity", "prices")],
                  ["foundation.mcp"] * 4 + ["native"])
            gap = owners.lacks(firm, "tax_lots") or ""
            check("tax lots read not assessed, naming it", (gap.startswith("Not assessed"), "Wealth platform (MCP)" in gap,
                                                         "no tax lots" in gap), (True, True, True))
            check("and the transaction history is the platform's 90 days", owners.capability(firm, "transactions_days"), 90)
            first = await ola.sync_foundation_source({"domain": "positions"})
            got = {c["field"]: c["to"] for c in first["changes"]}
            check("the first sync creates the book", (got["created"], got["status"]), (6, "success"))
            hh = (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()
            acct = (await s.execute(select(Account).where(Account.firm_id == firm.id))).scalar_one()
            mandate = (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id))).scalar_one()
            vti = (await s.execute(select(Holding).where(Holding.firm_id == firm.id))).scalar_one()
            check("marked with where it came from", ([h.source_system for h in hh], acct.external_id, float(acct.cash_balance)),
                  (["foundation.mcp"], "A1", 10000.0))
            again = {c["field"]: c["to"] for c in (await ola.sync_foundation_source({"domain": "parties"}))["changes"]}
            check("a second sync changes nothing", (again["created"], again["updated"], again["breaks"]), (0, 0, 0))
            src = await ex(people["cora"]).read_foundation_sources({})
            pos = next(r for r in src["domains"] if r["domain"] == "positions")
            check("the read shows the owner, its platform, mode, last sync and as_of",
                  (pos["owner"], pos["platform"], pos["mode"], pos["last_sync"]["status"], bool(pos["as_of"]), pos["simulated"]),
                  ("foundation.mcp", "Test Platform", "live", "success", True, False))
            check("and its declared capabilities", pos["capabilities"], {"tax_lots": False, "transactions_days": 90, "events": False})

            print("\n=== Orders out through the platform (B-165) ===")
            venue = venue_for_firm(firm)
            check("the firm's venue is the platform, which claims no market", (venue.key, venue.platform, venue.reaches_market),
                  ("foundation.mcp", True, False))
            check("a native firm keeps the paper venue", (venue_for_firm(plain).key, getattr(venue_for_firm(plain), "platform", False)),
                  ("paper", False))
            order_in = {"mandate_id": str(mandate.id), "orders": [{"symbol": "VTI", "side": "buy", "quantity": 10}],
                        "reason": "Adding to the core holding"}
            card = rows_of(await ann.preview_change("execute_orders", order_in))
            check("the card says the order goes to the platform", card.get("Venue"),
                  "Wealth platform (MCP): sent as an order; the book moves when its confirmation syncs back")
            done = await ann.execute_orders(order_in)
            check("it is sent and waits, nothing settled", (done["orders_waiting"], done["orders_settled"], done["orders_failed"]),
                  (1, 0, 0))
            order = (await s.execute(select(Order).where(Order.firm_id == firm.id))).scalar_one()
            cmd = (await s.execute(select(FoundationCommand).where(FoundationCommand.firm_id == firm.id))).scalar_one()
            check("the order is placed at the platform under its id", (str(order.status), order.venue, order.external_ref),
                  ("placed", "foundation.mcp", "ORD-1"))
            check("a pending orders.place command carries the account's platform id",
                  (cmd.kind, cmd.status, cmd.payload["account_external_id"], cmd.payload["symbol"], cmd.payload["ack"]["status"]),
                  ("orders.place", "pending", "A1", "VTI", "accepted"))
            await s.refresh(vti)
            await s.refresh(acct)
            check("and the book has not moved", (float(vti.quantity), float(acct.cash_balance),
                  (await s.execute(select(func.count()).select_from(Execution))).scalar_one()), (100.0, 10000.0, 0))
            src = await ex(people["ola"]).read_foundation_sources({})
            pos = next(r for r in src["domains"] if r["domain"] == "positions")
            check("the overview counts it waiting, as figures", (pos["pending_commands"], src["commands"][0]["summary"]),
                  (1, "buy 10 VTI"))
            synced = {c["field"]: c["to"] for c in (await ola.sync_foundation_source({"domain": "positions"}))["changes"]}
            check("the platform's trade syncs back and confirms it", synced["confirmed"], 1)
            await s.refresh(order)
            await s.refresh(cmd)
            await s.refresh(vti)
            await s.refresh(acct)
            fills = (await s.execute(select(Execution).where(Execution.order_id == order.id))).scalars().all()
            check("the order is filled and settled, booked by the sync",
                  (str(order.status), str(order.settlement_status), [f.settled for f in fills], fills[0].lineage.get("booked_by")),
                  ("filled", "settled", [True], "sync"))
            check("the holding and cash moved only through the sync", (float(vti.quantity), float(acct.cash_balance), cmd.status),
                  (110.0, 7000.0, "confirmed"))
            check("no tax lot was made here", (await s.execute(select(func.count()).select_from(TaxLot))).scalar_one(), 0)
            trades = (await s.execute(select(Transaction).where(Transaction.firm_id == firm.id))).scalars().all()
            check("the one trade is the platform's", sorted((t.external_id or "") for t in trades), ["T-ORD-1", "T1"])
            again = {c["field"]: c["to"] for c in (await ola.sync_foundation_source({"domain": "activity"}))["changes"]}
            await s.refresh(order)
            check("a second sync books nothing twice", (again["created"], again["updated"], again["confirmed"],
                                                       float(order.filled_quantity)), (0, 0, 0, 10.0))

            sell_in = {"mandate_id": str(mandate.id), "orders": [{"symbol": "VTI", "side": "sell", "quantity": 5}],
                       "reason": "Trimming"}
            card = rows_of(await ann.preview_change("execute_orders", sell_in))
            check("a sale with no lots here is not refused; its gain is not guessed", "gain" in str(card.get("Order 1")), False)
            sold = await ann.execute_orders(sell_in)
            check("and it is sent", sold["orders_waiting"], 1)

            refuse_in = {"mandate_id": str(mandate.id), "orders": [{"symbol": "AAPL", "side": "buy", "quantity": 1}],
                         "reason": "A small position"}
            out = await ann.execute_orders(refuse_in)
            refused_order = (await s.execute(select(Order).where(Order.firm_id == firm.id, Order.external_ref.is_(None)))).scalar_one()
            check("a refusal at the platform rejects the order, naming why",
                  (out["orders_failed"], str(refused_order.status), "not tradable" in (refused_order.rejected_reason or "")),
                  (1, "rejected", True))
            failed = (await s.execute(select(FoundationCommand).where(FoundationCommand.status == "failed"))).scalars().all()
            check("its command is failed with the platform's words", ["not tradable" in (f.error or "") for f in failed], [True])

            server.commands = []
            await ola.sync_foundation_source({"domain": "positions"})
            await refused("a platform that takes no orders refuses the card, naming itself",
                          ann.preview_change("execute_orders", order_in), "takes no orders", "Wealth platform (MCP)")
            server.commands = ["orders.place"]
            await ola.sync_foundation_source({"domain": "positions"})
            split = Firm(id=uuid.uuid4(), name="Split", slug="split", base_currency="USD", jurisdiction="US",
                         settings=owners.with_owner({}, "positions", "foundation.mcp"))
            check("positions owned without the activity: nothing could come back, so nothing is sent",
                  "not its activity" in (route(split)[1] or ""), True)
            check("a native firm has no route", route(plain), (None, None))
    finally:
        mcp.MCPAdapter.transport = None
        mcp.register_mock_server(saved_mock)
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
