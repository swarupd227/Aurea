"""Prove goals sized in conversation, and the step-up SIP (B-394, L200-2 India section 5).

  1. An adviser or paraplanner records any goal from the conversation behind a Confirm card, and changes it: each term as
     said, nothing supplied, a tier left unsaid not tiered.
  2. The monthly saving each goal needs, level and with a step-up the person states (no default), on the firm's assumption
     set, with the year-by-year comparison.
  3. Each goal's funded ratio beside the household's, with what neither counts.
  4. Vehicle categories by horizon, India only, as data: no product.
  5. A step-up is a term of the SIP registration: bought as registered, capped, held by the debit mandate, and never
     applied without being registered.
  6. Projections that take contributions count the registrations recorded against a goal, once.
  7. The demonstration's rows, from the seed and its backfill.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_goals
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState, tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import UserRole
from app.models.governance import LedgerEntry
from app.models.graph import Account, Goal, Household, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Instrument, Price
from app.models.tenant import Firm

PASS, FAIL = [], []
BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def section(title: str, fn) -> None:
    """One part's checks. A part that raises is one failure, and the rest still run."""
    print(f"\n=== {title} ===")
    try:
        await fn()
    except Exception as exc:  # noqa: BLE001 - reported as a failure with what raised
        FAIL.append(title)
        print(f"  FAIL  {title}: {type(exc).__name__}: {str(exc)[:300]}")


async def refused(label: str, coro, contains: str) -> None:
    try:
        await coro
    except ExecutorError as e:
        ok = contains.lower() in str(e).lower()
        check(label if ok else f"{label} [{e}]", ok, True)
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(db, expire_on_commit=False)() as s:
            await run(s)
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


async def run(s) -> None:
    from seed import india_demo

    firm = await india_demo.build(s, quotes=None)
    await s.commit()
    from app.aurea_core import firm_time

    today = firm_time.today(firm)
    users = {u.role: u for u in (await s.execute(select(User).where(User.firm_id == firm.id))).scalars().all()}
    homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    sharma, iyer, kapoor, dsouza = (homes[n] for n in ("The Sharma Family", "The Iyer Family", "The Kapoor Family Trust", "The D'Souza Household"))
    adv = ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.ADVISER, firm.id)
    other_firm = Firm(name="Other", slug="other-goals", jurisdiction="IN", base_currency="INR", settings={})
    s.add(other_firm)
    await s.flush()
    outsider = ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.ADVISER, other_firm.id)

    async def goal_named(home, name):
        return (await s.execute(select(Goal).where(Goal.household_id == home.id, Goal.name == name))).scalars().first()

    async def count(model):
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()

    async def rows_of(tool, inputs):
        return {r["label"]: r["value"] for r in (await adv.preview_change(tool, inputs))["rows"]}

    async def seeded():
        names = {g.name for g in (await s.execute(select(Goal).where(Goal.firm_id == firm.id))).scalars().all()}
        check("the demonstration has the three new goals", {"Aarav's wedding", "A second home", "Home renovation"} <= names, True)
        wedding = await goal_named(sharma, "Aarav's wedding")
        check("a wish, as the engines read it", wedding.assumptions["tier"], "wishes")
        from app.models.sip import Sip

        row = (await s.execute(select(Sip).where(Sip.goal_id == wedding.id))).scalars().first()
        check("a simulated step-up SIP saves towards the wedding",
              (row.simulated, float(row.step_up_percent), row.step_up_every_months, float(row.step_up_cap)), (True, 10.0, 12, 30000.0))
        from seed import india_demo_backfill, india_goals, india_planning_fixes

        check("a second run adds nothing", await india_goals.add(s, firm), 0)
        retirement = await goal_named(sharma, "Retirement at 60")
        check("the seeded tiers are read by the engines", retirement.assumptions["tier"], "needs")
        retirement.assumptions = {**retirement.assumptions, "tier": "need"}
        await india_planning_fixes.add(s, firm)
        check("an earlier singular tier is corrected", retirement.assumptions["tier"], "needs")
        check("the backfill runs it", "india_goals.add" in Path(india_demo_backfill.__file__).read_text(encoding="utf-8"), True)

    await section("7. The demonstration rows", seeded)

    async def recording():
        base = {"household_id": str(kapoor.id), "kind": "wedding", "target": 4000000, "target_basis": "today", "target_date": str(today.year + 9),
                "name": "Isha's wedding"}
        n = await count(Goal)
        card = await rows_of("add_goal", base)
        check("the card says each term, and what was not said",
              (card["Goal"], card["Kind"], card["Tier"], card["Saving each month"], card["Priority"], card["Funding share"].startswith("None recorded")),
              ("Isha's wedding", "Wedding", "Not tiered", "Not stated", "Medium (none stated)", True))
        check("the card writes nothing", await count(Goal), n)
        for label, change, says in (
                ("no target is refused", {"target": None}, "target amount"),
                ("no price basis is refused", {"target_basis": None}, "today's money"),
                ("no date is refused", {"target_date": None}, "date"),
                ("a past date is refused", {"target_date": "2001"}, "after today"),
                ("a retirement age on a wedding is refused", {"retirement_age": 60}, "belong"),
                ("a share past the whole is refused", {"funding_share_percent": 50}, "other goals already hold"),
                ("an unknown kind is refused", {"kind": "boat"}, "kind"),
                ("another goal needs a name", {"kind": "other", "name": None}, "name")):
            await refused(label, adv.preview_change("add_goal", {k: v for k, v in {**base, **change}.items() if v is not None}), says)
        await refused("another firm's household", outsider.preview_change("add_goal", base), "no such household")
        done = await adv.add_goal({**base, "tier": "want", "monthly_contribution": 12000, "funding_share_percent": 10, "priority": "low"})
        row = await goal_named(kapoor, "Isha's wedding")
        check("kept as said", (row.kind, float(row.target_amount), row.target_date, row.priority, row.assumptions["tier"], row.assumptions["funding_share"],
                               row.assumptions["monthly_contribution"], row.assumptions["annual_contribution"], row.assumptions["index"]),
              ("wedding", 4000000.0, date(today.year + 9, 12, 31), 3, "wants", 0.1, 12000.0, 144000.0, "cpi"))
        check("the result is that goal, sized, funded, with its categories",
              ([g["name"] for g in done["goals"]], done["goals"][0]["sizing"]["status"], done["goals"][0]["funded"]["status"], done["goals"][0]["vehicles"]["band"]),
              (["Isha's wedding"], "assessed", "assessed", "long"))
        check("it goes on the ledger", (await s.execute(select(func.count()).select_from(LedgerEntry).where(
            LedgerEntry.firm_id == firm.id, LedgerEntry.event_type == "goal_recorded", LedgerEntry.actor == users[UserRole.ADVISER].email))).scalar_one(), 1)
        await refused("the same name twice", adv.add_goal(base), "already on file")
        await refused("a second retirement goal", adv.add_goal({"household_id": str(sharma.id), "kind": "retirement", "target": 1, "target_basis": "today",
                                                              "retirement_age": 65}), "already on file")
        fresh = Household(firm_id=firm.id, name="A new family (test)", values={})
        s.add(fresh)
        await s.flush()
        ret = {"household_id": str(fresh.id), "kind": "retirement", "target": 50000000, "target_basis": "nominal", "retirement_age": 60, "retirement_spending": 1200000}
        await refused("an age with no date of birth gives no date", adv.preview_change("add_goal", ret), "date of birth")
        s.add(Person(firm_id=firm.id, household_id=fresh.id, full_name="Asha Test", date_of_birth=date(1985, 6, 15), kyc={}, profile={}))
        await s.flush()
        await adv.add_goal(ret)
        r = await goal_named(fresh, "Retirement at 60")
        check("retirement with an age: the date from the date of birth, the spending, no tier",
              (r.target_date, r.assumptions["retirement_age"], r.assumptions["annual_income"], r.assumptions.get("tier"), r.assumptions.get("monthly_contribution")),
              (date(2045, 6, 15), 60, 1200000.0, None, None))
        sized = await adv.read_goal_sizing({"household_id": str(fresh.id)})
        check("with nothing managed there is no return to size on: not assessed",
              (sized["goals"][0]["sizing"]["why"], sized["goals"][0]["saving_monthly"], sized["goals"][0]["funded"]["why"]), ("no_holdings", None, "no_funding_share"))

        change = {"goal_id": str(r.id), "retirement_age": 62, "monthly_contribution": 40000, "retirement_spending": 1500000, "funding_share_percent": 25, "name": "Retire at 62"}
        card = await rows_of("update_goal", change)
        check("the change card names each", sorted(set(card) & {"Name", "Retirement age", "Date", "Retirement spending", "Saving each month", "Funding share"}),
              ["Date", "Funding share", "Name", "Retirement age", "Retirement spending", "Saving each month"])
        done = await adv.update_goal(change)
        check("the new age moves the date", (r.target_date, r.assumptions["retirement_age"], r.assumptions["annual_contribution"], r.assumptions["funding_share"], r.name),
              (date(2047, 6, 15), 62, 480000.0, 0.25, "Retire at 62"))
        check("the result names the household, for the page beside it", done["household_id"], str(fresh.id))
        await refused("what is on file is no change", adv.preview_change("update_goal", {"goal_id": str(r.id), "retirement_age": 62}), "say what to change")
        await adv.update_goal({"goal_id": str(r.id), "funding_share_percent": "none"})
        check("a share is cleared", "funding_share" in r.assumptions, False)
        await refused("another firm's goal", outsider.preview_change("update_goal", change), "not found")

        check("a card for each change", [TOOLS[k].change_state for k in ("add_goal", "link_sip_goal", "read_goal_sizing")],
              [ToolChangeState.PAUSE_FOR_CONFIRMATION, ToolChangeState.PAUSE_FOR_CONFIRMATION, ToolChangeState.NO])
        check("adviser and paraplanner record a goal; operations, compliance and a client do not",
              ["add_goal" in tools_for_role(x) for x in (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS, UserRole.COMPLIANCE, UserRole.CLIENT)],
              [True, True, False, False, False])
        check("no client holds any of them", [k for k in ("add_goal", "read_goal_sizing", "link_sip_goal") if k in tools_for_role(UserRole.CLIENT)], [])

    await section("1. Goals recorded and changed as said", recording)

    async def sizing():
        from app.aurea_core import goal_sizing as gs

        end = gs.corpus(100000, gs.required_monthly(5000000, 100000, 0.0, 0.09, 10), 0.0, 0.09, 10)[-1]
        check("the level amount reaches the target, within a rupee a month", 0 <= end - 5000000 < 250, True)
        first = gs.required_monthly(5000000, 100000, 0.10, 0.09, 10)
        check("a step-up starts lower", first < gs.required_monthly(5000000, 100000, 0.0, 0.09, 10), True)
        check("and reaches it too", 0 <= gs.corpus(100000, first, 0.10, 0.09, 10)[-1] - 5000000 < 350, True)
        check("nothing is needed where what is set aside gets there", gs.required_monthly(100000, 100000, 0.0, 0.09, 5), 0.0)
        check("no return: the plain division", gs.required_monthly(120000, 0, 0.0, 0.0, 10), 1000.0)

        plain = await adv.read_goal_sizing({"household_id": str(iyer.id)})
        home = next(g for g in plain["goals"] if g["name"] == "A second home")
        check("with no step-up stated there is none: no default",
              (plain["step_up_percent"], home["sizing"]["step_up"], home["comparison"], plain["total_step_up_monthly"]), (None, None, None, None))
        stepped = await adv.read_goal_sizing({"household_id": str(iyer.id), "step_up_percent": 10})
        home = next(g for g in stepped["goals"] if g["name"] == "A second home")
        z = home["sizing"]
        check("level and stepped side by side", (z["step_up"]["first_monthly"] < z["level_monthly"], z["step_up"]["last_monthly"] > z["step_up"]["first_monthly"],
                                                 z["step_up"]["percent"]), (True, True, 10.0))
        c = home["comparison"]
        check("the comparison starts both at what is saved on it today",
              (c["first_monthly"], c["first_monthly_basis"], len(c["points"]), c["stepped_end"] > c["level_end"], c["points"][0]["level"] == c["points"][0]["stepped"]),
              (100000.0, "saved_on_the_goal", home["years"] + 1, True, True))
        check("the target is in the money of its date", (home["target_basis"], home["target"] > 10000000, home["target_today"]), ("today", True, 10000000.0))
        check("what is assumed is named", (stepped["assumptions_source"] in ("firm", "market", "platform"), stepped["expected"]["compound"] < stepped["expected"]["mean"],
                                           "even chance" in stepped["basis"], "held_away" in stepped["not_counted"]), (True, True, True, True))
        check("what is short each month", home["short_by_monthly"], round(max(z["level_monthly"] - 100000, 0), 2))
        check("Astra's words say it is not a likelihood", "not a likelihood" in stepped["message"], True)
        await refused("a step-up of nothing", adv.read_goal_sizing({"household_id": str(iyer.id), "step_up_percent": 0}), "more than 0")
        await refused("another firm's household", outsider.read_goal_sizing({"household_id": str(iyer.id)}), "not found")
        # The firm's assumption set, not a rate of this module's own.
        before = home["sizing"]["level_monthly"]
        firm.settings = {**firm.settings, "cmas": {k: [0.02, 0.01] for k in ("equity", "fixed_income", "cash", "alternatives", "property", "commodity", "multi_asset")}}
        await s.flush()
        low = await adv.read_goal_sizing({"household_id": str(iyer.id)})
        check("a lower return on the firm's set asks for more", next(g for g in low["goals"] if g["name"] == "A second home")["sizing"]["level_monthly"] > before, True)
        check("and says the returns are the firm's", low["assumptions_source"], "firm")
        firm.settings = {k: v for k, v in firm.settings.items() if k != "cmas"}
        await s.flush()
        one = await adv.read_goal_sizing({"household_id": str(iyer.id), "goal_id": home["goal_id"]})
        check("one goal on its own", [g["name"] for g in one["goals"]], ["A second home"])
        await refused("another household's goal", adv.read_goal_sizing({"household_id": str(sharma.id), "goal_id": home["goal_id"]}), "not this household's")

    await section("2. The monthly saving, level and stepped up", sizing)

    async def ratios():
        from app.aurea_core import funded, goal_tradeoff, retirement
        from app.aurea_core.graph import household_brain

        brain = await household_brain(s, iyer.id, firm_id=firm.id)
        likely = goal_tradeoff.likelihoods(brain, today)
        plan = await retirement.for_household(s, iyer.id, firm_id=firm.id)
        f = funded.ratio(brain, likely, plan)
        by = {r["name"]: r for r in f["by_goal"]}
        book, rate = brain["totals"]["total_value"], f["discount_rate"]
        home = next(g for g in likely["goals"] if g["name"] == "A second home")
        check("a goal's ratio is its share of the book over its present value",
              by["A second home"]["ratio"], round(book * 0.1 / (home["target_amount"] / (1 + rate) ** home["years"]), 3))
        check("retirement's need is the retirement plan's spending", (by["Retirement at 58"]["need_basis"], by["Retirement at 58"]["pv_need"]),
              ("retirement_spending", plan["pv_spending"]))
        ret = next(g for g in likely["goals"] if g["name"] == "Retirement at 58")
        check("with the ratio to its own target beside it, which the monthly figure is sized on",
              (by["Retirement at 58"]["ratio_to_target"], by["A second home"]["ratio_to_target"]),
              (round(book * 0.9 / (ret["target_amount"] / (1 + rate) ** ret["years"]), 3), None))
        brain = await household_brain(s, sharma.id, firm_id=firm.id)
        f = funded.ratio(brain, goal_tradeoff.likelihoods(brain, today), None)
        wedding = next(r for r in f["by_goal"] if r["name"] == "Aarav's wedding")
        check("no share recorded: not assessed, the book is not shared out", (wedding["status"], wedding["why"], wedding["ratio"]), ("not_assessed", "no_funding_share", None))
        check("what is not counted is named", f["not_counted_by_goal"], ["contributions", "future_earnings", "held_away", "unassigned_book", "other_goals_shares"])
        check("the household's ratio is beside it, as before", (f["ratio"] is not None, f["not_counted"]), (True, ["contributions", "future_earnings", "held_away"]))

    await section("3. A funded ratio for each goal", ratios)

    async def vehicles():
        from app.aurea_core import goal_vehicles as gv, markets

        check("the bands", [gv.band_of(y) for y in (1, 2, 3, 6, 7, 20)], ["short", "short", "medium", "medium", "long", "long"])
        cats = lambda kind, y: [r["category"] for r in gv.for_goal(kind, y, today)["rows"]]  # noqa: E731
        check("short: deposits and debt, no equity", (any("Fixed deposits" == c for c in cats("other", 2)), any("Equity" in c for c in cats("other", 2))), (True, False))
        check("long education carries the tax-sheltered companions", {"Public Provident Fund", "Sukanya Samriddhi Account"} <= set(cats("education", 10)), True)
        check("a wedding does not", "Sukanya Samriddhi Account" in cats("wedding", 10), False)
        edu = gv.for_goal("education", 10, today)
        check("the age rule is a law figure", edu["law"][0]["key"], "in_ssy_rule")
        check("each has its reason, and the source says it is a convention", (all(r["reason"] for r in edu["rows"]), "not law" in edu["source"], "not advice" in edu["note"]),
              (True, True, True))
        check("India only", (gv.applies(markets.get("IN")), gv.applies(markets.get("US")), gv.applies(None)), (True, False, False))
        names = {i.name for i in (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()}
        every = {c for kind in ("education", "other") for y in (1, 5, 10) for c in cats(kind, y)}
        check("no category is a product on the shelf", sorted(every & names), [])
        short = await adv.read_goal_sizing({"household_id": str(dsouza.id)})
        check("the renovation in two years reads short", next(g for g in short["goals"] if g["name"] == "Home renovation")["vehicles"]["band"], "short")

    await section("4. Vehicle categories by horizon", vehicles)

    async def step_up():
        from app.aurea_core import sip as sip_core, sip_mandates, sip_step_up as su
        from app.models.sip import Sip

        t = {"id": "x", "amount": 10000.0, "day": 5, "starts_on": date(2026, 1, 1), "ends_on": None, "step_up_percent": 10.0,
             "step_up_every_months": 12, "step_up_cap": 12500.0}
        check("level for a year, then up, then capped", [su.amount_on(t, date(y, m, 5)) for y, m in ((2026, 1), (2026, 12), (2027, 1), (2028, 1), (2029, 1), (2030, 1))],
              [10000.0, 10000.0, 11000.0, 12100.0, 12500.0, 12500.0])
        check("half-yearly", su.amount_on({**t, "step_up_every_months": 6, "step_up_cap": None}, date(2027, 1, 5)), 12100.0)
        level = {**t, "step_up_percent": None, "step_up_every_months": None, "step_up_cap": None}
        check("with no step-up on it, never", su.amount_on(level, date(2040, 1, 5)), 10000.0)
        check("the next step", su.next_step(t, date(2026, 3, 5)), {"on": "2027-01-05", "amount": 11000.0})
        check("none once capped", su.next_step(t, date(2029, 3, 5)), None)
        years = su.yearly([t], date(2025, 12, 31), 3)
        check("the schedule by year", years, [120000.0, 132000.0, 145200.0])
        check("and it stops with the SIP", su.yearly([{**t, "ends_on": date(2026, 6, 30)}], date(2025, 12, 31), 2), [60000.0, 0.0])

        wedding = await goal_named(sharma, "Aarav's wedding")
        seeded_sip = (await s.execute(select(Sip).where(Sip.goal_id == wedding.id))).scalars().one()
        account = await s.get(Account, seeded_sip.account_id)
        fund = await s.get(Instrument, seeded_sip.instrument_id)
        listed = next(x for x in (await adv.read_sips({"household_id": str(sharma.id)}))["sips"] if x["id"] == str(seeded_sip.id))
        check("the list shows the next instalment stepped, the registered first one, the step-up and the goal",
              (listed["amount"], listed["registered_amount"], listed["step_up"]["percent"], listed["goal"]), (16500.0, 15000.0, 10.0, "Aarav's wedding"))
        base = {"account_id": str(account.id), "instrument": fund.symbol, "amount": 5000, "day_of_month": 3, "starts_on": today.isoformat(), "simulated": True}
        card = await rows_of("record_sip", base)
        check("a registration with none says so", card["Step-up"], "None on the registration")
        card = await rows_of("record_sip", {**base, "step_up_percent": 10, "step_up_every_months": 12, "step_up_cap": 8000, "goal_id": str(wedding.id)})
        check("the card says the step-up, its first date and the goal",
              ("10% a year" in card["Step-up"], "never above" in card["Step-up"], "5,500" in card["First step-up"], card["Saves towards"], "Debit mandate" in card),
              (True, True, True, "Aarav's wedding", True))
        n = await count(Sip)
        await refused("a percentage with no frequency", adv.preview_change("record_sip", {**base, "step_up_percent": 10}), "how often")
        await refused("a frequency with no percentage", adv.preview_change("record_sip", {**base, "step_up_every_months": 12}), "percentage")
        await refused("a cap below the instalment", adv.preview_change("record_sip", {**base, "step_up_percent": 10, "step_up_every_months": 12, "step_up_cap": 4000}), "cap")
        await refused("another household's goal", adv.preview_change("record_sip", {**base, "goal_id": str((await goal_named(iyer, "A second home")).id)}), "another household")
        check("nothing was written", await count(Sip), n)

        # The worker buys the stepped instalment: a real NAV and the cash for it.
        for p in (await s.execute(select(Price).where(Price.instrument_id == fund.id))).scalars().all():
            await s.delete(p)
        await s.flush()
        s.add(Price(firm_id=firm.id, instrument_id=fund.id, as_of=today, close=80, currency="INR", source="test", is_real=True))
        account.cash_balance = 1_000_000
        level_sip = await sip_core.record(s, firm, account.id, by="adviser", instrument=fund.symbol, amount=4000, day_of_month=25,
                                          starts_on=today - timedelta(days=800), ends_on=None, evidence_ref=None, simulated=True)
        # The next 25th on or after today: the seeded debit mandate starts on the day the seed ran.
        day = sip_core.next_instalment(25, today, today)
        for other in (await s.execute(select(Sip).where(Sip.firm_id == firm.id, Sip.id.notin_([seeded_sip.id, level_sip.id])))).scalars().all():
            other.last_month = day.strftime("%Y-%m")
        seeded_sip.last_month = level_sip.last_month = None
        await s.flush()
        due = su.amount_on(su.terms(seeded_sip), day)
        ran = await sip_core.run_due(s, firm, today=day)
        got = seeded_sip.history[-1]
        check("both are bought", (ran["bought"], got.get("status"), got.get("reason")), (2, "bought", None))
        check("the stepped SIP buys the stepped amount, units at the NAV", (got["amount"], got["step_ups"], got["units"]), (due, 1, round(due / 80, 3)))
        check("the level SIP, 800 days old, still buys what it registered", (level_sip.history[-1]["amount"], "step_ups" in level_sip.history[-1]), (4000.0, False))

        # A debit mandate below the stepped amount is why it is missed.
        behind = await sip_mandates.for_sip(s, seeded_sip, today)
        check("the demonstration's SIP has a debit mandate whose limit is its cap", (behind["state"], float(behind["row"].per_debit_limit)), ("active", 30000.0))
        await sip_mandates.record(s, firm, UserRole.ADVISER, by="adviser", account_id=account.id, reference=behind["row"].reference, mandate_type="nach",
                                  per_debit_limit=15500, starts_on=today, last4="3947", status="active", document="Registration, filed")
        warned = await sip_mandates.for_sip(s, seeded_sip, today)
        check("the stepped instalment is above the mandate's limit, though the first was not", (warned["fits"], "limit" in (warned["warning"] or "")), (False, True))
        card = await rows_of("record_sip", {**base, "amount": 15000, "step_up_percent": 10, "step_up_every_months": 12})
        check("the card warns where a step passes the limit", "above its limit" in card["Debit mandate"], True)
        seeded_sip.last_month = None
        await sip_core.run_due(s, firm, today=day)
        check("and the instalment is missed with that reason", (seeded_sip.history[-1]["status"], "limit" in seeded_sip.history[-1]["reason"]), ("missed", True))
        boot = (BACKEND / "app" / "db_bootstrap.py").read_text(encoding="utf-8")
        check("a deployed database gets the columns", all(f"ALTER TABLE sip ADD COLUMN IF NOT EXISTS {c}" in boot for c in
                                                          ("step_up_percent", "step_up_every_months", "step_up_cap", "goal_id")), True)

    await section("5. The step-up SIP", step_up)

    async def projections():
        from app.aurea_core import goal_tradeoff, retirement, return_need, scenarios, sip as sip_core
        from app.aurea_core.graph import household_brain
        from app.models.sip import Sip

        brain = await household_brain(s, sharma.id, firm_id=firm.id)
        goals = {g["name"]: g for g in goal_tradeoff.prepare(brain, today)["goals"]}
        w = goals["Aarav's wedding"]
        check("the wedding's saving is its registrations' schedule, rising",
              (w["contribution_basis"], w["schedule"][1] > w["schedule"][0] > 0), ("sip_registration", True))
        check("and stops with the registration", w["schedule"][12], 0.0)
        check("a goal with no SIP keeps what is recorded on it", (goals["Retirement at 60"]["contribution_basis"], goals["Retirement at 60"]["schedule"]), ("recorded", None))
        with_sip = next(g for g in goal_tradeoff.likelihoods(brain, today)["goals"] if g["name"] == "Aarav's wedding")
        bare = {**brain, "goals": [{k: v for k, v in g.items() if k != "sips"} for g in brain["goals"]]}
        without = next(g for g in goal_tradeoff.likelihoods(bare, today)["goals"] if g["name"] == "Aarav's wedding")
        check("the likelihood counts it", with_sip["success_probability"] >= without["success_probability"] and with_sip["projected_median"] > without["projected_median"], True)
        need = return_need.lay_out(brain, today)
        check("the return the needs require still reads", need["status"], "assessed")

        goal = await goal_named(iyer, "Retirement at 58")
        before = await retirement.for_household(s, iyer.id, firm_id=firm.id)
        check("before: the figure recorded on the goal", before["contribution"], {"basis": "recorded", "first_year": 9000000.0})
        account = (await s.execute(select(Account).join(Mandate, Mandate.id == Account.mandate_id).where(
            Mandate.person_id == people["Karthik Iyer"].id))).scalars().first()
        row = await sip_core.record(s, firm, account.id, by="adviser", instrument="PPFAS-FLEXI-DIRECT", amount=900000, day_of_month=10, starts_on=today,
                                    ends_on=None, evidence_ref=None, simulated=True, step_up_percent=10, step_up_every_months=12)
        card = await rows_of("link_sip_goal", {"sip_id": str(row.id), "goal_id": str(goal.id)})
        check("the link card", card["Saves towards"], "No goal becomes Retirement at 58")
        check("which writes nothing", row.goal_id, None)
        done = await adv.link_sip_goal({"sip_id": str(row.id), "goal_id": str(goal.id)})
        after = await retirement.for_household(s, iyer.id, firm_id=firm.id)
        check("the retirement plan counts the registration in place of the recorded figure",
              (after["contribution"]["basis"], 9_000_000 <= after["contribution"]["first_year"] <= 12_150_000), ("sip_registration", True))
        check("so the pot is larger", after["projected_at_retirement"] > before["projected_at_retirement"], True)
        check("Astra says which goal", "Retirement at 58" in done["message"], True)
        ran = await scenarios.run(s, firm, iyer.id, {"extra_contribution": 500000})
        check("a scenario adds to the schedule", ran["after"]["retirement_success"] >= ran["before"]["retirement_success"], True)
        what_if = await retirement.for_household(s, iyer.id, firm_id=firm.id, overrides={"annual_contribution": 0})
        check("a what-if figure still replaces it", what_if["contribution"], {"basis": "what_if", "first_year": 0.0})
        await refused("linking it again is no change", adv.preview_change("link_sip_goal", {"sip_id": str(row.id), "goal_id": str(goal.id)}), "already")
        await refused("another household's goal", adv.preview_change("link_sip_goal", {"sip_id": str(row.id), "goal_id": str((await goal_named(sharma, "Retirement at 60")).id)}),
                      "another household")
        await sip_core.end(s, firm, row.id, by="adviser", reason="Test ended")
        ended = await retirement.for_household(s, iyer.id, firm_id=firm.id)
        check("an ended SIP is not counted", ended["contribution"]["basis"], "recorded")

    await section("6. Projections count the registrations once", projections)

    async def screens():
        pane = (FRONTEND / "lib" / "paneViews.ts").read_text(encoding="utf-8")
        renderer = (FRONTEND / "app" / "workspace" / "components" / "artifacts" / "ArtifactRenderer.tsx").read_text(encoding="utf-8")
        check("each tool has its card and opens the plan beside the conversation",
              [(f'case "{k}":' in pane, f'case "{k}":' in renderer) for k in ("add_goal", "read_goal_sizing", "link_sip_goal", "update_goal")],
              [(True, True)] * 4)
        view = (FRONTEND / "components" / "views" / "FinancialPlanView.tsx").read_text(encoding="utf-8")
        check("the plan page carries the goals", "/api/plan/goals" in view and "GoalSizingBody" in view, True)
        from app.api import financial_plan as api

        check("its route", "/api/plan/goals" in [r.path for r in api.router.routes], True)

    await section("8. Card, pane and page", screens)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
