"""The simulated WealthSmart and Dynamics 365 on its demonstration firm (B-221, B-222), end to end.

Part of the simulations: delete this file with `app/simulations/wealth_platform` and `dynamics_demo`. The real MCP
adapter is proved on its own in `tests.verify_foundation_mcp`; this proves the simulation that stands in for the server.

  - the simulated firm is made with its people (offered on the login page through the firm's own personas), the
    instruments and models the platform's book names, and the live market-data connector, and no client data;
  - seeding connects it: an MCP connector in mock mode tagged simulated, parties, accounts, positions and activity owned
    by it, a first sync through the in-process server, the declared capabilities (no tax lots: not assessed), each
    household's adviser recorded, and the Foundation read showing the platform with a simulated chip;
  - a direct order goes to the platform and waits; the next sync brings the fill at the firm's real price, and the
    book moves only then;
  - Dynamics 365 on the same firm: the CRM owned by it, synced, simulated; discarding hands the CRM back;
  - discarding the platform removes the firm and every row in it.

    python -m tests.verify_sim_wealth_platform
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.config import settings
from app.core.db import Base
from app.core.personas import firm_personas
from app.foundation import owners
from app.llm.executors import ToolExecutors
from app.models.crm import CrmContact
from app.models.graph import Account, Household, Mandate, RelationshipEdge
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Price, TaxLot
from app.models.tenant import Firm
from app.models.trading import Order

PASS, FAIL = [], []
# Real closes as a feed would give them; the test cannot reach one.
CLOSES = {"VTI": 301.25, "VXUS": 66.4, "BND": 72.9, "AAPL": 228.6, "MSFT": 415.1, "JNJ": 155.3, "GLD": 241.7}


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {str(got)[:160]}, want {str(want)[:160]}")


async def main() -> int:
    import app.simulations as sims
    from app.simulations import dynamics_demo, wealth_platform

    settings.simulations = True
    sims.install_all()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)

        print("\n=== The simulated demonstration firm ===")
        async with Session() as s:
            firm = await wealth_platform.ensure_firm(s, fetch_prices=False)
            again = await wealth_platform.ensure_firm(s, fetch_prices=False)
            check("made once, marked simulated, in the US", (again.id == firm.id, firm.settings.get("simulated_firm"),
                                                           firm.jurisdiction, firm.base_currency), (True, "wealth_platform", "US", "USD"))
            users = (await s.execute(select(User).where(User.firm_id == firm.id))).scalars().all()
            offered = firm_personas([firm])
            check("its people are offered on the login page, each a user of the firm",
                  (sorted(p["email"] for p in offered) == sorted(u.email for u in users), {p["firm_slug"] for p in offered},
                   all(p["simulated"] for p in offered)), (True, {"platform-sim"}, True))
            check("no client data of its own", (await s.execute(select(func.count()).select_from(Household).where(
                Household.firm_id == firm.id))).scalar_one(), 0)
            for i in (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all():
                s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=date.today(), close=CLOSES[i.symbol], currency="USD",
                            source="yahoo", is_real=True))
            await s.commit()

        print("\n=== Seeded: the platform owns the book ===")
        async with Session() as s:
            firm = await s.get(Firm, firm.id)
            out = await wealth_platform.seed(s, firm)
            await s.commit()
            check("a first sync through the in-process MCP server", (out["owners_set"], out["records_synced"] > 20, out["adviser_edges"]),
                  (4, True, 3))
            check("the four domains are the platform's, prices the firm's own",
                  [owners.owner(firm, d) for d in ("parties", "accounts", "positions", "activity", "prices", "crm")],
                  ["foundation.mcp"] * 4 + ["native", "native"])
            check("no tax lots: tax and rebalancing read not assessed", "no tax lots" in (owners.lacks(firm, "tax_lots") or ""), True)
            check("and none were made", (await s.execute(select(func.count()).select_from(TaxLot).where(
                TaxLot.firm_id == firm.id))).scalar_one(), 0)
            hh = (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()
            check("three invented households, sourced from the platform",
                  sorted((h.name, h.source_system) for h in hh)[0], ("The Harrington Family", "foundation.mcp"))
            edges = (await s.execute(select(RelationshipEdge).where(RelationshipEdge.firm_id == firm.id))).scalars().all()
            check("each household's adviser recorded, tagged", {(e.attributes or {}).get("source") for e in edges},
                  {"simulated:wealth_platform"})
            vti = (await s.execute(select(Holding).join(Account).join(Instrument, Instrument.id == Holding.instrument_id).where(
                Account.external_id == "WS-A-5001", Instrument.symbol == "VTI"))).scalar_one()
            check("valued at the firm's own real price", round(float(vti.market_value), 2), round(620 * CLOSES["VTI"], 2))
            ops = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == "operations"))).scalar_one()
            src = await ToolExecutors(s, ops.id, ops.role, firm.id).read_foundation_sources({})
            pos = next(r for r in src["domains"] if r["domain"] == "positions")
            check("the Foundation read names the platform and says simulated",
                  (pos["platform"], pos["simulated"], pos["mode"], src["simulated"]), ("Wealth platform (simulated)", True, "mock", True))
            again = await wealth_platform.seed(s, firm)
            check("seeding again adds nothing", (again["connector"], again["owners_set"], again["records_synced"],
                                                 again["adviser_edges"]), (0, 0, 0, 0))
            await s.commit()

        print("\n=== A direct order goes to the platform ===")
        async with Session() as s:
            firm = await s.get(Firm, firm.id)
            adviser = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == "adviser"))).scalar_one()
            joint = (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.external_id == "WS-M-4001"))).scalar_one()
            ann = ToolExecutors(s, adviser.id, adviser.role, firm.id)
            order_in = {"mandate_id": str(joint.id), "orders": [{"symbol": "VTI", "side": "buy", "quantity": 5}],
                        "reason": "Adding to the core holding"}
            card = {r["label"]: r["value"] for r in (await ann.preview_change("execute_orders", order_in))["rows"]}
            check("the card names the platform as where it goes", card["Venue"].startswith("Wealth platform (MCP): sent"), True)
            done = await ann.execute_orders(order_in)
            acct = (await s.execute(select(Account).where(Account.firm_id == firm.id, Account.external_id == "WS-A-5001"))).scalar_one()
            check("sent and waiting, the book unmoved", (done["orders_waiting"], float(vti.quantity), float(acct.cash_balance)),
                  (1, 620.0, 42500.0))
            await ToolExecutors(s, ops.id, ops.role, firm.id).sync_foundation_source({"domain": "positions"})
            vti = await s.get(Holding, vti.id)
            await s.refresh(acct)
            order = (await s.execute(select(Order).where(Order.firm_id == firm.id))).scalar_one()
            check("the platform filled it at the real price, and the sync booked it",
                  (str(order.status), float(vti.quantity), round(float(acct.cash_balance), 2), round(float(order.avg_fill_price), 2)),
                  ("filled", 625.0, round(42500 - 5 * CLOSES["VTI"], 2), CLOSES["VTI"]))
            await s.commit()

        print("\n=== Dynamics 365 on the simulated firm ===")
        async with Session() as s:
            found = await dynamics_demo.firms(s)
            check("named for the simulated firm only", [f.slug for f in found], ["platform-sim"])
            firm = found[0]
            out = await dynamics_demo.seed(s, firm)
            await s.commit()
            check("the CRM is Dynamics 365's, synced", (owners.owner(firm, "crm"), out["records_synced"] > 0), ("crm.dynamics", True))
            src = await ToolExecutors(s, ops.id, ops.role, firm.id).read_foundation_sources({})
            crm = next(r for r in src["domains"] if r["domain"] == "crm")
            check("and shown simulated", (crm["owner"], crm["simulated"]), ("crm.dynamics", True))
            await dynamics_demo.discard(s, firm)
            await s.commit()
            check("discarding hands the CRM back and removes what it brought",
                  (owners.owner(firm, "crm"), (await s.execute(select(func.count()).select_from(CrmContact).where(
                      CrmContact.firm_id == firm.id))).scalar_one()), ("native", 0))

        print("\n=== Discarding the platform removes the firm ===")
        async with Session() as s:
            firm = await s.get(Firm, firm.id)
            fid = firm.id
            await wealth_platform.discard(s, firm)
            await s.commit()
        async with Session() as s:
            left = {}
            for t in Base.metadata.sorted_tables:
                if "firm_id" in t.c:
                    n = (await s.execute(select(func.count()).select_from(t).where(t.c.firm_id == fid))).scalar_one()
                    if n:
                        left[t.name] = n
            check("no row of it is left anywhere", (left, await s.get(Firm, fid)), ({}, None))
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
