"""Name an instrument on each model target allocation, for models already in service.

    python -m seed.model_instrument_backfill

TargetAllocation.instrument_id has existed since the model was defined and was never
populated, so the rebalancer had no instrument to nominate for an asset class no account
already held. An under-weight class in that position produced "no instrument available"
and no order — the model could not be rebalanced toward its own target.

Picks, per class, the firm's own instrument for it: preferring a public, priced one so the
result is actually tradeable, and falling back to whatever exists in that class. Never
overwrites a nomination already made.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.enums import MarketType
from app.models.portfolio import Instrument, ModelPortfolio, Price, TargetAllocation
from app.models.tenant import Firm

log = get_logger("aurea.seed.model_instrument_backfill")

# Preferred instrument per class where the firm holds several.
PREFERRED = {"equity": "MSFT", "fixed_income": "AGG", "property": "VNQ",
             "alternatives": "PPEF1"}


async def backfill() -> None:
    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm))).scalars().all()
        named, kept, unmatched = 0, 0, []

        for firm in firms:
            instruments = (await s.execute(
                select(Instrument).where(Instrument.firm_id == firm.id)
            )).scalars().all()
            priced = {
                r[0] for r in (await s.execute(
                    select(Price.instrument_id).where(Price.firm_id == firm.id)
                )).all()
            }

            def pick(asset_class: str) -> Instrument | None:
                same = [i for i in instruments if str(i.asset_class) == str(asset_class)]
                if not same:
                    return None
                want = PREFERRED.get(str(asset_class))
                for i in same:
                    if i.symbol == want:
                        return i
                # Otherwise prefer something public and priced — i.e. actually tradeable.
                tradeable = [i for i in same
                             if i.market_type == MarketType.PUBLIC and i.id in priced]
                return (tradeable or same)[0]

            targets = (await s.execute(
                select(TargetAllocation).where(TargetAllocation.firm_id == firm.id)
            )).scalars().all()

            for t in targets:
                if t.instrument_id:
                    kept += 1
                    continue
                chosen = pick(t.asset_class)
                if chosen is None:
                    unmatched.append((firm.slug, str(t.asset_class)))
                    continue
                t.instrument_id = chosen.id
                named += 1
                log.info("model_instrument_named", firm=firm.slug,
                         asset_class=str(t.asset_class), symbol=chosen.symbol)

        await s.commit()

        print(f"\nNamed {named} instrument(s); left {kept} existing nomination(s) alone.\n")
        for firm in firms:
            models = (await s.execute(
                select(ModelPortfolio).where(ModelPortfolio.firm_id == firm.id)
            )).scalars().all()
            for m in models:
                print(f"{firm.slug} · {m.name}")
                print(f"  {'class':16}{'weight':>8}  {'instrument':28}tradeable")
                print("  " + "-" * 66)
                rows = (await s.execute(
                    select(TargetAllocation).where(TargetAllocation.model_id == m.id)
                )).scalars().all()
                for t in rows:
                    inst = await s.get(Instrument, t.instrument_id) if t.instrument_id else None
                    label = f"{inst.symbol} — {inst.name[:18]}" if inst else "(none named)"
                    trade = ("yes" if inst.market_type == MarketType.PUBLIC else "no — private")\
                        if inst else "-"
                    print(f"  {str(t.asset_class):16}{float(t.target_weight):>7.0%}  {label:28}{trade}")
                print()

        if unmatched:
            print("No instrument of the right class exists for:")
            for slug, ac in unmatched:
                print(f"  {slug}: {ac}")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
