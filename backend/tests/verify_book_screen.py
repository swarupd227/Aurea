"""Ask-your-book is the conversation: a question about the whole book is answered by screen_book.

    python -m tests.verify_book_screen

Ask-your-book put a snapshot of the book into a prompt and called a household "overweight" above a
fixed 60%. This checks, against a throwaway SQLite database, that:

  - screen_book reads and changes nothing, is held by the staff who had the Ask page, and no client;
  - equity is measured per mandate against its IPS where one is in force, else its model, outside the
    same band, and a mandate with neither is not measured rather than called over;
  - a loss is what the book shows, and says what it does not check;
  - a values exclusion a mandate does not carry, and a holding against it, are named;
  - filters must all match, an unknown one is refused, and another firm's book never appears;
  - the old route is gone and the page only redirects to the conversation.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import firm_time
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState, tools_for_role, validate_tool_call
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import MarketType, UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.portfolio import Holding, Instrument, InvestmentPolicy, ModelPortfolio, Price, TargetAllocation
from app.models.tenant import Firm

PASS, FAIL = [], []
R = UserRole
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def gating() -> None:
    print("\n=== Read-only, and held by the staff who had the Ask page ===")
    tool = TOOLS["screen_book"]
    check("it changes nothing", tool.change_state, ToolChangeState.NO)
    check("held by", {r.value for r in tool.roles_required},
          {"adviser", "paraplanner", "portfolio_team", "research_cio", "branch_leader"})
    check("a client holds none of it", "screen_book" in tools_for_role(R.CLIENT), False)

    print("\n=== Ask-your-book is retired ===")
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    check("the snapshot route is gone", "/api/studio/ask" in paths, False)
    nav = (FRONTEND / "lib" / "nav.ts").read_text(encoding="utf-8")
    check("the sidebar no longer lists it", "/studio/ask" in nav, False)
    page = (FRONTEND / "app" / "(app)" / "studio" / "ask" / "page.tsx").read_text(encoding="utf-8")
    check("its old links go to the conversation", 'router.replace("/workspace")' in page, True)
    check("and it calls nothing", "api(" in page, False)
    renderer = (FRONTEND / "app" / "workspace" / "components" / "artifacts" / "ArtifactRenderer.tsx").read_text(encoding="utf-8")
    check("the result draws a card", 'case "screen_book":' in renderer and "<BookScreenCard" in renderer, True)
    panes = (FRONTEND / "lib" / "paneViews.ts").read_text(encoding="utf-8")
    check("and opens beside the conversation", 'case "screen_book":' in panes, True)


async def behaviour() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD", jurisdiction="NZ")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="NZD", jurisdiction="NZ")
            s.add_all([firm, other])
            await s.flush()
            today = firm_time.today(firm)

            lee = Household(id=uuid.uuid4(), firm_id=firm.id, name="Lee", values={"exclusions": ["tobacco"]})
            cho = Household(id=uuid.uuid4(), firm_id=firm.id, name="Cho", values={"exclusions": ["tobacco"]})
            zed = Household(id=uuid.uuid4(), firm_id=other.id, name="Zed")
            s.add_all([lee, cho, zed])
            await s.flush()
            ann = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=lee.id, full_name="Ann Lee")
            bo = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=cho.id, full_name="Bo Cho")
            kai = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=cho.id, full_name="Kai Cho", is_next_gen=True)
            zoe = Person(id=uuid.uuid4(), firm_id=other.id, household_id=zed.id, full_name="Zoe Zed", is_next_gen=True)
            s.add_all([ann, bo, kai, zoe])
            await s.flush()
            model = ModelPortfolio(id=uuid.uuid4(), firm_id=firm.id, name="Balanced", drift_band=0.05)
            s.add(model)
            await s.flush()
            s.add_all([TargetAllocation(firm_id=firm.id, model_id=model.id, asset_class="equity", target_weight=0.6),
                       TargetAllocation(firm_id=firm.id, model_id=model.id, asset_class="fixed_income", target_weight=0.4)])
            m_lee = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=ann.id, name="Ann mandate", mandate_type="advisory",
                            model_portfolio_id=model.id, suitability={}, constraints={})
            m_cho = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=bo.id, name="Bo mandate", mandate_type="advisory",
                            suitability={}, constraints={"values_exclusions": ["tobacco"]})
            m_zed = Mandate(id=uuid.uuid4(), firm_id=other.id, person_id=zoe.id, name="Zoe mandate", mandate_type="advisory",
                            suitability={}, constraints={})
            s.add_all([m_lee, m_cho, m_zed])
            await s.flush()

            async def hold(f, mandate, rows):
                acc = Account(id=uuid.uuid4(), firm_id=f.id, mandate_id=mandate.id, name=f"{mandate.name} A/C", custodian="C",
                              currency="NZD", cash_balance=0)
                s.add(acc)
                await s.flush()
                for symbol, cls, value, cost, tags in rows:
                    inst = Instrument(id=uuid.uuid4(), firm_id=f.id, symbol=symbol, name=symbol, asset_class=cls,
                                      market_type=MarketType.PUBLIC, currency="NZD", values_tags=tags)
                    s.add(inst)
                    await s.flush()
                    s.add(Price(firm_id=f.id, instrument_id=inst.id, as_of=today, close=10, currency="NZD", source="test", is_real=True))
                    s.add(Holding(id=uuid.uuid4(), firm_id=f.id, account_id=acc.id, instrument_id=inst.id,
                                  quantity=value / 10, market_value=value, cost_basis=cost))
                await s.flush()

            # Lee: 80% equity against a 60% model, a 20k loss, and 5k in tobacco that no mandate excludes.
            await hold(firm, m_lee, [("EQ", "equity", 75000, 95000, {}), ("TOB", "equity", 5000, 5000, {"flags": ["tobacco"]}),
                                     ("BND", "fixed_income", 20000, 16000, {})])
            # Cho: no model and no IPS, at a gain, a next generation on file, and its mandate carries tobacco.
            await hold(firm, m_cho, [("GOV", "fixed_income", 10000, 9000, {})])
            await hold(other, m_zed, [("ZZ", "equity", 900000, 1000000, {})])

            def run(role=R.ADVISER, f=firm):
                return ToolExecutors(s, uuid.uuid4(), role, f.id)

            print("\n=== The whole book ===")
            out = await run().execute("screen_book", {})
            rows = {r["household"]: r for r in out["households"]}
            check("every household screened, in this firm only", (out["households_screened"], [r["household"] for r in out["households"]]),
                  (2, ["Lee", "Cho"]))
            check("in the firm's currency", out["currency"], "NZD")
            check("it says what a loss does not check", "wash-sale" in out["not_checked"], True)
            lee_eq = rows["Lee"]["equity"][0]
            check("equity is measured against the model", (lee_eq["equity_weight"], lee_eq["equity_target"], lee_eq["measured_against"]),
                  (0.8, 0.6, "model"))
            check("and is over it beyond the band", (lee_eq["over_by"], rows["Lee"]["over_equity_target"]), (0.2, True))
            cho_eq = rows["Cho"]["equity"][0]
            check("a mandate with no model or IPS is not measured", (cho_eq["equity_target"], cho_eq["measured_against"], cho_eq["over_target"]),
                  (None, None, False))
            check("the loss is what the book shows", (rows["Lee"]["unrealised_loss"], rows["Lee"]["positions_at_loss"]), (20000.0, 1))
            check("a gain is no loss", rows["Cho"]["positions_at_loss"], 0)
            check("an exclusion the mandate does not carry is named",
                  rows["Lee"]["values"]["not_on_mandates"], [{"mandate_id": str(m_lee.id), "mandate": "Ann mandate", "not_carried": ["tobacco"]}])
            check("and what is held against it", rows["Lee"]["values"]["held_against_value"], 5000.0)
            check("a mandate that carries it has no gap", rows["Cho"]["values_gap"], False)
            check("the next generation on file", (rows["Lee"]["next_gen"], rows["Cho"]["next_gen"]), ([], ["Kai Cho"]))
            check("counts", out["counts"], {"over_equity_target": 1, "unrealised_loss": 1, "values_gap": 1, "next_gen": 1})

            print("\n=== Filters ===")
            names = lambda o: [r["household"] for r in o["households"]]
            check("over the equity target", names(await run().execute("screen_book", {"only": ["over_equity_target"]})), ["Lee"])
            check("next generation, given as one string", names(await run().execute("screen_book", {"only": "next_gen"})), ["Cho"])
            both = await run().execute("screen_book", {"only": ["over_equity_target", "next_gen"]})
            check("several must all match", (names(both), both["matched"]), ([], 0))
            capped = await run().execute("screen_book", {"limit": 1})
            check("a limit shows the largest, and says how many matched", (names(capped), capped["matched"]), (["Lee"], 2))
            try:
                await run().execute("screen_book", {"only": ["overweight"]})
                check("an unknown filter is refused", False, True)
            except ExecutorError as exc:
                check("an unknown filter is refused, naming the ones there are", "over_equity_target" in str(exc), True)
            check("a client may not run it", validate_tool_call(R.CLIENT, "screen_book", {})[0], False)
            check("nor compliance, who had no Ask page", validate_tool_call(R.COMPLIANCE, "screen_book", {})[0], False)

            print("\n=== An IPS outranks the model ===")
            s.add(InvestmentPolicy(firm_id=firm.id, mandate_id=m_lee.id, version=1, accepted_on=today,
                                   targets={"equity": 0.8, "fixed_income": 0.2}, drift_band=0.05, restricted=[],
                                   document="IPS v1", recorded_by="a@t", recorded_by_role="adviser"))
            await s.flush()
            lee_eq = {r["household"]: r for r in (await run().execute("screen_book", {}))["households"]}["Lee"]["equity"][0]
            check("measured against the IPS, and within it", (lee_eq["equity_target"], lee_eq["measured_against"], lee_eq["over_target"]),
                  (0.8, "ips", False))

            print("\n=== A household that has left ===")
            cho.left_on = today - timedelta(days=1)
            await s.flush()
            check("leaves the screen", (await run().execute("screen_book", {}))["households_screened"], 1)

            print("\n=== The other firm ===")
            theirs = await run(f=other).execute("screen_book", {})
            check("sees only its own book", [r["household"] for r in theirs["households"]], ["Zed"])
    finally:
        await db.dispose()


def main() -> int:
    gating()
    asyncio.run(behaviour())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
