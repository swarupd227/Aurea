"""Dynamics 365 on the simulated demonstration firm (B-222). Stands for a Dynamics 365 environment.

The real part is the Dynamics 365 adapter (``app/foundation/adapters/dynamics.py``, B-163), whose mock mode reads a
Dataverse-shaped fixture and takes an activity as Dynamics 365 would. This switches it on as the CRM's owner of the
simulated wealth platform firm, so a sync, an activity going out and coming back, and a break can be shown there: its own
mock-mode connector, tagged ``simulated``, the CRM owned by it, and a first sync. ``firms`` names only the simulated
firm (creating it through the wealth-platform simulation when needed); a firm that already has a Dynamics 365
connector of its own, or whose CRM another system owns, is left as it is.

Discarding hands the CRM back to Astra and removes what the sync brought, the commands sent, the connector and its
syncs. When Dynamics 365 itself is connected, this is discarded and the firm's real connector is switched live.
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm.attributes import flag_modified

from app.simulations import Simulation, enabled_for, opt_in, opt_out, register, tag

KEY = "dynamics_demo"
TAG = tag(KEY)
D365 = "crm.dynamics"


def install() -> None:
    """Nothing to register: the Dynamics 365 adapter's mock mode is built in. The seam is the CRM's owner."""


async def firms(session) -> list:
    from app.models.tenant import Firm
    from app.simulations.wealth_platform import KEY as PLATFORM, ensure_firm

    found = [f for f in (await session.execute(select(Firm))).scalars().all()
             if (f.settings or {}).get("simulated_firm") == PLATFORM]
    return found or [await ensure_firm(session)]


async def _mine(session, firm) -> list:
    from app.models.connectors import Connector

    return [r for r in (await session.execute(select(Connector).where(
        Connector.firm_id == firm.id, Connector.provider_key == D365))).scalars().all()
        if (r.config or {}).get("simulated") == KEY]


async def seed(session, firm) -> dict:
    from app.conduit.service import run_sync
    from app.foundation import owners
    from app.models.connectors import Connector, ConnectorSync
    from app.models.enums import ConnectorDomain, ConnectorStatus

    await opt_in(session, firm, KEY)
    mine = await _mine(session, firm)
    theirs = [r for r in (await session.execute(select(Connector).where(
        Connector.firm_id == firm.id, Connector.provider_key == D365))).scalars().all() if r not in mine]
    if not mine and (theirs or owners.owner(firm, "crm") not in (owners.NATIVE, D365)):
        return {"connector": 0, "owner_set": 0, "records_synced": 0, "left_alone": 1}
    made = 0
    if not mine:
        conn = Connector(firm_id=firm.id, domain=ConnectorDomain.CRM, provider_key=D365,
                         display_name="Microsoft Dynamics 365 (simulated)", status=ConnectorStatus.MOCK, use_mock=True,
                         sync_cron="0 */6 * * *", config={"simulated": KEY})
        session.add(conn)
        await session.flush()
        mine, made = [conn], 1
    changed = 0
    if owners.owner(firm, "crm") != D365:
        firm.settings = owners.with_owner(firm.settings, "crm", D365)
        flag_modified(firm, "settings")
        changed = 1
        await session.flush()
    synced = 0
    if (await session.execute(select(ConnectorSync.id).where(ConnectorSync.connector_id == mine[0].id))).first() is None:
        sync = await run_sync(session, mine[0])
        synced = int(sync.records_ingested or 0)
    return {"connector": made, "owner_set": changed, "records_synced": synced}


async def discard(session, firm) -> dict:
    from app.foundation import contract as c
    from app.foundation import owners
    from app.foundation.mapping import SPECS
    from app.models.connectors import ConnectorSync
    from app.models.foundation import ExternalRef, FoundationBreak, FoundationCommand

    mine = await _mine(session, firm)
    if not mine and not enabled_for(firm, KEY):
        return {"records": 0, "connectors": 0}
    out = {"records": 0, "connectors": len(mine), "commands": 0}
    if mine:   # only what came through its own connector: a firm's own Dynamics 365 is not this simulation's
        refs = (await session.execute(select(ExternalRef).where(
            ExternalRef.firm_id == firm.id, ExternalRef.source == D365))).scalars().all()
        for cls in reversed(c.DOMAINS["crm"]):
            model = SPECS[cls.entity].model
            ids = [r.local_id for r in refs if r.entity_type == cls.entity]
            if ids:
                out["records"] += (await session.execute(delete(model).where(
                    model.firm_id == firm.id, model.id.in_(ids)))).rowcount or 0
            out["records"] += (await session.execute(delete(model).where(
                model.firm_id == firm.id, model.source_system == D365))).rowcount or 0
        await session.execute(delete(ExternalRef).where(ExternalRef.firm_id == firm.id, ExternalRef.source == D365))
        await session.execute(delete(FoundationBreak).where(FoundationBreak.firm_id == firm.id, FoundationBreak.source == D365))
        out["commands"] = (await session.execute(delete(FoundationCommand).where(
            FoundationCommand.firm_id == firm.id, FoundationCommand.source == D365))).rowcount or 0
        for conn in mine:
            await session.execute(delete(ConnectorSync).where(ConnectorSync.connector_id == conn.id))
            await session.delete(conn)
        if owners.owner(firm, "crm") == D365:
            s = owners.with_owner(firm.settings, "crm", owners.NATIVE)
            if s.get("foundation") == {}:
                s.pop("foundation")
            firm.settings = s
            flag_modified(firm, "settings")
        await session.flush()
    await opt_out(session, firm, KEY)
    return out


register(Simulation(
    key=KEY, name="Dynamics 365 on the simulated demonstration firm",
    stands_for="A Dynamics 365 environment holding the firm's CRM",
    seam="foundation.adapters: the Dynamics 365 adapter (crm.dynamics) in mock mode, as the CRM's owner of the "
         "simulated wealth platform firm",
    install=install, seed=seed, discard=discard, firms=firms,
    notes=["Contacts, opportunities and activities from the adapter's Dataverse-shaped fixture."],
))
