"""The simulated wealth platform's MCP server: the Foundation MCP profile v1 over Streamable HTTP, as a tiny ASGI app.

It is reached in process through ``httpx.ASGITransport`` (``install`` registers it as the MCP adapter's mock-mode
server), so it needs no network and nothing is mounted on Astra's app. It speaks the protocol a real server would:
``initialize`` with a session id, ``notifications/initialized`` answered 202, ``tools/list``, and ``tools/call`` of
``foundation_capabilities``, ``foundation_read`` (paged, answered as a server-sent event stream) and
``foundation_command``.

What a platform has, it keeps: the fixture's book, plus every order it accepted, filled at the firm's latest real price
when it took it (an order with no real price is refused, never filled on an invented one). Like the Dynamics 365 mock,
it remembers the orders it took through Astra's own record of what was sent (``foundation_command`` with the platform's
acknowledgement), which is the one shortcut a real server would not take. It values each holding at the firm's latest
price, as the firm's own valuation does, so a revaluation here and a read there agree.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.foundation.adapters.mcp import KEY, ORDERS_PLACE, PROFILE, PROTOCOL
from app.simulations.wealth_platform.fixture import CUSTODIAN, PLATFORM, book

PAGE = 25
TOOLS = [
    {"name": "foundation_capabilities", "title": "What this platform supplies",
     "description": "The Foundation MCP profile this server speaks, the domains it supplies, whether it keeps tax lots, "
                    "how many days of transactions it holds, and the commands it takes.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "foundation_read", "title": "Read a domain",
     "description": "One page of a domain (parties, accounts, positions, activity) as Foundation contract records, "
                    "and the cursor for the next page.",
     "inputSchema": {"type": "object", "required": ["domain"], "properties": {
         "domain": {"type": "string", "enum": ["parties", "accounts", "positions", "activity"]},
         "cursor": {"type": "string"}, "contract_version": {"type": "string"}}}},
    {"name": "foundation_command", "title": "Take a command",
     "description": "A command from the firm's agents (orders.place). Returns the platform's id for it; what happens "
                    "comes back on a later foundation_read.",
     "inputSchema": {"type": "object", "required": ["kind", "payload", "idempotency_key"], "properties": {
         "kind": {"type": "string", "enum": [ORDERS_PLACE]}, "payload": {"type": "object"},
         "idempotency_key": {"type": "string"}}}},
]


class Refused(Exception):
    """A tool call the platform refuses; the message is what it says."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class PlatformServer:
    """One server instance per connection, bound to the firm it serves and the session it reads prices through."""

    def __init__(self, session, firm):
        self.session, self.firm = session, firm
        self.sessions: set[str] = set()

    # ── The platform's state ──

    async def _prices(self) -> dict[str, tuple[float, bool]]:
        """symbol -> (latest close, is real), from the firm's own feed: the same price its valuation uses."""
        from app.aurea_core.valuation import latest_prices
        from app.models.portfolio import Instrument

        latest = await latest_prices(self.session, self.firm.id)
        symbols = {i.id: i.symbol for i in (await self.session.execute(
            select(Instrument).where(Instrument.firm_id == self.firm.id))).scalars().all()}
        return {symbols[iid]: (float(p.close), bool(p.is_real)) for iid, p in latest.items() if iid in symbols}

    async def _real_price(self, symbol: str):
        from app.models.portfolio import Instrument, Price

        return (await self.session.execute(
            select(Price).join(Instrument, Instrument.id == Price.instrument_id).where(
                Instrument.firm_id == self.firm.id, Instrument.symbol == symbol, Price.is_real.is_(True))
            .order_by(Price.as_of.desc()).limit(1))).scalars().first()

    async def _accepted(self) -> list:
        """The orders this platform accepted, in the order it took them."""
        from app.models.foundation import FoundationCommand

        rows = (await self.session.execute(select(FoundationCommand).where(
            FoundationCommand.firm_id == self.firm.id, FoundationCommand.source == KEY,
            FoundationCommand.kind == ORDERS_PLACE, FoundationCommand.external_id.is_not(None))
            .order_by(FoundationCommand.created_at, FoundationCommand.id))).scalars().all()
        return [r for r in rows if ((r.payload or {}).get("ack") or {}).get("status") == "accepted"]

    async def state(self) -> dict:
        data = book()
        holdings = {(h["account_ref"], h["symbol"]): h for h in data["positions"]}
        accounts = {a["external_id"]: a for a in data["accounts"] if a["entity"] == "account"}
        for cmd in await self._accepted():
            p, ack = cmd.payload or {}, (cmd.payload or {}).get("ack") or {}
            self._apply(data, holdings, accounts, p, ack, cmd.external_id)
        prices = await self._prices()
        for h in data["positions"]:
            close = prices.get(h["symbol"])
            if close is not None:
                h["market_value"] = float(h["quantity"]) * close[0]
                h["confidence"] = 0.99 if close[1] else 0.85
        return data

    @staticmethod
    def _apply(data, holdings, accounts, p, ack, ext) -> None:
        acct, sym, qty = p["account_external_id"], p["symbol"], float(p["quantity"])
        px, at = float(ack["fill_price"]), ack.get("received_at") or _now()
        gross = qty * px
        account = accounts[acct]
        h = holdings.get((acct, sym))
        if p["side"] == "buy":
            if h is None:
                h = {"entity": "holding", "external_id": f"WS-H-{acct}-{sym}", "account_ref": acct, "symbol": sym,
                     "quantity": 0.0, "cost_basis": 0.0}
                holdings[(acct, sym)] = h
                data["positions"].append(h)
            h["quantity"] = float(h["quantity"]) + qty
            h["cost_basis"] = round(float(h["cost_basis"]) + gross, 2)
            account["cash_balance"] = round(float(account["cash_balance"]) - gross, 2)
        else:
            held = float(h["quantity"])
            h["cost_basis"] = round(float(h["cost_basis"]) * (held - qty) / held, 2) if held else 0.0
            h["quantity"] = held - qty
            account["cash_balance"] = round(float(account["cash_balance"]) + gross, 2)
        h["as_of"] = account["as_of"] = at
        data["activity"].append({
            "entity": "txn", "external_id": f"WS-T-{ext}", "as_of": at, "account_ref": acct, "symbol": sym,
            "txn_type": p["side"], "quantity": qty, "price": px, "amount": round(gross, 2), "trade_date": at[:10],
            "lineage": {"order_ref": ext, "platform": PLATFORM["name"], "fees": 0, "price_source": ack.get("price_source"),
                        "price_as_of": ack.get("price_as_of"), "reaches_market": False, "simulated": True}})

    # ── The tools ──

    async def capabilities(self, _args: dict) -> dict:
        return {"profile": PROFILE, "contract_versions": ["1"], "domains": ["parties", "accounts", "positions", "activity"],
                "capabilities": {"tax_lots": False, "transactions_days": 365, "events": False},
                "commands": [ORDERS_PLACE], "platform": dict(PLATFORM), "simulated": True}

    async def read(self, args: dict) -> dict:
        domain = str(args.get("domain") or "")
        if domain not in ("parties", "accounts", "positions", "activity"):
            raise Refused(f"No domain {domain or '(none)'} here.")
        if str(args.get("contract_version") or "1") != "1":
            raise Refused("This platform writes contract version 1.")
        rows = (await self.state())[domain]
        start = int(args.get("cursor") or 0)
        page = rows[start:start + PAGE]
        nxt = start + PAGE if start + PAGE < len(rows) else None
        return {"domain": domain, "contract_version": "1", "records": copy.deepcopy(page),
                "next_cursor": str(nxt) if nxt is not None else None, "simulated": True}

    async def command(self, args: dict) -> dict:
        if args.get("kind") != ORDERS_PLACE:
            raise Refused(f"This platform takes no {args.get('kind')}.")
        p = dict(args.get("payload") or {})
        key = str(args.get("idempotency_key") or "")
        ext = "WS-ORD-" + uuid.uuid5(uuid.NAMESPACE_URL, f"wealthsmart-sim:{key or uuid.uuid4()}").hex[:12].upper()
        for cmd in await self._accepted():   # asked again: the same answer
            if cmd.external_id == ext:
                return dict((cmd.payload or {}).get("ack") or {})
        data = await self.state()
        accounts = {a["external_id"]: a for a in data["accounts"] if a["entity"] == "account"}
        acct, sym, side = p.get("account_external_id"), str(p.get("symbol") or ""), p.get("side")
        try:
            qty = float(p.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        if acct not in accounts:
            raise Refused(f"There is no account {acct} on this platform.")
        if side not in ("buy", "sell") or qty <= 0:
            raise Refused("An order needs a side (buy or sell) and a quantity above nothing.")
        price = await self._real_price(sym)
        if price is None:
            raise Refused(f"No real price for {sym} today, so the order cannot fill. Nothing is filled on an invented price.")
        px = float(price.close)
        if p.get("limit_price") is not None:
            limit = float(p["limit_price"])
            if (side == "buy" and px > limit) or (side == "sell" and px < limit):
                raise Refused(f"{sym} is at {px:.2f}, outside the limit of {limit:.2f}: not filled.")
            px = limit
        held = next((float(h["quantity"]) for h in data["positions"] if h["account_ref"] == acct and h["symbol"] == sym), 0.0)
        if side == "sell" and qty > held + 1e-9:
            raise Refused(f"The account holds {held:g} {sym}; it cannot sell {qty:g}.")
        if side == "buy" and qty * px > float(accounts[acct]["cash_balance"]) + 1e-6:
            raise Refused(f"Buying {qty:g} {sym} at {px:.2f} costs more than the {accounts[acct]['cash_balance']:.2f} "
                          f"cash in the account.")
        return {"status": "accepted", "external_id": ext, "received_at": _now(), "fill_price": px,
                "price_as_of": price.as_of.isoformat(), "price_source": price.source,
                "note": f"Filled at the latest real close; {CUSTODIAN} booked it.", "simulated": True}

    # ── The wire: JSON-RPC 2.0 over Streamable HTTP ──

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
        if scope["method"] == "DELETE":
            self.sessions.discard(headers.get("mcp-session-id", ""))
            return await self._reply(send, 204, b"")
        if scope["method"] != "POST":
            return await self._reply(send, 405, b"")
        try:
            req = json.loads(body or b"{}")
        except ValueError:
            return await self._json(send, 400, {"jsonrpc": "2.0", "id": None,
                                                "error": {"code": -32700, "message": "Parse error"}})
        if "id" not in req:   # a notification
            return await self._reply(send, 202, b"")
        rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        if method == "initialize":
            sid = uuid.uuid4().hex
            self.sessions.add(sid)
            return await self._json(send, 200, {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": PLATFORM["name"], "version": PLATFORM["version"]},
                "instructions": "A simulated wealth platform speaking the Foundation MCP profile v1."}},
                extra=[(b"mcp-session-id", sid.encode())])
        if headers.get("mcp-session-id") not in self.sessions:
            return await self._reply(send, 404, b"")
        if method == "tools/list":
            return await self._json(send, 200, {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            handler = {"foundation_capabilities": self.capabilities, "foundation_read": self.read,
                       "foundation_command": self.command}.get(name)
            if handler is None:
                return await self._json(send, 200, {"jsonrpc": "2.0", "id": rid,
                                                    "error": {"code": -32602, "message": f"Unknown tool: {name}"}})
            try:
                out = await handler(args)
                result = {"content": [{"type": "text", "text": json.dumps(out)}], "structuredContent": out,
                          "isError": False}
            except Refused as exc:
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            message = {"jsonrpc": "2.0", "id": rid, "result": result}
            if name == "foundation_read" and "text/event-stream" in headers.get("accept", ""):
                return await self._sse(send, message)
            return await self._json(send, 200, message)
        return await self._json(send, 200, {"jsonrpc": "2.0", "id": rid,
                                            "error": {"code": -32601, "message": f"Method not found: {method}"}})

    @staticmethod
    async def _reply(send, status: int, body: bytes, ctype: bytes = b"application/json", extra=None) -> None:
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", ctype)] + list(extra or [])})
        await send({"type": "http.response.body", "body": body})

    async def _json(self, send, status: int, payload: dict, extra=None) -> None:
        await self._reply(send, status, json.dumps(payload).encode(), extra=extra)

    async def _sse(self, send, payload: dict) -> None:
        await self._reply(send, 200, f"event: message\ndata: {json.dumps(payload)}\n\n".encode(),
                          ctype=b"text/event-stream")


def transport(session, firm, connector):
    """The adapter's mock-mode server for this firm: an in-process transport to a fresh server instance."""
    import httpx

    return httpx.ASGITransport(app=PlatformServer(session, firm))
