"""A simulated wealth platform, reached through MCP (B-221). Stands for the client's WealthSmart.

The real part is elsewhere and stays: ``app/foundation/adapters/mcp.py`` is an MCP client that reads the Foundation
MCP profile v1 from any server speaking it, and sends orders as commands (``app/foundation/orders.py``). This package
is the server and its data, and is thrown away when WealthSmart exposes its own MCP server: the connector is then
switched live with that server's URL, and nothing else changes.

  - ``install`` registers the in-process server as the MCP adapter's mock-mode server (no database work).
  - ``firms`` returns the simulated demonstration firm, creating it if needed: a US firm marked ``simulated_firm``,
    its staff (reachable from the login page through the firm's ``personas``, a seam core reads from any firm's
    settings), the instruments and models the platform's book names, and the live market-data connector, so prices
    are real. It holds no client data of its own.
  - ``seed`` connects a firm to the platform: a mock-mode MCP connector tagged ``simulated``, the firm's parties,
    accounts, positions and activity owned by it, and a first sync. On the simulated firm it also records the adviser
    of each household the platform brought, tagged, since the contract does not carry one.
  - ``discard`` removes the simulated firm and every row in it; on any other firm it removes what the platform synced,
    its connector and its tagged rows, and hands the domains back to Astra.
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm.attributes import flag_modified

from app.simulations import Simulation, enabled_for, opt_in, opt_out, register, tag

KEY = "wealth_platform"
TAG = tag(KEY)
SLUG = "platform-sim"
OWNED = ("parties", "accounts", "positions", "activity")
DOMAIN = "platform-sim.demo"
PERSONAS = [
    {"email": f"adviser@{DOMAIN}", "role": "adviser", "full_name": "Rachel Moreno", "title": "Senior Adviser",
     "default_path": "/studio", "group": "Advice", "description": "Advises the platform's households; orders go to the platform."},
    {"email": f"portfolio@{DOMAIN}", "role": "portfolio_team", "full_name": "Daniel Kwan", "title": "Portfolio Manager",
     "default_path": "/studio/review", "group": "Investment", "description": "Discretionary mandates on the platform."},
    {"email": f"compliance@{DOMAIN}", "role": "compliance", "full_name": "Priya Raman", "title": "Head of Compliance",
     "default_path": "/provenance", "group": "Risk", "description": "Supervision over a book the platform owns."},
    {"email": f"operations@{DOMAIN}", "role": "operations", "full_name": "Marcus Bell", "title": "Operations Lead",
     "default_path": "/studio/onboarding", "group": "Operations", "description": "Syncs, breaks and orders waiting on the platform."},
    {"email": f"admin@{DOMAIN}", "role": "admin", "full_name": "Hannah Lee", "title": "Platform Admin",
     "default_path": "/admin", "group": "Platform", "description": "Which system owns each domain."},
]


def install() -> None:
    from app.foundation.adapters.mcp import register_mock_server
    from app.simulations.wealth_platform.server import transport

    register_mock_server(transport)


def is_simulated_firm(firm) -> bool:
    return ((getattr(firm, "settings", None) or {}).get("simulated_firm")) == KEY


# ── The simulated demonstration firm ───────────────────────────────────────────────────────────────

async def ensure_firm(session, *, fetch_prices: bool = True):
    """The simulated firm, created if it is not there. Everything in it goes when it is discarded."""
    from app.agents import schedules as agent_schedules
    from app.agents.catalogue import CATALOGUE
    from app.core.security import hash_password
    from app.models.connectors import Connector
    from app.models.enums import ConnectorDomain, ConnectorStatus
    from app.models.identity import User
    from app.models.portfolio import Instrument, ModelPortfolio, TargetAllocation
    from app.models.tenant import AgentConfig, Firm
    from app.simulations.wealth_platform.fixture import INSTRUMENTS, MODELS

    firm = (await session.execute(select(Firm).where(Firm.slug == SLUG))).scalar_one_or_none()
    if firm is not None:
        return firm
    firm = Firm(slug=SLUG, name="Wealth platform demonstration (simulated)",
                legal_name="Wealth platform demonstration firm (simulated platform, invented clients)",
                jurisdiction="US", regulator="SEC", base_currency="USD",
                branding={"primary": "#163a52", "accent": "#c8a35e", "logo_text": "Astra for Wealth",
                          "tagline": "On a simulated wealth platform"},
                settings={"timezone": "America/New_York", "demonstration": True, "simulated_firm": KEY,
                          "personas": PERSONAS, "persona_label": "Wealth platform · simulated, invented clients",
                          "execution": {"fee_bps": 0, "fee_minimum": 0}},
                model_config_json={})
    session.add(firm)
    await session.flush()
    pw = hash_password("aurea")
    for p in PERSONAS:
        session.add(User(firm_id=firm.id, email=p["email"], full_name=p["full_name"], hashed_password=pw, role=p["role"],
                         title=p["title"], is_active=True))
    for key, meta in CATALOGUE.items():
        cron, _why = agent_schedules.DEFAULT_SCHEDULES.get(key, (None, None))
        # Run by hand: a simulated book does not take the shared worker's scheduled model calls.
        session.add(AgentConfig(firm_id=firm.id, agent_key=key, enabled=True, default_tier=meta["default_tier"],
                                schedule_cron=cron, schedule_enabled=False))
    instruments = {}
    for sym, (name, cls) in INSTRUMENTS.items():
        inst = Instrument(firm_id=firm.id, symbol=sym, name=name, asset_class=cls, market_type="public", currency="USD",
                          market_symbol=sym, values_tags={})
        session.add(inst)
        instruments[sym] = inst
    await session.flush()
    for name, (description, targets) in MODELS.items():
        model = ModelPortfolio(firm_id=firm.id, name=name, description=description, drift_band=0.05)
        session.add(model)
        await session.flush()
        for cls, weight, sym in targets:
            session.add(TargetAllocation(firm_id=firm.id, model_id=model.id, asset_class=cls, target_weight=weight,
                                         instrument_id=instruments[sym].id))
    # Prices are the firm's own real feed: the platform supplies the book, never a price.
    session.add(Connector(firm_id=firm.id, domain=ConnectorDomain.MARKET_RESEARCH_DATA, provider_key="marketdata.yahoo",
                          display_name="Yahoo Finance Market Data (Live)", status=ConnectorStatus.CONNECTED,
                          use_mock=False, sync_cron="0 */1 * * *", config={}))
    await session.flush()
    if fetch_prices:
        from app.conduit.service import sync_market_data

        try:
            async with session.begin_nested():
                await sync_market_data(session, firm.id)
        except Exception:   # offline: no price, so nothing fills and nothing is valued; never an invented one
            pass
    return firm


async def firms(session) -> list:
    return [await ensure_firm(session)]


# ── Seed: a firm connected to the platform ─────────────────────────────────────────────────────────

async def _connector(session, firm):
    from app.foundation.adapters.mcp import KEY as MCP
    from app.models.connectors import Connector
    from app.models.enums import ConnectorDomain, ConnectorStatus

    rows = (await session.execute(select(Connector).where(Connector.firm_id == firm.id, Connector.provider_key == MCP))
            ).scalars().all()
    mine = [r for r in rows if (r.config or {}).get("simulated") == KEY]
    if mine:
        return mine[0], False
    c = Connector(firm_id=firm.id, domain=ConnectorDomain.CUSTODY, provider_key=MCP,
                  display_name="Wealth platform (simulated, MCP)", status=ConnectorStatus.MOCK, use_mock=True,
                  sync_cron="0 */6 * * *", config={"simulated": KEY})
    session.add(c)
    await session.flush()
    return c, True


async def seed(session, firm) -> dict:
    from app.conduit.service import run_sync
    from app.foundation import owners
    from app.foundation.adapters.mcp import KEY as MCP
    from app.models.connectors import ConnectorSync
    from app.models.graph import Household, RelationshipEdge
    from app.models.identity import User

    install()   # the first sync needs the server, also when seeding from the command line
    await opt_in(session, firm, KEY)
    connector, made = await _connector(session, firm)
    changed = 0
    for d in OWNED:
        if owners.owner(firm, d) != MCP:
            firm.settings = owners.with_owner(firm.settings, d, MCP)
            changed += 1
    if changed:
        flag_modified(firm, "settings")
        await session.flush()
    synced = 0
    first = (await session.execute(select(ConnectorSync.id).where(ConnectorSync.connector_id == connector.id))).first()
    if first is None:
        sync = await run_sync(session, connector)
        synced = int(sync.records_ingested or 0)
    edges = 0
    if is_simulated_firm(firm):
        adviser = (await session.execute(select(User).where(User.firm_id == firm.id, User.email == PERSONAS[0]["email"])
                                         )).scalar_one_or_none()
        if adviser is not None:
            have = {e.to_id for e in (await session.execute(select(RelationshipEdge).where(
                RelationshipEdge.firm_id == firm.id, RelationshipEdge.kind == "adviser"))).scalars().all()}
            for h in (await session.execute(select(Household).where(
                    Household.firm_id == firm.id, Household.source_system == MCP))).scalars().all():
                if h.id not in have:
                    session.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=adviser.id,
                                                 to_type="household", to_id=h.id,
                                                 attributes={"source": TAG, "recorded_by": "the simulation's seed"}))
                    edges += 1
        await session.flush()
    return {"connector": int(made), "owners_set": changed, "records_synced": synced, "adviser_edges": edges}


# ── Discard ─────────────────────────────────────────────────────────────────────────────────────

async def purge_firm(session, firm) -> int:
    """Every row of a firm, then the firm. Children first; rows with no firm column go with what they hang off."""
    import app.models  # noqa: F401  - every table on the metadata
    from app.core.db import Base

    await session.flush()
    fid = firm.id
    tables = [t for t in Base.metadata.sorted_tables if t.name != "firm"]
    doomed: dict[str, set] = {}
    for t in tables:
        if "firm_id" in t.c and "id" in t.c:
            doomed[t.name] = set((await session.execute(select(t.c.id).where(t.c.firm_id == fid))).scalars().all())
    for t in tables:
        if "firm_id" in t.c or "id" not in t.c:
            continue
        for fk in t.foreign_keys:
            parents = doomed.get(fk.column.table.name)
            if parents and fk.column.name == "id":
                doomed.setdefault(t.name, set()).update(
                    (await session.execute(select(t.c.id).where(fk.parent.in_(list(parents))))).scalars().all())
    n = 0
    for t in reversed(tables):
        if "firm_id" in t.c:
            n += (await session.execute(delete(t).where(t.c.firm_id == fid))).rowcount or 0
        elif doomed.get(t.name):
            n += (await session.execute(delete(t).where(t.c.id.in_(list(doomed[t.name]))))).rowcount or 0
    for obj in list(session.identity_map.values()):
        if obj is firm or getattr(obj, "firm_id", None) == fid:
            session.expunge(obj)
    await session.execute(delete(type(firm).__table__).where(type(firm).__table__.c.id == fid))
    return n + 1


async def discard(session, firm) -> dict:
    from app.foundation import contract as c
    from app.foundation import owners
    from app.foundation.adapters.mcp import DECLARED, KEY as MCP
    from app.foundation.mapping import SPECS
    from app.models.connectors import Connector, ConnectorSync
    from app.models.foundation import ExternalRef, FoundationBreak, FoundationCommand
    from app.models.graph import RelationshipEdge
    from app.models.trading import Execution, Order

    if is_simulated_firm(firm):
        return {"firm_rows": await purge_firm(session, firm)}
    mine = [r for r in (await session.execute(select(Connector).where(
        Connector.firm_id == firm.id, Connector.provider_key == MCP))).scalars().all()
        if (r.config or {}).get("simulated") == KEY]
    if not mine and not enabled_for(firm, KEY):
        return {"records": 0, "connectors": 0}
    out = {"records": 0, "connectors": len(mine), "commands": 0, "edges": 0}
    # Orders sent to the platform, and what they became here.
    orders = (await session.execute(select(Order).where(Order.firm_id == firm.id, Order.venue == MCP))).scalars().all()
    for o in orders:
        await session.execute(delete(Execution).where(Execution.order_id == o.id))
        await session.delete(o)
    # What the platform synced, children first.
    refs = (await session.execute(select(ExternalRef).where(ExternalRef.firm_id == firm.id, ExternalRef.source == MCP))
            ).scalars().all()
    order = [cls.entity for d in reversed(c.DOMAIN_ORDER) for cls in reversed(c.DOMAINS[d])]
    for entity in order:
        ids = [r.local_id for r in refs if r.entity_type == entity]
        model = SPECS[entity].model
        if ids:
            out["records"] += (await session.execute(delete(model).where(model.firm_id == firm.id, model.id.in_(ids)))).rowcount or 0
        if SPECS[entity].sourced:   # anything else it wrote, by its mark
            out["records"] += (await session.execute(delete(model).where(
                model.firm_id == firm.id, model.source_system == MCP))).rowcount or 0
    await session.execute(delete(ExternalRef).where(ExternalRef.firm_id == firm.id, ExternalRef.source == MCP))
    await session.execute(delete(FoundationBreak).where(FoundationBreak.firm_id == firm.id, FoundationBreak.source == MCP))
    out["commands"] = (await session.execute(delete(FoundationCommand).where(
        FoundationCommand.firm_id == firm.id, FoundationCommand.source == MCP))).rowcount or 0
    for e in (await session.execute(select(RelationshipEdge).where(RelationshipEdge.firm_id == firm.id))).scalars().all():
        if (e.attributes or {}).get("source") == TAG:
            await session.delete(e)
            out["edges"] += 1
    for conn in mine:
        await session.execute(delete(ConnectorSync).where(ConnectorSync.connector_id == conn.id))
        await session.delete(conn)
    # The domains back to Astra, and what the server declared forgotten.
    s = dict(firm.settings or {})
    for d in OWNED:
        if owners.owner(firm, d) == MCP:
            s = owners.with_owner(s, d, owners.NATIVE)
    if s.get("foundation") == {}:
        s.pop("foundation")
    declared = dict(s.get(DECLARED) or {})
    declared.pop(MCP, None)
    if declared:
        s[DECLARED] = declared
    else:
        s.pop(DECLARED, None)
    firm.settings = s
    flag_modified(firm, "settings")
    await session.flush()
    await opt_out(session, firm, KEY)
    return out


register(Simulation(
    key=KEY, name="A simulated wealth platform, reached through MCP",
    stands_for="The client's wealth platform (WealthSmart) owning parties, accounts, positions and activity",
    seam="foundation.adapters: the real MCP client adapter (foundation.mcp) in mock mode, reaching this in-process "
         "MCP server; orders go out through foundation/orders.py",
    install=install, seed=seed, discard=discard, firms=firms,
    notes=["No tax lots, so tax and rebalancing read not assessed on this firm.",
           "Prices are the firm's own real feed; an order with no real price is refused by the platform."],
))
