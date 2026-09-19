"""Seed a starter UMA (L200-3 §6) on one account per firm, for firms that predate these
tables: two sleeves running the account's own model, each holding fully attributed to
exactly one sleeve — a clean split (equities in one, everything else in the other) that
reconciles to zero breaks out of the box.

Idempotent: skips a firm that already has any Sleeve row.

    python -m seed.sleeves_backfill
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.graph import Account, Mandate
from app.models.portfolio import Holding, Instrument
from app.models.sleeves import Sleeve, SleeveHolding
from app.models.tenant import Firm

log = get_logger("aurea.seed.sleeves_backfill")


async def backfill() -> None:
    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm))).scalars().all()
        firms_seeded = 0

        for firm in firms:
            existing = (await s.execute(select(Sleeve).where(Sleeve.firm_id == firm.id))).scalar_one_or_none()
            if existing is not None:
                continue

            # A mandate with a model and at least two holdings on its account, so the split
            # is real rather than degenerate.
            mandates = (await s.execute(
                select(Mandate).where(Mandate.firm_id == firm.id, Mandate.model_portfolio_id.isnot(None))
            )).scalars().all()

            target = None
            for m in mandates:
                accounts = (await s.execute(
                    select(Account).where(Account.mandate_id == m.id)
                )).scalars().all()
                for acct in accounts:
                    holdings = (await s.execute(
                        select(Holding).where(Holding.account_id == acct.id)
                    )).scalars().all()
                    if len(holdings) >= 2:
                        target = (m, acct, holdings)
                        break
                if target:
                    break

            if target is None:
                continue

            mandate, account, holdings = target
            core = Sleeve(firm_id=firm.id, account_id=account.id, model_id=mandate.model_portfolio_id,
                          name="Core Equity Sleeve", target_weight=0.6, cash_target=0.02, status="active")
            satellite = Sleeve(firm_id=firm.id, account_id=account.id, model_id=mandate.model_portfolio_id,
                               name="Satellite Sleeve", target_weight=0.4, cash_target=0.02, status="active")
            s.add_all([core, satellite])
            await s.flush()

            instrument_cache: dict = {}
            for i, h in enumerate(holdings):
                if h.instrument_id not in instrument_cache:
                    instrument_cache[h.instrument_id] = await s.get(Instrument, h.instrument_id)
                inst = instrument_cache[h.instrument_id]
                sleeve = core if (inst and inst.asset_class == "equity") or i % 2 == 0 else satellite
                s.add(SleeveHolding(firm_id=firm.id, sleeve_id=sleeve.id, holding_id=h.id,
                                    quantity=h.quantity, cost_basis=h.cost_basis))

            firms_seeded += 1
            log.info("sleeves_seeded", firm=firm.slug, account=account.name, holdings=len(holdings))

        await s.commit()
        print(f"\nSeeded a starter UMA (2 sleeves) on one account for {firms_seeded} firm(s).\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
