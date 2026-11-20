"""The native adapter: Astra's own tables, as the contract.

It is the default owner of every domain, and a no-op at run time: a native domain is never synced, because its tables
are the source. What it does is export: every row of a firm's domain as a contract record whose external id is the
row's own Astra id. Imported into another firm through the sync, the same records must give the same brain, valuation,
rebalance and rule outputs (tests/verify_foundation_contract.py): that is what proves the contract carries the book.
"""
from __future__ import annotations

from app.foundation import contract as c
from app.foundation.adapters.base import Adapter, Pulled
from app.foundation.mapping import SPECS, Names, rows_of, to_record
from app.foundation.owners import DOMAINS


class NativeAdapter(Adapter):
    key = "native"
    name = "Astra"
    domains = frozenset(DOMAINS)
    capabilities = {"tax_lots": True, "transactions_days": None, "events": False}

    async def export(self, session, firm, domain: str) -> list[c.Record]:
        """Every row of the firm in this domain, in the order an import takes them."""
        names = await Names.native(session, firm.id)
        out: list[c.Record] = []
        for cls in c.DOMAINS[domain]:
            spec = SPECS[cls.entity]
            for row in await rows_of(session, spec, firm.id):
                if spec.entity == "price":
                    ext = f"{names.symbols.get(row.instrument_id)}@{row.as_of.isoformat()}"
                else:
                    ext = str(row.id)
                out.append(to_record(spec, row, names, source=self.key, external_id=ext,
                                     as_of=getattr(row, "updated_at", None) if spec.sourced else None))
        return out

    async def pull(self, session, firm, connector, domain: str) -> Pulled:
        return Pulled(records=await self.export(session, firm, domain), mode="native")
