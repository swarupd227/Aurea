"""Where each of a firm's domains comes from, as figures: the owner and its adapter, what it can supply, its last sync,
how fresh the copy is, how many records it holds, the open breaks and the commands still waiting.

One read for the Connectors page (GET /api/conduit/foundation) and the conversation (read_foundation_sources)."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.foundation.adapters import for_domain, get_adapter
from app.foundation.commands import connector_for
from app.foundation.mapping import DOMAIN_ENTITIES, SPECS
from app.foundation.owners import DOMAIN_LABEL, DOMAINS, NATIVE, owners
from app.models.connectors import ConnectorSync
from app.models.foundation import FoundationBreak, FoundationCommand

SHOWN = 20


def _iso(v):
    return v.isoformat() if v else None


def _command_summary(m) -> str | None:
    """An order as the figures it carries: side, units, symbol."""
    p = m.payload or {}
    if m.kind == "orders.place" and p.get("symbol"):
        return f"{str(p.get('side') or '').lower()} {float(p.get('quantity') or 0):g} {p['symbol']}"
    return None


async def overview(session: AsyncSession, firm) -> dict:
    owned = owners(firm)
    rows = []
    for domain in DOMAINS:
        key = owned[domain]
        adapter = get_adapter(key)
        connector = None if key == NATIVE else await connector_for(session, firm, key)
        last = None
        if connector is not None:
            sync = (await session.execute(select(ConnectorSync).where(ConnectorSync.connector_id == connector.id)
                                          .order_by(ConnectorSync.created_at.desc()).limit(1))).scalars().first()
            if sync is not None:
                last = {"at": _iso(sync.finished_at or sync.created_at), "status": sync.status,
                        "records": sync.records_ingested}
        total = from_source = 0
        as_of = None
        for entity in DOMAIN_ENTITIES[domain]:
            spec = SPECS[entity]
            total += (await session.execute(select(func.count()).select_from(spec.model)
                                            .where(spec.model.firm_id == firm.id))).scalar_one()
            if key != NATIVE and spec.sourced:
                n, newest = (await session.execute(select(func.count(), func.max(spec.model.as_of)).where(
                    spec.model.firm_id == firm.id, spec.model.source_system == key))).one()
                from_source += n
                if newest and (as_of is None or newest > as_of):
                    as_of = newest
        breaks = (await session.execute(select(func.count()).select_from(FoundationBreak).where(
            FoundationBreak.firm_id == firm.id, FoundationBreak.domain == domain,
            FoundationBreak.status == "open"))).scalar_one()
        pending = (await session.execute(select(func.count()).select_from(FoundationCommand).where(
            FoundationCommand.firm_id == firm.id, FoundationCommand.domain == domain,
            FoundationCommand.status == "pending"))).scalar_one()
        declared = adapter.declared(firm) if adapter is not None and key != NATIVE else {}
        simulated = key != NATIVE and (bool(declared.get("simulated")) or bool(connector is not None and (connector.config or {}).get("simulated")))
        rows.append({
            "domain": domain, "label": DOMAIN_LABEL[domain], "owner": key, "native": key == NATIVE,
            "adapter": adapter.name if adapter else key, "built": adapter is not None,
            "capabilities": dict(adapter.capabilities_for(firm)) if adapter else {},
            # What the owner's own system said of itself (an MCP server declares it), and whether it is a simulation.
            "platform": (declared.get("platform") or {}).get("name") if declared else None,
            "simulated": simulated,
            "connector_id": str(connector.id) if connector else None,
            "mode": None if connector is None else ("mock" if connector.use_mock else "live"),
            "last_sync": last, "as_of": _iso(as_of),
            "records": total, "from_source": from_source if key != NATIVE else None,
            "open_breaks": breaks, "pending_commands": pending,
            "owners_available": [a.key for a in for_domain(domain)],
        })
    breaks = (await session.execute(select(FoundationBreak).where(
        FoundationBreak.firm_id == firm.id, FoundationBreak.status == "open")
        .order_by(FoundationBreak.detected_at.desc()).limit(SHOWN))).scalars().all()
    commands = (await session.execute(select(FoundationCommand).where(FoundationCommand.firm_id == firm.id)
                                      .order_by(FoundationCommand.created_at.desc()).limit(SHOWN))).scalars().all()
    return {
        "native": all(r["native"] for r in rows),
        "simulated": any(r["simulated"] for r in rows),
        "domains": rows,
        "breaks": [{"id": str(b.id), "domain": b.domain, "entity": b.entity_type, "local_id": str(b.local_id),
                    "source": b.source, "external_id": b.external_id, "fields": b.fields or [],
                    "detected_at": _iso(b.detected_at)} for b in breaks],
        "commands": [{"id": str(m.id), "domain": m.domain, "kind": m.kind, "to": m.source, "status": m.status,
                      "external_id": m.external_id, "activity_type": (m.payload or {}).get("activity_type"),
                      "summary": _command_summary(m),
                      "error": m.error, "created_at": _iso(m.created_at), "confirmed_at": _iso(m.confirmed_at)}
                     for m in commands],
    }
