"""Prove the GIPS composite engine (L200-5 §2.5): a real per-account return series built
from every monthly price point on file (not just first-vs-last, which is all the existing
book-level number ever computed), seasoning and minimum-size exclusions, asset-weighted
gross return and internal dispersion, an ex-post standard deviation honestly labelled by
how many months it actually used against the GIPS 36-month minimum, and the firm-definition
check catching discretionary AUM that isn't captured in any composite.

Runs against a throwaway SQLite database so it needs no cloud connection.

    python -m tests.verify_composites
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.aurea_core import composites as engine
from app.core.db import Base, utcnow
from app.models.composites import Composite
from app.models.graph import Account, Mandate
from app.models.portfolio import Holding, Instrument, ModelPortfolio, Price
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = (abs(float(got) - float(want)) < 0.01) if isinstance(want, (int, float)) else got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    wanted = ["firm", "account", "instrument", "price", "holding", "mandate",
              "model_portfolio", "composite"]
    tables = [Base.metadata.tables[n] for n in wanted]
    async with db.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    Session = async_sessionmaker(db, expire_on_commit=False)

    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="Test", slug="test")
        model = ModelPortfolio(id=uuid.uuid4(), firm_id=firm.id, name="Growth", benchmark_symbol="BENCH")
        s.add_all([firm, model])
        await s.flush()

        jan, feb, mar = date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)
        aaa = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="AAA", name="Alpha", asset_class="equity")
        bbb = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="BBB", name="Beta", asset_class="equity")
        bench = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="BENCH", name="Benchmark Index", asset_class="equity")
        s.add_all([aaa, bbb, bench])
        await s.flush()

        # AAA: +10% then +10%. BBB: -10% then -20%. Deliberately different so the latest
        # period's dispersion and the two-period annualised std dev are both non-zero.
        s.add_all([
            Price(firm_id=firm.id, instrument_id=aaa.id, as_of=jan, close=Decimal("100"), source="test", is_real=True),
            Price(firm_id=firm.id, instrument_id=aaa.id, as_of=feb, close=Decimal("110"), source="test", is_real=True),
            Price(firm_id=firm.id, instrument_id=aaa.id, as_of=mar, close=Decimal("121"), source="test", is_real=True),
            Price(firm_id=firm.id, instrument_id=bbb.id, as_of=jan, close=Decimal("100"), source="test", is_real=True),
            Price(firm_id=firm.id, instrument_id=bbb.id, as_of=feb, close=Decimal("90"), source="test", is_real=True),
            Price(firm_id=firm.id, instrument_id=bbb.id, as_of=mar, close=Decimal("72"), source="test", is_real=True),
            Price(firm_id=firm.id, instrument_id=bench.id, as_of=jan, close=Decimal("200"), source="test", is_real=True),
            Price(firm_id=firm.id, instrument_id=bench.id, as_of=mar, close=Decimal("220"), source="test", is_real=True),
        ])
        await s.flush()

        now = utcnow()
        seasoned = now - timedelta(days=200)
        unseasoned = now - timedelta(days=5)

        mandate = Mandate(id=uuid.uuid4(), firm_id=firm.id, name="M1", mandate_type="discretionary",
                          model_portfolio_id=model.id)
        # A discretionary mandate with NO model -> its account should show up as
        # "uncaptured" in the firm-definition check.
        mandate_unmodeled = Mandate(id=uuid.uuid4(), firm_id=firm.id, name="M-unmodeled", mandate_type="discretionary")
        s.add_all([mandate, mandate_unmodeled])
        await s.flush()

        a1 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate.id, name="A1 Seasoned AAA",
                     currency="USD", cash_balance=Decimal("0"), created_at=seasoned)
        a2 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate.id, name="A2 Seasoned BBB",
                     currency="USD", cash_balance=Decimal("0"), created_at=seasoned)
        a3 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate.id, name="A3 Unseasoned",
                     currency="USD", cash_balance=Decimal("0"), created_at=unseasoned)
        a4 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate.id, name="A4 Below Minimum",
                     currency="USD", cash_balance=Decimal("0"), created_at=seasoned)
        a5 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate_unmodeled.id, name="A5 Uncaptured",
                     currency="USD", cash_balance=Decimal("5000"), created_at=seasoned)
        s.add_all([a1, a2, a3, a4, a5])
        await s.flush()

        s.add_all([
            Holding(firm_id=firm.id, account_id=a1.id, instrument_id=aaa.id,
                    quantity=Decimal("100"), market_value=Decimal("12100"), cost_basis=Decimal("10000")),
            Holding(firm_id=firm.id, account_id=a2.id, instrument_id=bbb.id,
                    quantity=Decimal("100"), market_value=Decimal("7200"), cost_basis=Decimal("10000")),
            Holding(firm_id=firm.id, account_id=a3.id, instrument_id=aaa.id,
                    quantity=Decimal("10"), market_value=Decimal("1210"), cost_basis=Decimal("1000")),
            Holding(firm_id=firm.id, account_id=a4.id, instrument_id=aaa.id,
                    quantity=Decimal("1"), market_value=Decimal("121"), cost_basis=Decimal("100")),
        ])
        await s.flush()

        print("\n=== account_return_series: real per-account monthly returns, not just first/last ===")
        series_a1 = await engine.account_return_series(s, a1.id)
        check("A1 has two periods (Feb, Mar)", len(series_a1["returns"]), 2)
        check("A1 Feb return = +10%", series_a1["returns"][0], 0.10)
        check("A1 Mar return = +10%", series_a1["returns"][1], 0.10)

        series_a2 = await engine.account_return_series(s, a2.id)
        check("A2 Feb return = -10%", series_a2["returns"][0], -0.10)
        check("A2 Mar return = -20%", series_a2["returns"][1], -0.20)

        print("\n=== composite_series: asset-weighted aggregation across accounts ===")
        combined = engine._composite_series([
            {"periods": series_a1["periods"], "returns": series_a1["returns"], "account_value": 12100},
            {"periods": series_a2["periods"], "returns": series_a2["returns"], "account_value": 7200},
        ])
        # Feb: (12100*0.10 + 7200*-0.10) / 19300 = 490/19300
        check("Feb weighted composite return", combined[0], 490 / 19300)
        # Mar: (12100*0.10 + 7200*-0.20) / 19300 = -230/19300
        check("Mar weighted composite return", combined[1], -230 / 19300)

        print("\n=== composite_report: seasoning/minimum exclusions, gross return, dispersion, std dev ===")
        composite = Composite(id=uuid.uuid4(), firm_id=firm.id, model_portfolio_id=model.id,
                              name="Growth Composite", inclusion_criteria="All discretionary Growth accounts.",
                              creation_date=date(2020, 1, 1), seasoning_days=90, minimum_account_size=Decimal("1000"))
        s.add(composite)
        await s.flush()

        report = await engine.composite_report(s, composite)
        check("only the two seasoned, above-minimum accounts qualify", report["accounts_included"], 2)
        check("the unseasoned account is named as excluded", report["accounts_excluded_unseasoned"], ["A3 Unseasoned"])
        check("the below-minimum account is named as excluded", report["accounts_excluded_below_minimum"], ["A4 Below Minimum"])
        check("composite assets = A1 + A2 market value", report["composite_assets"], 19300.0)
        # gross = (12100*0.10 + 7200*-0.20) / 19300 = -230/19300
        check("gross return (latest period, asset-weighted)", report["gross_return"], round(-230 / 19300, 4))
        check("net return is honestly absent, not estimated", report["net_return"], None)
        check("internal dispersion is a real positive number", report["internal_dispersion"] > 0, True)
        check("ex-post std dev used exactly 2 months", report["ex_post_std_dev"]["months_used"], 2)
        check("2 months does not meet the 36-month GIPS minimum", report["ex_post_std_dev"]["meets_gips_minimum"], False)
        check("std dev value is a real positive number", report["ex_post_std_dev"]["value"] > 0, True)
        check("benchmark resolved from the model's benchmark_symbol", report["benchmark"]["symbol"], "BENCH")
        check("benchmark return computed from real price history", report["benchmark"]["return"], 0.10)

        print("\n=== firm_definition_check: catches discretionary AUM not captured in any composite ===")
        fd = await engine.firm_definition_check(s, firm.id)
        check("discretionary AUM includes all 5 accounts", fd["discretionary_aum"], 12100 + 7200 + 1210 + 121 + 5000)
        check("composite-eligible AUM excludes the unmodeled mandate's account",
              fd["composite_eligible_aum"], 12100 + 7200 + 1210 + 121)
        check("uncaptured AUM equals the unmodeled account's value", fd["uncaptured_aum"], 5000.0)
        check("firm definition is not clean", fd["clean"], False)

        print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
        for f in FAIL:
            print(f"  FAILED: {f}")

    await db.dispose()  # otherwise the process hangs on the open pool at exit
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
