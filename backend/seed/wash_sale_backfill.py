"""Seed one genuine wash-sale repurchase (L200-4 §7.2), for a firm whose demo book has
no recent buy to flag.

The household wash-sale calendar is computed live from real tax lots — there was never
a scripted "violation" to show, because no seeded account has ever bought the same
instrument twice within 30 days. This finds an instrument a household already holds at
a real, unrealised loss and adds one small extra tax lot on it, acquired recently, at
roughly today's real market price (not the original, higher cost) — an honest
repurchase, not a fabricated flag. `_load_lots` in tax_intelligence.py does the rest:
it only disallows a loss lot that a recent same-instrument purchase conflicts with,
exactly per IRC §1091.

Idempotent: skips a firm that already has any tax lot inside the wash-sale window.

    python -m seed.wash_sale_backfill
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.portfolio import Holding, TaxLot
from app.models.tenant import Firm

log = get_logger("aurea.seed.wash_sale_backfill")

_WASH_SALE_WINDOW = 30
_REPURCHASE_DAYS_AGO = 10


async def backfill() -> None:
    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm))).scalars().all()
        seeded = 0
        today = date.today()

        for firm in firms:
            lots = (await s.execute(select(TaxLot).where(TaxLot.firm_id == firm.id))).scalars().all()
            if any((today - lot.acquired_on).days <= _WASH_SALE_WINDOW for lot in lots):
                continue  # already has a recent lot somewhere -- nothing to seed

            holdings = (await s.execute(select(Holding).where(Holding.firm_id == firm.id))).scalars().all()
            holding_map = {h.id: h for h in holdings}

            # The largest real loss position in the book: cost basis clearly above
            # today's market price, aged well past the window, so the repurchase we add
            # creates an unambiguous violation rather than one riding the boundary.
            candidate = None
            candidate_price = 0.0
            candidate_loss = 0.0
            for lot in lots:
                h = holding_map.get(lot.holding_id)
                if not h or float(h.quantity or 0) <= 0:
                    continue
                current_price = float(h.market_value) / float(h.quantity)
                cost = float(lot.cost_per_unit)
                if current_price >= cost or (today - lot.acquired_on).days <= _WASH_SALE_WINDOW:
                    continue
                loss = (cost - current_price) * float(lot.quantity)
                if loss > candidate_loss:
                    candidate, candidate_price, candidate_loss = lot, current_price, loss

            if candidate is None:
                continue  # no loss position anywhere in this firm's book

            holding = holding_map[candidate.holding_id]
            repurchase_qty = round(float(candidate.quantity) * 0.05, 6) or 1.0
            repurchase_cost = round(candidate_price, 6)

            s.add(TaxLot(
                firm_id=firm.id, holding_id=holding.id, quantity=repurchase_qty,
                cost_per_unit=repurchase_cost, acquired_on=today - timedelta(days=_REPURCHASE_DAYS_AGO),
            ))
            holding.quantity = float(holding.quantity) + repurchase_qty
            holding.market_value = float(holding.market_value) + repurchase_qty * repurchase_cost
            holding.cost_basis = float(holding.cost_basis) + repurchase_qty * repurchase_cost

            seeded += 1
            log.info("wash_sale_seeded", firm=firm.slug, holding_id=str(holding.id),
                      disallowed_loss=round(candidate_loss, 2))

        await s.commit()
        print(f"\nSeeded {seeded} wash-sale repurchase lot(s) across {len(firms)} firm(s).\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
