"""Prove India row 8: a Portfolio Manager's client is measured against what its agreement states and what the rules cap.

Covers the agreement as a person recorded it (added, never edited), each check against its cited figure (minimum ticket,
track, segregation, benchmarks, operating expenses, performance fee on a high-water mark, exit loads), "not assessed" where
nothing is recorded, the hold on an approval only for a recorded breach, the billing preview carrying the agreement and a
performance fee that is shown and not added, the quarter's report and why it is not publishable, and the tools.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_pms
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import billing, firm_time, law_figures, pms
from app.core.db import Base, utcnow
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import AgentKey, MandateType, UserRole
from app.models.governance import Recommendation
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.onboarding import FeeSchedule
from app.models.pms import PmsAgreement
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, contains: str | None = None, exc=(pms.PmsError,)) -> None:
    try:
        await coro
    except exc as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:100]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


GOOD = dict(strategy="Growth Strategy", kind="discretionary", signed_on="2026-01-10", document="Signed agreement, Annexure A",
            initial_corpus=12_000_000, segregated=True, benchmarks=["Nifty 50 TRI", "Nifty 500 TRI"], fee_type="hybrid", fixed_rate=0.01,
            performance_rate=0.2, hurdle_rate=0.1, high_water_mark=True, operating_expense_rate=0.004, exit_load=[0.02, 0.01, 0.0])


def keyed(s: dict) -> dict:
    return {c["key"]: c["state"] for c in s["checks"]}


def months(start: date, returns: list[float], value: float = 1_000_000.0) -> list[dict]:
    out, d, v = [], start, value
    for r in returns:
        first_next = (d.replace(day=28) + timedelta(days=4)).replace(day=1)             # the 1st of the month after d's
        end = (first_next.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)   # that month's last day
        out.append({"start": d.isoformat(), "end": end.isoformat(), "return": r, "start_value": v, "end_value": v * (1 + r), "net_flow": 0.0, "income": 0.0})
        d, v = end, v * (1 + r)
    return out


async def main() -> int:
    print("\n=== The figures, with their source ===")
    today = date(2026, 11, 17)
    check("the minimum ticket is Rs 50 lakh", law_figures.value("in_pms_min_ticket", today), 5_000_000)
    check("operating expenses are capped at 0.50%", law_figures.value("in_pms_opex_cap", today), 0.005)
    check("a strategy names at most three benchmarks", law_figures.value("in_pms_benchmarks", today), 3)
    check("exit loads run 3, 2 and 1%", [law_figures.value(f"in_pms_exit_load_y{i}", today) for i in (1, 2, 3)], [0.03, 0.02, 0.01])
    check("each carries the Regulations or the Master Circular", all("Portfolio Managers" in law_figures.get(k).source for k in
                                                                  ("in_pms_min_ticket", "in_pms_opex_cap", "in_pms_benchmarks", "in_pms_exit_load_y1")), True)

    print("\n=== The performance fee, on a high-water mark and a hurdle ===")
    agreement = PmsAgreement(performance_rate=0.2, hurdle_rate=0.10, high_water_mark=True)
    history = months(date(2025, 12, 31), [0.10, -0.05, 0.02, 0.02, 0.02])      # Jan..May: up 10, down 5, then three at 2
    start, end = date(2026, 2, 28), date(2026, 5, 31)
    with_hwm = pms.performance_fee(agreement, history, start, end)
    agreement.high_water_mark = False
    without = pms.performance_fee(agreement, history, start, end)
    check("three months of 2% is 6.12% for the period", with_hwm["period_return"], 0.0612)
    check("the 10% hurdle for the 92 days is 2.52%", with_hwm["hurdle_for_period"], 0.0252)
    check("with the client's own high-water mark the fee is on the climb back above the old peak only", with_hwm["excess_return"], 0.0086)
    check("without one it is on everything above the hurdle", without["excess_return"], 0.036)
    check("so the mark lowers the fee", with_hwm["estimate"] < without["estimate"], True)
    check("the fee is the rate times the excess times the value at the start of the period",
          abs(with_hwm["estimate"] - 0.2 * 0.00857 * 1_045_000) < 60, True)
    check("no returns on file is no estimate, and says why", pms.performance_fee(agreement, [], start, end)["estimate"], None)
    check("the last quarter on the financial year's calendar", pms.last_quarter(date(2026, 11, 25))[2], "Q2 2026-27")
    check("in January it is October to December", (pms.last_quarter(date(2027, 1, 10))[2], pms.last_quarter(date(2027, 1, 10))[0].isoformat()), ("Q3 2026-27", "2026-10-01"))
    check("in April it is the year just ended", pms.last_quarter(date(2026, 4, 5))[2], "Q4 2025-26")

    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="IN", slug="in", base_currency="INR", jurisdiction="IN",
                        settings={"licences": ["investment_adviser", "pms_discretionary", "pms_non_discretionary"], "timezone": "Asia/Kolkata"})
            nz = Firm(id=uuid.uuid4(), name="NZ", slug="nz", base_currency="NZD", jurisdiction="NZ")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="INR", jurisdiction="IN", settings={"licences": ["pms_discretionary"]})
            s.add_all([firm, nz, other])
            await s.flush()
            today = firm_time.today(firm)
            users = {r: User(id=uuid.uuid4(), firm_id=firm.id, email=f"{r.value}@in", full_name=r.value, role=r, hashed_password="x")
                     for r in (UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE, UserRole.ADVISER)}
            s.add_all(users.values())
            hh = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Iyers")
            s.add(hh)
            await s.flush()
            karthik = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="Karthik Iyer")
            ia_client = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="Lakshmi Iyer")
            s.add_all([karthik, ia_client])
            await s.flush()

            async def mandate(owner, track, cash=20_000_000):
                m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=owner.id, name=f"{owner.full_name} - {track}",
                            mandate_type=MandateType.DISCRETIONARY, constraints={"service_track": track})
                s.add(m)
                await s.flush()
                s.add(Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name=f"{owner.full_name} demat", custodian="CDSL", currency="INR", cash_balance=cash))
                await s.flush()
                return m

            pm = await mandate(karthik, "pms_discretionary")
            ia = await mandate(ia_client, "investment_adviser")

            print("\n=== No agreement: not assessed, and nothing is held ===")
            s0 = await pms.status(s, firm, pm)
            check("every check reads not assessed, the agreement included", (keyed(s0), s0["held"]), ({"agreement": "not_assessed"}, []))
            rec = Recommendation(firm_id=firm.id, agent_key=AgentKey.DRIFT_REBALANCING, subject_type="mandate", subject_id=pm.id, title="Rebalance")
            check("an approval is not held for want of an agreement", await pms.refusal(s, firm, rec), None)
            check("the firm's PMS clients are the PMS mandates alone", [c["mandate_id"] for c in (await pms.overview(s, firm))["clients"]], [str(pm.id)])
            check("a New Zealand firm has none", (await pms.overview(s, nz))["applies"], False)
            check("nor does a firm with only an adviser's licence",
                  pms.applies(Firm(id=uuid.uuid4(), name="x", slug="x", jurisdiction="IN", base_currency="INR", settings={"licences": ["investment_adviser"]})), False)

            print("\n=== Recording is a person's, from the signed document ===")
            for label, change, contains in (("a kind that is none of the three", {"kind": "managed"}, "one of"),
                                            ("a strategy with no name", {"strategy": " "}, "Name the strategy"),
                                            ("a day in the future", {"signed_on": (today + timedelta(days=3)).isoformat()}, "future"),
                                            ("no document", {"document": ""}, "signed agreement is on file"),
                                            ("a rate that is not a fraction", {"fixed_rate": 2}, "between 0 and 1"),
                                            ("a performance fee with no rate", {"performance_rate": None}, "needs its rate"),
                                            ("a fee type of the wrong name", {"fee_type": "carry"}, "one of")):
                await refused(f"{label} is refused", pms.prepare(s, firm, mandate_id=pm.id, **{**GOOD, **change}), contains)
            await refused("a mandate on the adviser's track is not a PMS client", pms.prepare(s, firm, mandate_id=ia.id, **GOOD), "not on a Portfolio Manager")
            await refused("another firm's mandate is refused", pms.prepare(s, other, mandate_id=pm.id, **GOOD), "not this firm's")
            check("nothing was written by the refusals", (await s.execute(select(func.count()).select_from(PmsAgreement))).scalar_one(), 0)

            s1 = await pms.record(s, firm, by="pm@in", mandate_id=pm.id, **GOOD)
            check("a complete agreement passes every check", (set(keyed(s1).values()), s1["held"]), ({"pass"}, []))
            check("it names the strategy, the kind and where it is filed", (s1["agreement"]["strategy"], s1["agreement"]["kind_label"], s1["agreement"]["document"]),
                  ("Growth Strategy", "Discretionary", "Signed agreement, Annexure A"))
            check("an approval is not held", await pms.refusal(s, firm, rec), None)
            await refused("the same terms again change nothing", pms.prepare(s, firm, mandate_id=pm.id, **GOOD), "Nothing changes")

            print("\n=== A recorded breach is held, and only a breach ===")

            async def breach(label: str, key: str, **change) -> None:
                out = await pms.record(s, firm, by="pm@in", mandate_id=pm.id, **{**GOOD, **change})
                why = await pms.refusal(s, firm, rec)
                check(f"{label}: the check fails and the approval is held", (keyed(out)[key], bool(why) and "breaks what the Regulations state" in why), ("fail", True))

            await breach("a Rs 40 lakh opening corpus", "ticket", initial_corpus=4_000_000)
            await breach("a non-discretionary agreement on the discretionary track", "kind", kind="non_discretionary")
            await breach("securities pooled", "segregation", segregated=False)
            await breach("four benchmarks", "benchmarks", benchmarks=["A", "B", "C", "D"])
            await breach("operating expenses at 0.60%", "opex", operating_expense_rate=0.006)
            await breach("a performance fee with no high-water mark", "performance_fee", high_water_mark=False)
            await breach("a 4% first-year exit load", "exit_load", exit_load=[0.04, 0.02, 0.01])
            check("a fix is a new record: the latest stands", (await s.execute(select(func.count()).select_from(PmsAgreement))).scalar_one(), 8)
            fixed = await pms.record(s, firm, by="pm@in", mandate_id=pm.id, **GOOD)
            check("and the hold lifts", (fixed["held"], await pms.refusal(s, firm, rec)), ([], None))
            check("a proposal that moves nothing is never held", await pms.refusal(s, firm, Recommendation(
                firm_id=firm.id, agent_key=AgentKey.TAX_INTELLIGENCE, subject_type="mandate", subject_id=pm.id, title="x")), None)
            check("the approval path asks the rule", "pms.refusal" in Path(__import__("app.atlas.runtime", fromlist=["x"]).__file__).read_text(encoding="utf-8"), True)

            print("\n=== The bill carries the agreement ===")
            schedule = FeeSchedule(firm_id=firm.id, code="PMS-100", name="PMS 1%", fee_type="flat_bps", flat_bps=100, currency="INR")
            s.add(schedule)
            await s.flush()
            await billing.activate_arrangement(s, firm_id=firm.id, mandate=pm, fee_schedule_id=schedule.id, billing_method="arrears",
                                               billing_frequency="quarterly", householding=False, effective_from=today - timedelta(days=200),
                                               set_by="x", set_at=utcnow(), confirmed_by="y", confirmed_at=utcnow())
            entry = next(e for e in (await billing.preview(s, firm.id))["arrangements"] if e["mandate_id"] == str(pm.id))
            check("the preview carries the agreement's terms", (entry["pms_fee"]["kind"], entry["pms_fee"]["fixed_rate"]), ("discretionary", 0.01))
            check("a hybrid fee shows its performance part beside the fixed one, never in the amount",
                  (entry["pms_fee"]["performance"]["added_to_bill"], entry["pms_fee"]["performance"]["estimate"]), (False, None))
            check("and says why there is no estimate", "monthly return" in entry["pms_fee"]["performance"]["reason"], True)
            check("the rate on the bill agrees with the agreement", [c for c in entry["checks"] if c["code"] == "pms_fixed_rate_differs"], [])
            await pms.record(s, firm, by="pm@in", mandate_id=pm.id, **{**GOOD, "fixed_rate": 0.015})
            entry = next(e for e in (await billing.preview(s, firm.id))["arrangements"] if e["mandate_id"] == str(pm.id))
            check("a bill at another rate than the agreement states is warned about", [c["severity"] for c in entry["checks"] if c["code"] == "pms_fixed_rate_differs"], ["warn"])
            await pms.record(s, firm, by="pm@in", mandate_id=pm.id, **{**GOOD, "operating_expense_rate": 0.006})
            entry = next(e for e in (await billing.preview(s, firm.id))["arrangements"] if e["mandate_id"] == str(pm.id))
            check("an expense rate over the cap blocks the bill, with the Circular named",
                  (entry["blocked"], any(c["code"] == "pms_opex" and "Master Circular for Portfolio Managers" in c["detail"] for c in entry["checks"])), (True, True))
            await pms.record(s, firm, by="pm@in", mandate_id=pm.id, **GOOD)

            print("\n=== The quarter's report is a draft, never for the client ===")
            report = await pms.client_report(s, firm, pm.id)
            check("it is for the last completed quarter", (report["quarter"], report["start"]), ("Q2 2026-27", "2026-07-01"))
            check("it names the benchmarks, and fetches no value for them", [(b["name"], b["return"]) for b in report["benchmarks"]],
                  [("Nifty 50 TRI", None), ("Nifty 500 TRI", None)])
            check("it is not publishable, and says on what basis", (report["publishable"], "not APMI's TWRR" in report["not_publishable_because"], report["basis"]),
                  (False, True, "monthly-linked, securities only, gross of fees"))
            check("with no monthly return on file, the return is not assessed", (report["return"], report["not_assessed"]), (None, ["No monthly return in the quarter on file"]))
            await refused("an adviser's mandate has no PMS report", pms.client_report(s, firm, ia.id), "not a PMS client")

            print("\n=== The conversation: tools, roles and Confirm cards ===")
            check("recording an agreement stops for a Confirm", TOOLS["record_pms_agreement"].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            check("reading it does not", (TOOLS["read_pms"].change_state, TOOLS["read_pms_report"].change_state), (ToolChangeState.NO, ToolChangeState.NO))
            ex = ToolExecutors(s, users[UserRole.PORTFOLIO_TEAM].id, UserRole.PORTFOLIO_TEAM, firm.id)
            count = (await s.execute(select(func.count()).select_from(PmsAgreement))).scalar_one()
            card = await ex.preview_change("record_pms_agreement", {"mandate_id": str(pm.id), **{**GOOD, "benchmarks": "Nifty 50 TRI", "fixed_rate": 0.0125}})
            labels = [r["label"] for r in card["rows"]]
            check("the card names the strategy, the fee, the checks and the evidence",
                  all(x in labels for x in ("Strategy", "Fixed", "Against the rules", "Evidence")), True)
            check("showing it wrote nothing", (await s.execute(select(func.count()).select_from(PmsAgreement))).scalar_one(), count)
            await refused("compliance reads and does not record",
                          ToolExecutors(s, users[UserRole.COMPLIANCE].id, UserRole.COMPLIANCE, firm.id).preview_change(
                              "record_pms_agreement", {"mandate_id": str(pm.id), **GOOD}), "portfolio team", (ExecutorError,))
            out = await ex.record_pms_agreement({"mandate_id": str(pm.id), **{**GOOD, "benchmarks": "Nifty 50 TRI", "fixed_rate": 0.0125}})
            check("confirmed, it is recorded once", (await s.execute(select(func.count()).select_from(PmsAgreement))).scalar_one(), count + 1)
            check("and the reading carries it", out["clients"][0]["agreement"]["fixed_rate"], 0.0125)
            read = await ex.read_pms({})
            check("the reading counts clients, holds and those with no agreement", (len(read["clients"]), read["held"], read["without_agreement"]), (1, 0, 0))
            check("the report tool says there is nothing to state without a return",
                  "not assessed" in (await ex.read_pms_report({"mandate_id": str(pm.id)}))["message"], True)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
