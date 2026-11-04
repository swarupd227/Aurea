"""The decision ledger (spec §10.1) — append-only, hash-chained, tamper-evident.

Every agent decision and human action writes an entry capturing the trigger, the data used
(with lineage + confidence), firm research cited, the recommendation and its plain-language
rationale, the autonomy tier, and the human action taken. Each entry chains to the previous
via SHA-256, so any retroactive edit is detectable by verifying the chain.

One firm's appends are serialised (B-158): two transactions that each read the latest entry and
added one used to write the same `seq` twice, and the check then read that fork as a break. A
fork already written stays as it is, reported as one; rewriting hashes to mend it would defeat
the chain."""
from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.governance import LedgerEntry

log = get_logger("aurea.ledger")
GENESIS = "0" * 64


def _canonical(content: dict) -> str:
    return json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)


def lock_key(firm_id) -> int:
    """The firm's advisory-lock key: a signed 64-bit number from its id, the same in every process."""
    raw = hashlib.sha256(b"ledger:" + uuid.UUID(str(firm_id)).bytes).digest()[:8]
    return int.from_bytes(raw, "big", signed=True)


async def _serialise(session: AsyncSession, firm_id) -> None:
    """Hold the firm's ledger until this transaction ends, so the next append reads this one.
    SQLite writes one transaction at a time already."""
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key(firm_id)})


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
    await _serialise(session, firm_id)
    last = (
        await session.execute(
            select(LedgerEntry)
            .where(LedgerEntry.firm_id == firm_id)
            .order_by(LedgerEntry.seq.desc(), LedgerEntry.created_at.desc())
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
    """Recompute every entry's hash and check it links to an entry one before it.

    `broken_at_seq` is the first entry whose content no longer matches its hash, or whose link
    leads to no entry (one removed or changed before it). `forks` are numbers two entries share
    because they were written at once before appends were serialised (B-158): each still
    hashes and links, so the chain is not broken by them, and they are named, not hidden."""
    entries = (
        await session.execute(
            select(LedgerEntry).where(LedgerEntry.firm_id == firm_id).order_by(LedgerEntry.seq.asc())
        )
    ).scalars().all()
    by_seq: dict[int, list[LedgerEntry]] = {}
    for e in entries:
        by_seq.setdefault(e.seq, []).append(e)
    forks = sorted(seq for seq, group in by_seq.items() if len(group) > 1)
    broken = None
    for seq in sorted(by_seq):
        before = {GENESIS} if seq == 1 else {p.entry_hash for p in by_seq.get(seq - 1, [])}
        if any(e.prev_hash not in before
               or e.entry_hash != compute_hash(e.prev_hash, e.seq, e.event_type, e.content) for e in by_seq[seq]):
            broken = seq
            break
    return {"valid": broken is None, "broken_at_seq": broken, "count": len(entries), "forks": forks}


def integrity_line(chain: dict) -> str:
    """The chain check in one sentence, for the conversation."""
    if not chain["valid"]:
        return (f"The ledger's hash chain is BROKEN at entry {chain['broken_at_seq']}: an entry there no longer "
                "matches its hash or links to nothing.")
    line = f"The ledger's hash chain is intact over {chain['count']} entries."
    if chain.get("forks"):
        n = len(chain["forks"])
        line += (f" {n} entry number{'s are' if n > 1 else ' is'} shared by two entries written at the same moment "
                 f"(first at {chain['forks'][0]}); each still hashes and links, and none was changed.")
    return line


async def ledger_count(session: AsyncSession, firm_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count(LedgerEntry.id)).where(LedgerEntry.firm_id == firm_id)
        )
    ).scalar_one()
