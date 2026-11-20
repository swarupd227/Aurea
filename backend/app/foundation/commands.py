"""What Astra asks a Foundation system to record leaves as a command, and the copy changes only when the system's own
record of it comes back through a sync.

Today that is a CRM activity on a firm whose CRM Dynamics 365 owns: ``log_crm_activity`` (and its route) sends it
here instead of writing ``crm_activity``. The command is pending until a sync reads the activity back, then confirmed
with the row it became; a system that refuses it marks it failed with what it said. Nothing here writes a CRM row.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.foundation import contract as c
from app.foundation.adapters import AdapterError, get_adapter
from app.foundation.owners import NATIVE, owner, owner_name
from app.models.foundation import ExternalRef, FoundationCommand
from app.models.identity import AuditEvent

CRM_ACTIVITY = "crm.activity.create"


class CommandRefused(Exception):
    """The owner cannot take this command; the reason names it."""


async def connector_for(session: AsyncSession, firm, key: str):
    """The firm's connector for an adapter: the one that is switched on, if there are several."""
    from app.models.connectors import Connector

    rows = (await session.execute(select(Connector).where(Connector.firm_id == firm.id, Connector.provider_key == key)
                                  .order_by(Connector.created_at))).scalars().all()
    on = [r for r in rows if str(r.status) != "disabled"]
    return (on or [None])[0]


async def external_id_of(session: AsyncSession, firm, source: str, entity: str, local_id: uuid.UUID | None) -> str | None:
    if local_id is None:
        return None
    return (await session.execute(select(ExternalRef.external_id).where(
        ExternalRef.firm_id == firm.id, ExternalRef.source == source, ExternalRef.entity_type == entity,
        ExternalRef.local_id == local_id))).scalars().first()


async def prepare_crm_activity(session: AsyncSession, firm, *, contact, opportunity=None) -> dict:
    """Who would take an activity on this contact, and its ids there, or why it cannot go. Writes nothing."""
    key = owner(firm, "crm")
    adapter = None if key == NATIVE else get_adapter(key)
    if adapter is None or CRM_ACTIVITY not in adapter.commands:
        raise CommandRefused(f"{owner_name(key)} owns this firm's CRM and takes no activity from Astra.")
    connector = await connector_for(session, firm, key)
    if connector is None:
        raise CommandRefused(f"No {adapter.name} connector is switched on, so the activity cannot be sent. "
                             "Operations sets it up on the Connectors page.")
    contact_ext = await external_id_of(session, firm, key, "crm_contact", contact.id)
    if not contact_ext:
        raise CommandRefused(f"{contact.full_name} is not in {adapter.name}, so an activity on them cannot be recorded "
                             f"there. Add them in {adapter.name}; they reach Astra on the next sync.")
    opp_ext = None
    if opportunity is not None:
        opp_ext = await external_id_of(session, firm, key, "crm_opportunity", opportunity.id)
        if not opp_ext:
            raise CommandRefused(f"The deal {opportunity.title} is not in {adapter.name}.")
    return {"adapter": adapter, "connector": connector, "contact_external_id": contact_ext,
            "opportunity_external_id": opp_ext}


async def crm_activity(session: AsyncSession, firm, actor, *, contact, opportunity, activity_type: str, at: datetime,
                       detail: str) -> FoundationCommand:
    """Send one activity to the CRM's owner. It stays pending until the next sync brings it back."""
    ready = await prepare_crm_activity(session, firm, contact=contact, opportunity=opportunity)
    payload = {"contact_external_id": ready["contact_external_id"],
               "opportunity_external_id": ready["opportunity_external_id"],
               "contact_id": str(contact.id), "opportunity_id": str(opportunity.id) if opportunity else None,
               "activity_type": activity_type, "occurred_at": at.isoformat(), "detail": detail,
               "logged_by_email": getattr(actor, "email", None)}
    return await send(session, firm, actor, adapter=ready["adapter"], connector=ready["connector"], domain="crm",
                      kind=CRM_ACTIVITY, payload=payload, subject=f"{activity_type} with {contact.full_name}")


async def send(session: AsyncSession, firm, actor, *, adapter, connector, domain: str, kind: str, payload: dict,
               subject: str) -> FoundationCommand:
    cmd = FoundationCommand(firm_id=firm.id, connector_id=connector.id if connector else None, source=adapter.key,
                            domain=domain, kind=kind, payload=payload, status="pending",
                            created_by=getattr(actor, "id", None))
    session.add(cmd)
    await session.flush()
    try:
        await adapter.send(session, firm, connector, cmd)
        cmd.sent_at = utcnow()
    except AdapterError as exc:
        cmd.status, cmd.error = "failed", str(exc)
    session.add(AuditEvent(firm_id=firm.id, actor_id=getattr(actor, "id", None), actor_email=getattr(actor, "email", None),
                           event_type="foundation.command_sent", subject=subject,
                           detail={"command_id": str(cmd.id), "to": adapter.key, "kind": kind, "status": cmd.status}))
    await session.flush()
    return cmd


async def confirm(session: AsyncSession, firm, source: str, records: list[c.Record], *, now: datetime | None = None) -> int:
    """Pending commands whose record came back in this pull: confirmed, with the row it became."""
    came = {r.external_id for r in records if r.entity == "crm_activity"}
    if not came:
        return 0
    pending = (await session.execute(select(FoundationCommand).where(
        FoundationCommand.firm_id == firm.id, FoundationCommand.source == source, FoundationCommand.status == "pending",
        FoundationCommand.external_id.in_(came)))).scalars().all()
    for cmd in pending:
        cmd.local_id = (await session.execute(select(ExternalRef.local_id).where(
            ExternalRef.firm_id == firm.id, ExternalRef.source == source, ExternalRef.entity_type == "crm_activity",
            ExternalRef.external_id == cmd.external_id))).scalars().first()
        cmd.status, cmd.confirmed_at = "confirmed", now or utcnow()
    await session.flush()
    return len(pending)
