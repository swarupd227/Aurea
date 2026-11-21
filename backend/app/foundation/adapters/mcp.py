"""A wealth platform as the Foundation, reached through MCP (B-164, B-165; docs/03_Architecture/foundation-mcp-profile.md).

The client's target architecture: their wealth platform owns parties, accounts, positions and activity, and Astra's
agents reach it through the Model Context Protocol. This adapter is an MCP client, written to the wire protocol itself
(JSON-RPC 2.0 over Streamable HTTP, protocol 2025-06-18) so it adds no dependency: ``initialize``, then
``notifications/initialized``, ``tools/list`` and ``tools/call``, each answered as JSON or as a server-sent event
stream, with the ``Mcp-Session-Id`` the server gives carried on every later request.

What it asks the server for is the Foundation MCP profile v1, three tools:

    foundation_capabilities   what the platform supplies: domains, tax lots, history, the commands it takes
    foundation_read           one page of a domain as contract records, and the cursor for the next
    foundation_command        a command (``orders.place``) and the platform's id for it

Its capabilities are what the server declares, recorded on the firm at each read (``settings["foundation_declared"]``)
so a module asking "are there tax lots?" reads the platform's own answer; before the first read they are the floor
(nothing claimed). Records arrive as contract records and go through the same sync as every adapter.

Live mode (``use_mock`` off) calls the connector's ``base_url`` with its auth header; nothing secret is put in a message.
Mock mode calls a server registered at ``register_mock_server`` (a simulation's, in process) and is refused when none
is. A test hands the live client a transport through ``transport``, as the Dynamics 365 adapter's tests do.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import ValidationError

from app.foundation import contract as c
from app.foundation.adapters.base import Adapter, AdapterError, Pulled

KEY = "foundation.mcp"
PROFILE = "foundation-mcp/1"
PROTOCOL = "2025-06-18"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26")
TOOLS = ("foundation_capabilities", "foundation_read", "foundation_command")
ORDERS_PLACE = "orders.place"
DECLARED = "foundation_declared"      # firm.settings key: what each source's server last declared
MAX_PAGES = 500                       # a server that never ends its cursor is stopped, not followed forever
CLIENT_INFO = {"name": "Astra for Wealth", "version": "1"}
# Mock mode's server (a simulation's, reached in process): a callable (session, firm, connector) -> transport.
_MOCK_SERVER: dict[str, Callable | None] = {"factory": None}


class MCPError(AdapterError):
    """The server refused, could not be reached, or did not speak the profile. Names what failed, never a secret."""


# ── The wire: JSON-RPC 2.0 over Streamable HTTP ───────────────────────────────────────────────────

def _sse_messages(text: str) -> list[dict]:
    """The JSON-RPC messages in a server-sent event stream: each event's data lines, joined."""
    out, data = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif line.strip() == "" and data:
            try:
                msg = json.loads("\n".join(data))
            except ValueError:
                msg = None
            if isinstance(msg, list):
                out.extend(m for m in msg if isinstance(m, dict))
            elif isinstance(msg, dict):
                out.append(msg)
            data = []
    return out


class MCPClient:
    """One MCP session with one server. Use as ``async with MCPClient(url, ...) as mcp``."""

    def __init__(self, url: str, *, headers: dict | None = None, transport=None, timeout: float = 30):
        self.url = url
        self._headers = dict(headers or {})
        self.transport = transport
        self.timeout = timeout
        self.session_id: str | None = None
        self.protocol: str | None = None
        self.server: dict = {}
        self._id = 0
        self._http = None

    async def __aenter__(self) -> "MCPClient":
        import httpx

        self._http = httpx.AsyncClient(timeout=self.timeout, transport=self.transport)
        try:
            await self.initialize()
        except BaseException:
            await self._http.aclose()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            if self.session_id:   # ending the session is a courtesy; a server that refuses it is not an error
                try:
                    await self._http.delete(self.url, headers=self._request_headers())
                except Exception:
                    pass
        finally:
            await self._http.aclose()

    def _request_headers(self) -> dict:
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **self._headers}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol:
            h["MCP-Protocol-Version"] = self.protocol
        return h

    async def _post(self, body: dict):
        import httpx

        try:
            return await self._http.post(self.url, content=json.dumps(body), headers=self._request_headers())
        except httpx.HTTPError as exc:
            raise MCPError(f"The MCP server could not be reached ({type(exc).__name__}).") from None

    async def request(self, method: str, params: dict | None = None) -> dict:
        """Send one request and return its result; a JSON-RPC error is raised with the server's message."""
        self._id += 1
        rid = self._id
        body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        r = await self._post(body)
        if r.status_code == 404 and self.session_id and method != "initialize":
            # The server ended the session: start a new one once, then ask again.
            self.session_id = self.protocol = None
            await self.initialize()
            r = await self._post(body)
        if r.status_code in (401, 403):
            raise MCPError(f"The MCP server refused the credentials ({r.status_code}).")
        if r.status_code >= 400:
            raise MCPError(f"The MCP server refused {method} ({r.status_code}).")
        if method == "initialize" and r.headers.get("mcp-session-id"):
            self.session_id = r.headers["mcp-session-id"]
        ctype = (r.headers.get("content-type") or "").lower()
        if "text/event-stream" in ctype:
            messages = _sse_messages(r.text)
        else:
            try:
                parsed = r.json()
            except ValueError:
                raise MCPError(f"The MCP server's answer to {method} was not JSON.") from None
            messages = parsed if isinstance(parsed, list) else [parsed]
        reply = next((m for m in messages if isinstance(m, dict) and m.get("id") == rid
                      and ("result" in m or "error" in m)), None)
        if reply is None:
            raise MCPError(f"The MCP server gave no answer to {method}.")
        if "error" in reply:
            err = reply.get("error") or {}
            raise MCPError(f"The MCP server refused {method}: {str(err.get('message') or 'no reason given')[:200]}")
        return reply.get("result") or {}

    async def notify(self, method: str, params: dict | None = None) -> None:
        body = {"jsonrpc": "2.0", "method": method}
        if params:
            body["params"] = params
        r = await self._post(body)
        if r.status_code >= 400:
            raise MCPError(f"The MCP server refused {method} ({r.status_code}).")

    async def initialize(self) -> dict:
        result = await self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                   "clientInfo": CLIENT_INFO})
        version = str(result.get("protocolVersion") or "")
        if version not in SUPPORTED_PROTOCOLS:
            raise MCPError(f"The MCP server speaks protocol {version or 'unknown'}; this client speaks "
                           f"{', '.join(SUPPORTED_PROTOCOLS)}.")
        self.protocol = version
        self.server = dict(result.get("serverInfo") or {})
        await self.notify("notifications/initialized")
        return result

    async def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        for _ in range(MAX_PAGES):
            result = await self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(t for t in result.get("tools") or [] if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """A tool's answer: its structured content, else its first text block read as JSON. A tool error is raised
        with the server's own words."""
        result = await self.request("tools/call", {"name": name, "arguments": arguments})
        texts = [b.get("text") for b in result.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"]
        if result.get("isError"):
            raise MCPError(f"{name}: {str(texts[0] if texts else 'refused, with no reason given')[:300]}")
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        for t in texts:
            try:
                return json.loads(t)
            except (TypeError, ValueError):
                continue
        raise MCPError(f"{name} answered with nothing this client can read.")


# ── The profile's answers as contract records ─────────────────────────────────────────────────────

def to_records(domain: str, rows: list, *, version: str = c.CONTRACT_VERSION) -> Pulled:
    """A page of a domain as contract records. A row of another domain, an entity the contract does not have, or one
    that does not fit the contract is skipped and named, never guessed."""
    out = Pulled()
    allowed = {cls.entity: cls for cls in c.DOMAINS.get(domain, ())}
    for row in rows or []:
        if not isinstance(row, dict):
            out.skipped.append(f"{domain}: a row that is not a record")
            continue
        entity, ext = str(row.get("entity") or ""), str(row.get("external_id") or "")
        cls = allowed.get(entity)
        if cls is None:
            out.skipped.append(f"{entity or 'record'} {ext}: not a {domain} record in contract v{version}")
            continue
        data = {k: v for k, v in row.items() if k not in ("entity", "source", "contract_version")}
        try:
            out.records.append(cls(source=KEY, contract_version=version, **data))
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            where = ".".join(str(p) for p in first.get("loc") or ()) or "record"
            out.skipped.append(f"{entity} {ext}: does not fit contract v{version} ({where}: {first.get('msg', 'invalid')})")
    return out


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class MCPAdapter(Adapter):
    key = KEY
    name = "Wealth platform (MCP)"
    domains = frozenset({"parties", "accounts", "positions", "activity"})
    # The floor until the server declares its own: nothing is claimed that the platform has not said it supplies.
    capabilities = {"tax_lots": False, "transactions_days": False, "events": False}
    commands = frozenset({ORDERS_PLACE})
    # A test hands the live client a transport; nothing else sets it.
    transport = None

    # ── What the server declared ──

    def declared(self, firm) -> dict:
        return dict((((getattr(firm, "settings", None) or {}).get(DECLARED) or {}).get(self.key)) or {})

    def capabilities_for(self, firm) -> dict:
        got = self.declared(firm).get("capabilities")
        if not isinstance(got, dict):
            return dict(self.capabilities)
        days = got["transactions_days"] if "transactions_days" in got else False   # unsaid is none, not all history
        return {"tax_lots": bool(got.get("tax_lots")),
                "transactions_days": days if days is None or days is False else int(days),
                "events": bool(got.get("events"))}

    def commands_for(self, firm) -> frozenset[str]:
        said = self.declared(firm).get("commands")
        return self.commands if not isinstance(said, list) else frozenset(k for k in said if k in self.commands)

    def _record_declared(self, firm, said: dict, server: dict) -> None:
        from sqlalchemy.orm.attributes import flag_modified

        entry = {"profile": said.get("profile"), "contract_versions": list(said.get("contract_versions") or []),
                 "domains": [d for d in said.get("domains") or [] if d in self.domains],
                 "capabilities": dict(said.get("capabilities") or {}),
                 "commands": [k for k in said.get("commands") or [] if isinstance(k, str)],
                 "platform": dict(said.get("platform") or {}) or {"name": server.get("name"), "version": server.get("version")},
                 "simulated": bool(said.get("simulated")), "read_at": _now()}
        settings = dict(firm.settings or {})
        known = dict(settings.get(DECLARED) or {})
        before = dict(known.get(self.key) or {})
        if {k: v for k, v in before.items() if k != "read_at"} == {k: v for k, v in entry.items() if k != "read_at"}:
            return   # unchanged: the firm's settings are not rewritten on every read
        known[self.key] = entry
        settings[DECLARED] = known
        firm.settings = settings
        flag_modified(firm, "settings")

    # ── Reaching the server ──

    def _client(self, session, firm, connector) -> MCPClient:
        cfg = (connector.config if connector is not None else None) or {}
        if connector is None or connector.use_mock:
            if _MOCK_SERVER["factory"] is None:
                raise MCPError("No MCP server is set up for mock mode. Give the platform's server URL and switch the "
                               "connector live.")
            return MCPClient("http://foundation.mcp.mock/mcp", transport=_MOCK_SERVER["factory"](session, firm, connector))
        url = str(cfg.get("base_url") or "").strip()
        if not url:
            raise MCPError("The MCP server URL is not set on the connector.")
        if not url.startswith("https://"):
            raise MCPError("The MCP server URL must be https.")
        headers = {}
        value = str(cfg.get("auth_value") or "")
        if value:
            headers[str(cfg.get("auth_header") or "Authorization").strip() or "Authorization"] = value
        return MCPClient(url, headers=headers, transport=self.transport)

    async def _profile(self, mcp: MCPClient, firm) -> dict:
        names = {t.get("name") for t in await mcp.list_tools()}
        missing = [t for t in TOOLS if t not in names]
        if missing:
            raise MCPError(f"The MCP server does not speak the Foundation profile: it has no {', '.join(missing)}.")
        said = await mcp.call_tool("foundation_capabilities", {})
        if not isinstance(said, dict) or str(said.get("profile") or "") != PROFILE:
            raise MCPError(f"The MCP server's profile is {said.get('profile') if isinstance(said, dict) else 'unreadable'}, "
                           f"not {PROFILE}.")
        if c.CONTRACT_VERSION not in [str(v) for v in said.get("contract_versions") or []]:
            raise MCPError(f"The MCP server does not write contract v{c.CONTRACT_VERSION}.")
        self._record_declared(firm, said, mcp.server)
        return said

    async def describe(self, session, firm, connector) -> dict:
        """Ask the server what it supplies, and record it. For a connection test."""
        async with self._client(session, firm, connector) as mcp:
            return await self._profile(mcp, firm)

    async def pull(self, session, firm, connector, domain: str) -> Pulled:
        if domain not in self.domains:
            raise MCPError(f"The wealth platform supplies parties, accounts, positions and activity, not {domain}.")
        async with self._client(session, firm, connector) as mcp:
            said = await self._profile(mcp, firm)
            if domain not in (said.get("domains") or []):
                raise MCPError(f"The platform's MCP server does not supply {domain}.")
            out = Pulled(mode="mock" if connector is None or connector.use_mock else "live")
            cursor = None
            for _ in range(MAX_PAGES):
                args = {"domain": domain, "contract_version": c.CONTRACT_VERSION}
                if cursor:
                    args["cursor"] = cursor
                page = await mcp.call_tool("foundation_read", args)
                if not isinstance(page, dict):
                    raise MCPError(f"foundation_read gave no page of {domain}.")
                got = to_records(domain, page.get("records") or [])
                out.records.extend(got.records)
                out.skipped.extend(got.skipped)
                cursor = page.get("next_cursor")
                if not cursor:
                    break
            else:
                raise MCPError(f"foundation_read of {domain} did not end after {MAX_PAGES} pages.")
        return out

    async def send(self, session, firm, connector, command) -> None:
        """Deliver a command. The platform answers with its own id for it; the copy changes only when the platform's
        record of what happened comes back through a sync."""
        if command.kind not in self.commands:
            raise MCPError(f"The wealth platform takes no {command.kind} from Astra.")
        async with self._client(session, firm, connector) as mcp:
            said = await self._profile(mcp, firm)
            if command.kind not in (said.get("commands") or []):
                raise MCPError(f"The platform's MCP server takes no {command.kind}.")
            ack = await mcp.call_tool("foundation_command", {"kind": command.kind, "payload": command.payload or {},
                                                             "idempotency_key": str(command.id)})
        ext = str((ack or {}).get("external_id") or "") if isinstance(ack, dict) else ""
        if not ext:
            raise MCPError(f"The platform took the {command.kind} without giving its id for it.")
        command.external_id = ext[:128]
        # What the platform said when it took it (its own status and figures), kept beside what was asked.
        command.payload = {**(command.payload or {}),
                           "ack": {k: v for k, v in ack.items() if k in ("status", "external_id", "received_at", "note",
                                                                          "fill_price", "price_as_of", "simulated")}}


def register_mock_server(factory: Callable | None) -> None:
    """The in-process server mock mode reaches: ``factory(session, firm, connector)`` returns an httpx transport. A
    simulation registers one; None takes it away."""
    _MOCK_SERVER["factory"] = factory
