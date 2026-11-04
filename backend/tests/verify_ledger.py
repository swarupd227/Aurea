"""Prove the decision ledger's appends are serialised per firm and its check tells a fork from a break (B-158).

Live on 4 Nov the chain read "broken at 4963": twelve numbers had been written twice by appends that each read the
latest entry at the same moment. Each append now takes the firm's advisory lock on Postgres; a fork already written
still hashes and links, so it is named, not read as tampering; an entry changed or removed is still a break.

    python -m tests.verify_ledger
"""
from __future__ import annotations

import asyncio
import sys
import uuid

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


class _Dialect:
    def __init__(self, name): self.name = name


class _Bind:
    def __init__(self, name): self.dialect = _Dialect(name)


class _Recorder:
    """Stands in for a Postgres session, recording what the lock sends."""
    def __init__(self): self.sent = []
    def get_bind(self): return _Bind("postgresql")
    async def execute(self, stmt, params=None): self.sent.append((str(stmt), params))


async def main() -> int:
    from sqlalchemy import delete, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401
    from app.core.db import Base
    from app.models.governance import LedgerEntry
    from app.models.tenant import Firm
    from app.provenance import ledger

    print("\n=== The firm's lock ===")
    a, b = uuid.uuid4(), uuid.uuid4()
    check("one key per firm, the same every time and as text", (ledger.lock_key(a) == ledger.lock_key(str(a)),
                                                                ledger.lock_key(a) == ledger.lock_key(b)), (True, False))
    check("it fits Postgres' signed 64-bit key", -2**63 <= ledger.lock_key(a) < 2**63, True)
    pg = _Recorder()
    await ledger._serialise(pg, a)
    check("on Postgres an append takes the transaction's advisory lock",
          (len(pg.sent), "pg_advisory_xact_lock" in pg.sent[0][0], pg.sent[0][1]), (1, True, {"k": ledger.lock_key(a)}))

    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(db, expire_on_commit=False)() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD", jurisdiction="NZ", settings={})
            other = Firm(id=uuid.uuid4(), name="U", slug="u", base_currency="NZD", jurisdiction="NZ", settings={})
            s.add_all([firm, other])
            await s.flush()
            for i in range(5):
                await ledger.append_entry(s, firm_id=firm.id, event_type="note", content={"i": i}, actor="a@t")
            await ledger.append_entry(s, firm_id=other.id, event_type="note", content={"i": 0}, actor="a@u")

            print("\n=== A clean chain ===")
            chain = await ledger.verify_chain(s, firm.id)
            check("intact, five entries, no forks", (chain["valid"], chain["count"], chain["forks"]), (True, 5, []))
            check("it says so in one sentence", ledger.integrity_line(chain), "The ledger's hash chain is intact over 5 entries.")
            check("another firm's entries start their own chain", (await ledger.verify_chain(s, other.id))["count"], 1)

            print("\n=== Two entries written at once ===")
            third = (await s.execute(select(LedgerEntry).where(LedgerEntry.firm_id == firm.id, LedgerEntry.seq == 3))).scalar_one()
            twin_content = {"i": "twin"}
            s.add(LedgerEntry(firm_id=firm.id, seq=3, prev_hash=third.prev_hash, event_type="note", actor="b@t",
                              content=twin_content, entry_hash=ledger.compute_hash(third.prev_hash, 3, "note", twin_content)))
            await s.flush()
            chain = await ledger.verify_chain(s, firm.id)
            check("a fork is not a break, and is named", (chain["valid"], chain["broken_at_seq"], chain["forks"]), (True, None, [3]))
            line = ledger.integrity_line(chain)
            check("the sentence names it and says nothing was changed",
                  ("intact" in line, "first at 3" in line, "none was changed" in line), (True, True, True))
            nxt = await ledger.append_entry(s, firm_id=firm.id, event_type="note", content={"i": 5}, actor="a@t")
            check("the next append follows one entry, numbered after the last", nxt.seq, 6)

            print("\n=== A changed entry is still a break ===")
            fourth = (await s.execute(select(LedgerEntry).where(LedgerEntry.firm_id == firm.id, LedgerEntry.seq == 4))).scalar_one()
            fourth.content = {"i": "edited"}
            await s.flush()
            chain = await ledger.verify_chain(s, firm.id)
            check("broken where the content no longer matches its hash", (chain["valid"], chain["broken_at_seq"]), (False, 4))
            check("and the sentence says broken", "BROKEN at entry 4" in ledger.integrity_line(chain), True)
            fourth.content = {"i": 3}
            await s.flush()
            check("put back, it is intact again", (await ledger.verify_chain(s, firm.id))["valid"], True)

            print("\n=== A removed entry is still a break ===")
            await s.execute(delete(LedgerEntry).where(LedgerEntry.firm_id == firm.id, LedgerEntry.seq == 2))
            await s.flush()
            chain = await ledger.verify_chain(s, firm.id)
            check("the entry after it links to nothing", (chain["valid"], chain["broken_at_seq"]), (False, 3))

            print("\n=== A fork with a changed twin ===")
            async with async_sessionmaker(db, expire_on_commit=False)() as s2:
                f2 = Firm(id=uuid.uuid4(), name="V", slug="v", base_currency="NZD", jurisdiction="NZ", settings={})
                s2.add(f2)
                await s2.flush()
                first = await ledger.append_entry(s2, firm_id=f2.id, event_type="note", content={"i": 0})
                twin = LedgerEntry(firm_id=f2.id, seq=1, prev_hash=first.prev_hash, event_type="note",
                                   content={"i": "x"}, entry_hash="f" * 64)
                s2.add(twin)
                await s2.flush()
                chain = await ledger.verify_chain(s2, f2.id)
                check("a twin that does not hash is a break, fork or not", (chain["valid"], chain["broken_at_seq"]), (False, 1))
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
