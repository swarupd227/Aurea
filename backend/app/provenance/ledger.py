"""The decision ledger (spec §10.1) — append-only, hash-chained, tamper-evident.

Every agent decision and human action writes an entry capturing the trigger, the data used
(with lineage + confidence), firm research cited, the recommendation and its plain-language
rationale, the autonomy tier, and the human action taken. Each entry chains to the previous
via SHA-256, so any retroactive edit is detectable by verifying the chain."""
from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.governance import LedgerEntry

log = get_logger("aurea.ledger")
GENESIS = "0" * 64


def _canonical(content: dict) -> str:
    return json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(prev_hash: str, seq: int, event_type: str, content: dict) -> str:
    payload = f"{prev_hash}|{seq}|{event_type}|{_canonical(content)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def append_entry(
    session: AsyncSession,
    *,
    firm_id: uuid.UUID,
    event_type: str,
    content: dict,
    agent_key: str | None = None,
    run_id: uuid.UUID | None = None,
    recommendation_id: uuid.UUID | None = None,
    actor: str | None = None,
) -> LedgerEntry:
    """Append a new ledger entry, chained to the firm's latest entry."""
    last = (
        await session.execute(
            select(LedgerEntry)
            .where(LedgerEntry.firm_id == firm_id)
            .order_by(LedgerEntry.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    seq = (last.seq + 1) if last else 1
    prev_hash = last.entry_hash if last else GENESIS
    entry_hash = compute_hash(prev_hash, seq, event_type, content)

    entry = LedgerEntry(
        firm_id=firm_id,
        seq=seq,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
        event_type=event_type,
        agent_key=agent_key,
        run_id=run_id,
        recommendation_id=recommendation_id,
        actor=actor,
        content=content,
    )
    session.add(entry)
    await session.flush()
    log.info("ledger_append", firm_id=str(firm_id), seq=seq, event_type=event_type)
    return entry


async def verify_chain(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Recompute the hash chain and report the first break, if any."""
    entries = (
        await session.execute(
            select(LedgerEntry).where(LedgerEntry.firm_id == firm_id).order_by(LedgerEntry.seq.asc())
        )
    ).scalars().all()
    prev = GENESIS
    for e in entries:
        expected = compute_hash(prev, e.seq, e.event_type, e.content)
        if e.prev_hash != prev or e.entry_hash != expected:
            return {"valid": False, "broken_at_seq": e.seq, "count": len(entries)}
        prev = e.entry_hash
    return {"valid": True, "broken_at_seq": None, "count": len(entries)}


async def ledger_count(session: AsyncSession, firm_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count(LedgerEntry.id)).where(LedgerEntry.firm_id == firm_id)
        )
    ).scalar_one()
