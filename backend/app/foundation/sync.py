"""A source's records into Astra's tables: upserted by (source, external_id) through external_ref, idempotently.

For each record, three values are compared field by field: the row here, the source's, and what the last sync applied
(``external_ref.synced``).

  - here equals the source: nothing changes (the record is unchanged; an open break on it clears);
  - here equals what was last synced: the copy is untouched since, so it follows the source (updated);
  - otherwise the copy was changed here and the source says something else: a break, with each field's three values
    as its evidence. Neither side is written. It clears on a later sync where they agree.

A record whose source no longer has it is a break too ("absent at the source"), never a deletion. A reference this firm
cannot place (an activity on a contact it does not have) is skipped and counted, never guessed. Running a sync twice
changes nothing the second time.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.foundation import contract as c
from app.foundation.mapping import SPECS, Resolver, Unresolved, plain, snapshot, values_of
from app.models.foundation import ExternalRef, FoundationBreak
from app.models.portfolio import Instrument, Price

SKIPPED_SHOWN = 20


def _report() -> dict:
    return {"created": 0, "updated": 0, "unchanged": 0, "breaks": 0, "cleared": 0, "missing_at_source": 0,
            "skipped": [], "by_entity": {}}


async def _open_break(session: AsyncSession, firm_id: uuid.UUID, entity: str, local_id: uuid.UUID) -> FoundationBreak | None:
    return (await session.execute(select(FoundationBreak).where(
        FoundationBreak.firm_id == firm_id, FoundationBreak.entity_type == entity, FoundationBreak.local_id == local_id,
        FoundationBreak.status == "open"))).scalars().first()


async def _break(session: AsyncSession, firm_id: uuid.UUID, rec: c.Record, local_id: uuid.UUID, fields: list[dict],
                 now: datetime) -> None:
    """One open break per record: a later sync refreshes its evidence rather than raising another."""
    row = await _open_break(session, firm_id, rec.entity, local_id)
    if row is None:
        session.add(FoundationBreak(firm_id=firm_id, domain=rec.domain, entity_type=rec.entity, local_id=local_id,
                                    source=rec.source, external_id=rec.external_id, fields=fields, status="open",
                                    detected_at=now))
    elif row.fields != fields:
        row.fields = fields


async def _clear(session: AsyncSession, firm_id: uuid.UUID, entity: str, local_id: uuid.UUID, now: datetime) -> bool:
    row = await _open_break(session, firm_id, entity, local_id)
    if row is None:
        return False
    row.status, row.cleared_at = "cleared", now
    return True


def _evidence(src: dict, here: dict, synced: dict) -> list[dict]:
    return [{"field": k, "here": here.get(k), "source": src.get(k), "synced": synced.get(k)}
            for k in sorted(src) if here.get(k) != src.get(k)]


async def _price(session: AsyncSession, firm, rec: c.Price, values: dict, now: datetime, report: dict) -> str:
    """A price is keyed by its instrument and day. Settled history is never rewritten: a different figure for a day
    already on file is a break."""
    row = (await session.execute(select(Price).where(Price.instrument_id == values["instrument_id"],
                                                     Price.as_of == values["as_of"]))).scalars().first()
    if row is None:
        session.add(Price(firm_id=firm.id, **values))
        return "created"
    spec = SPECS["price"]
    src, here = snapshot(spec, values), snapshot(spec, {k: getattr(row, k) for k in values})
    if src == here:
        return "cleared" if await _clear(session, firm.id, "price", row.id, now) else "unchanged"
    await _break(session, firm.id, rec, row.id, _evidence(src, here, {}), now)
    return "break"


async def _upsert(session: AsyncSession, firm, rec: c.Record, values: dict, r: Resolver, now: datetime) -> str:
    spec = SPECS[rec.entity]
    src = snapshot(spec, values)
    ref = (await session.execute(select(ExternalRef).where(
        ExternalRef.firm_id == firm.id, ExternalRef.source == rec.source, ExternalRef.entity_type == rec.entity,
        ExternalRef.external_id == rec.external_id))).scalar_one_or_none()
    if ref is None:
        row = None
        if spec.entity == "instrument":   # a symbol is unique in a firm: the source's instrument is the one on file
            row = (await session.execute(select(Instrument).where(
                Instrument.firm_id == firm.id, Instrument.symbol == values["symbol"]))).scalars().first()
        outcome = "created"
        if row is None:
            row = spec.model(firm_id=firm.id, **values)
            if spec.sourced:
                row.source_system, row.external_id, row.as_of = rec.source, rec.external_id, rec.as_of
            session.add(row)
            await session.flush()
            if spec.entity == "instrument":
                r.forget_symbols()
        else:
            here = snapshot(spec, {k: getattr(row, k) for k in values})
            outcome = "unchanged"
            if here != src:
                await _break(session, firm.id, rec, row.id, _evidence(src, here, {}), now)
                outcome = "break"
        session.add(ExternalRef(firm_id=firm.id, entity_type=rec.entity, local_id=row.id, source=rec.source,
                                external_id=rec.external_id, synced=src, synced_at=now))
        r.remember(rec.entity, rec.external_id, row.id)
        return outcome

    row = await session.get(spec.model, ref.local_id)
    if row is None or row.firm_id != firm.id:
        await _break(session, firm.id, rec, ref.local_id,
                     [{"field": "record", "here": "absent", "source": "present", "synced": "present"}], now)
        return "break"
    r.remember(rec.entity, rec.external_id, row.id)
    here = snapshot(spec, {k: getattr(row, k) for k in values})
    synced = dict(ref.synced or {})
    if here == src:
        if synced != src:
            ref.synced, ref.synced_at = src, now
        if spec.sourced and rec.as_of and (row.as_of is None or plain(rec.as_of) > plain(row.as_of)):
            row.as_of = rec.as_of   # read again, the same: fresher, not changed
        return "cleared" if await _clear(session, firm.id, rec.entity, row.id, now) else "unchanged"
    if here == {k: synced.get(k) for k in src}:
        for k, v in values.items():
            setattr(row, k, v)
        if spec.sourced:
            row.as_of = rec.as_of
        ref.synced, ref.synced_at = src, now
        await _clear(session, firm.id, rec.entity, row.id, now)
        return "updated"
    await _break(session, firm.id, rec, row.id, _evidence(src, here, synced), now)
    return "break"


async def import_records(session: AsyncSession, firm, records: list[c.Record], *, now: datetime | None = None) -> dict:
    """Upsert records, which come in import order (a record after those it refers to). Returns what happened, counted."""
    now = now or utcnow()
    report = _report()
    resolvers: dict[str, Resolver] = {}
    for rec in records:
        spec = SPECS[rec.entity]
        r = resolvers.setdefault(rec.source, Resolver(session, firm.id, rec.source))
        try:
            values = await values_of(spec, rec, r)
        except Unresolved as exc:
            report["skipped"].append(str(exc))
            continue
        outcome = (await _price(session, firm, rec, values, now, report) if rec.entity == "price"
                   else await _upsert(session, firm, rec, values, r, now))
        key = {"cleared": "unchanged", "break": "breaks"}.get(outcome, outcome)
        report[key] += 1
        if outcome == "cleared":
            report["cleared"] += 1
        tally = report["by_entity"].setdefault(rec.entity, {"created": 0, "updated": 0, "unchanged": 0, "breaks": 0})
        tally[key] += 1
    await session.flush()
    return report


async def mark_missing(session: AsyncSession, firm, source: str, domain: str, records: list[c.Record],
                       *, now: datetime | None = None) -> int:
    """Records this source gave before and does not give now: a break each ("absent at the source"). Nothing is
    deleted; the copy keeps what it was told until a person decides."""
    now = now or utcnow()
    seen = {(r.entity, r.external_id) for r in records}
    entities = [cls.entity for cls in c.DOMAINS[domain] if cls.entity != "price"]
    refs = (await session.execute(select(ExternalRef).where(
        ExternalRef.firm_id == firm.id, ExternalRef.source == source, ExternalRef.entity_type.in_(entities)))).scalars().all()
    n = 0
    for ref in refs:
        if (ref.entity_type, ref.external_id) in seen:
            continue
        cls = c.RECORDS[ref.entity_type]
        stub = cls.model_construct(source=source, external_id=ref.external_id)
        await _break(session, firm.id, stub, ref.local_id,
                     [{"field": "record", "here": "present", "source": "absent", "synced": "present"}], now)
        n += 1
    await session.flush()
    return n


def _total(into: dict, rep: dict) -> None:
    for k in ("created", "updated", "unchanged", "breaks", "cleared", "missing_at_source", "confirmed"):
        into[k] = into.get(k, 0) + int(rep.get(k) or 0)


async def pull_domains(session: AsyncSession, firm, connector, adapter, domains: list[str]) -> dict[str, Any]:
    """Pull each domain the adapter owns for this firm, import it, mark what the source no longer has and confirm the
    commands whose records came back."""
    from app.foundation import commands

    now = utcnow()
    out: dict[str, Any] = {"adapter": adapter.key, "mode": None, "domains": {}}
    for domain in [d for d in c.DOMAIN_ORDER if d in domains]:
        pulled = await adapter.pull(session, firm, connector, domain)
        rep = await import_records(session, firm, pulled.records, now=now)
        rep["missing_at_source"] = await mark_missing(session, firm, adapter.key, domain, pulled.records, now=now)
        rep["confirmed"] = await commands.confirm(session, firm, adapter.key, pulled.records, now=now)
        rep["records"] = len(pulled.records)
        skipped = list(pulled.skipped) + rep["skipped"]
        rep["skipped_count"] = len(skipped)
        rep["skipped"] = skipped[:SKIPPED_SHOWN]
        out["domains"][domain] = rep
        out["mode"] = pulled.mode
        _total(out, rep)
    return out


async def run_for_connector(session: AsyncSession, connector) -> dict | None:
    """The body of a connector's sync when its adapter owns a domain of the firm; None when it owns none, so the
    connector's sync stays what it was (conduit/service.run_sync)."""
    from app.foundation.adapters import get_adapter
    from app.foundation.owners import NATIVE, owners
    from app.models.tenant import Firm

    adapter = get_adapter(connector.provider_key)
    if adapter is None or adapter.key == NATIVE:
        return None
    firm = await session.get(Firm, connector.firm_id)
    domains = [d for d, o in owners(firm).items() if o == adapter.key and adapter.serves(d)]
    if not domains:
        return None
    return await pull_domains(session, firm, connector, adapter, domains)
