"""Seed one GIPS composite per model portfolio (L200-5 §2.5), for firms that predate
these tables — wrapping the firm's real strategies rather than inventing new ones.

Also sets `ModelPortfolio.benchmark_symbol` where it was never set (the seed created two
models but never gave either a benchmark, so the existing firm-wide alpha calculation in
`analytics/portfolio.py` has always silently resolved to "no benchmark"). Adds an SPY
instrument with REAL fetched price history (the same `fetch_history_yahoo` path
`_seed_price_history` already uses — not fabricated) and gives each model a blend from its own
targets: its defensive share against AGG (already seeded with real history), the rest against
SPY. (It used to give Balanced AGG alone, which made a mostly-equity book look like it beat
its benchmark by the stock market's return; seed/benchmark_backfill.py corrects firms seeded then.)

Idempotent: skips a firm that already has any Composite row.

    python -m seed.composites_backfill
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.aurea_core.performance import blend_for_targets
from app.conduit.marketdata import fetch_history_yahoo
from app.core.db import SessionLocal, utcnow
from app.core.logging import configure_logging, get_logger
from app.models.composites import Composite
from app.models.enums import MarketType
from app.models.portfolio import Instrument, ModelPortfolio, Price
from app.models.tenant import Firm

log = get_logger("aurea.seed.composites_backfill")

TODAY = date.today()
SEASONING_DAYS = 90


def criteria_for(model_name: str) -> str:
    """What a seeded composite says it includes. composite_report applies exactly this."""
    return (
        f"All discretionary, fee-paying accounts managed to the '{model_name}' model "
        "for the full reporting period, from the date of firm inception. Accounts already "
        "on the model at composite construction are included immediately (this composite "
        "is being built from an existing back-book, not accumulating from a cold start); "
        f"a new account joining after construction is seasoned {SEASONING_DAYS} days before inclusion. "
        "No minimum account size."
    )


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

            models = (await s.execute(select(ModelPortfolio).where(ModelPortfolio.firm_id == firm.id)
                                      .options(selectinload(ModelPortfolio.targets)))).scalars().all()
            if not models:
                continue

            spy = await _ensure_spy(s, firm.id)

            for model in models:
                if not model.benchmark_symbol and spy:
                    # The model's own mix: its defensive share against AGG, the rest against SPY.
                    model.benchmark_symbol = blend_for_targets(model.targets)

                s.add(Composite(
                    firm_id=firm.id, model_portfolio_id=model.id, name=f"{model.name} Composite",
                    inclusion_criteria=criteria_for(model.name),
                    # The accounts seeded with the book are back-book and count at once;
                    # composite_report seasons only an account opened after this row.
                    # composite_seasoning_backfill.py corrects composites seeded with 0 days.
                    creation_date=TODAY, seasoning_days=SEASONING_DAYS,
                    minimum_account_size=None, status="active",
                ))
                created += 1
            log.info("composites_seeded", firm=firm.slug, count=len(models))

        await s.commit()
        print(f"\nSeeded {created} composite(s) across {len(firms)} firm(s).\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
