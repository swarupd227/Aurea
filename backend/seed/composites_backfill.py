"""Seed one GIPS composite per model portfolio (L200-5 §2.5), for firms that predate
these tables — wrapping the firm's real strategies rather than inventing new ones.

Also sets `ModelPortfolio.benchmark_symbol` where it was never set (the seed created two
models but never gave either a benchmark, so the existing firm-wide alpha calculation in
`analytics/portfolio.py` has always silently resolved to "no benchmark"). Growth gets a
new SPY instrument with REAL fetched price history (the same `fetch_history_yahoo` path
`_seed_price_history` already uses — not fabricated); Balanced references AGG, the bond
ETF already seeded with real history, since a fixed-income-heavy strategy is honestly
compared against a bond benchmark, not an equity index.

Idempotent: skips a firm that already has any Composite row.

    python -m seed.composites_backfill
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from sqlalchemy import select

from app.conduit.marketdata import fetch_history_yahoo
from app.core.db import SessionLocal, utcnow
from app.core.logging import configure_logging, get_logger
from app.models.composites import Composite
from app.models.enums import MarketType
from app.models.portfolio import Instrument, ModelPortfolio, Price
from app.models.tenant import Firm

log = get_logger("aurea.seed.composites_backfill")

TODAY = date.today()


async def _ensure_spy(s, firm_id) -> Instrument | None:
    existing = (await s.execute(
        select(Instrument).where(Instrument.firm_id == firm_id, Instrument.symbol == "SPY")
    )).scalar_one_or_none()
    if existing:
        return existing

    inst = Instrument(firm_id=firm_id, symbol="SPY", name="SPDR S&P 500 ETF Trust",
                      asset_class="equity", market_type=MarketType.PUBLIC, currency="USD")
    s.add(inst)
    await s.flush()

    hist = await fetch_history_yahoo("SPY")
    if len(hist) < 2:
        # Same honest fallback _seed_price_history uses when offline — a clearly-marked
        # synthetic path, never presented as a real market benchmark.
        for m in range(12, 0, -1):
            d = date(TODAY.year, TODAY.month, 1) - timedelta(days=30 * m)
            s.add(Price(firm_id=firm_id, instrument_id=inst.id, as_of=d,
                        close=round(500 * (1 - 0.01 * m), 4), currency="USD",
                        source="synthetic", is_real=False))
    else:
        for d_iso, close in hist:
            d = date.fromisoformat(d_iso)
            if d >= TODAY:
                continue
            s.add(Price(firm_id=firm_id, instrument_id=inst.id, as_of=d, close=close,
                        currency="USD", source="yahoo_history", is_real=True))
    await s.flush()
    return inst


async def backfill() -> None:
    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm))).scalars().all()
        created = 0

        for firm in firms:
            existing = (await s.execute(select(Composite).where(Composite.firm_id == firm.id))).scalar_one_or_none()
            if existing is not None:
                continue

            models = (await s.execute(select(ModelPortfolio).where(ModelPortfolio.firm_id == firm.id))).scalars().all()
            if not models:
                continue

            spy = await _ensure_spy(s, firm.id)

            for model in models:
                if not model.benchmark_symbol:
                    model.benchmark_symbol = "SPY" if "growth" in model.name.lower() and spy else \
                        ("AGG" if "balanced" in model.name.lower() else None)

                s.add(Composite(
                    firm_id=firm.id, model_portfolio_id=model.id, name=f"{model.name} Composite",
                    inclusion_criteria=(
                        f"All discretionary, fee-paying accounts managed to the '{model.name}' model "
                        "for the full reporting period, from the date of firm inception. New accounts "
                        "are seasoned 90 days before inclusion. No minimum account size."
                    ),
                    creation_date=TODAY - timedelta(days=365 * 2), seasoning_days=90,
                    minimum_account_size=None, status="active",
                ))
                created += 1
            log.info("composites_seeded", firm=firm.slug, count=len(models))

        await s.commit()
        print(f"\nSeeded {created} composite(s) across {len(firms)} firm(s).\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
