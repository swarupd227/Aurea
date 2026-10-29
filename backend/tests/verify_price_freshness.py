"""Prove the price-freshness view, the refresh, and the data-quality read built on it.

Freshness is read from the prices themselves - per held instrument, on the basis it is carried
at, against the limit that applies - not from when a connector last ran. The refresh is the
scheduled fetch on demand: confirm-gated, role-limited, and refused when the feed is off or ran
moments ago. Runs against a throwaway SQLite database; the provider is stubbed.

    python -m tests.verify_price_freshness
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import prices as prices_api
from app.aurea_core import price_freshness as pf
from app.conduit import service
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.connectors import Connector, ConnectorSync
from app.models.enums import ConnectorDomain, ConnectorStatus, MarketType, UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.portfolio import Holding, Instrument, Price
from app.models.tenant import Firm

PASS, FAIL = [], []
UTC = timezone.utc


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def rules() -> None:
    print("\n=== What each age means ===")
    pub, prv = MarketType.PUBLIC, MarketType.PRIVATE
    check("a quote from this week is current", pf.classify(pub, "market", 3)[0], "current")
    check("a long weekend is still current", pf.classify(pub, "market", 5)[0], "current")
    check("a fortnight old is ageing", pf.classify(pub, "market", 14)[0], "ageing")
    check("past the billing limit is stale", pf.classify(pub, "market", 36)[0], "stale")
    check("at the billing limit is not yet stale", pf.classify(pub, "market", 35)[0], "ageing")
    check("a stated valuation is current for a quarter", pf.classify(prv, "declared", 90)[0], "current")
    check("and stale after the private limit", pf.classify(prv, "declared", 101)[0], "stale")
    check("a placeholder is synthetic however new", pf.classify(prv, "synthetic", 1)[0], "synthetic")
    check("no price is missing", pf.classify(pub, None, None)[0], "missing")
    check("the limits are billing's own", (pf.classify(pub, "market", 1)[1], pf.classify(prv, "declared", 1)[1]), (35, 100))

    print("\n=== When prices are fetched next ===")
    fri_night = datetime(2026, 10, 30, 23, 0, tzinfo=UTC)         # Friday 23:00 UTC
    nxt = pf.next_runs(fri_night)
    check("after Friday's last run comes Monday morning", nxt[0]["at"], "2026-11-02T06:00:00+00:00")
    check("then Monday night", nxt[1]["at"], "2026-11-02T22:00:00+00:00")
    tue_early = datetime(2026, 10, 27, 5, 0, tzinfo=UTC)
    check("early on a weekday: this morning then tonight",
          [r["at"] for r in pf.next_runs(tue_early)], ["2026-10-27T06:00:00+00:00", "2026-10-27T22:00:00+00:00"])
    check("a run exactly now is not the next one", pf.next_runs(datetime(2026, 10, 27, 6, 0, tzinfo=UTC))[0]["at"],
          "2026-10-27T22:00:00+00:00")
    check("the schedule is the worker's one definition", [(h, m) for h, m, _ in pf.SCHEDULE], [(6, 0), (22, 0)])


async def main() -> int:
    rules()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        today = date(2026, 10, 27)
        now = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)

        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="NZD")
            s.add_all([firm, other])
            await s.flush()

            def inst(f, sym, kind=MarketType.PUBLIC):
                i = Instrument(id=uuid.uuid4(), firm_id=f.id, symbol=sym, name=sym, asset_class="equity",
                               market_type=kind, currency="NZD")
                s.add(i)
                return i

            fresh, ageing, stale = inst(firm, "FRESH"), inst(firm, "AGEING"), inst(firm, "STALE")
            nothing, placeholder, stated = inst(firm, "NOPRICE"), inst(firm, "PPEF1", MarketType.PRIVATE), inst(firm, "PPEF2", MarketType.PRIVATE)
            unheld, theirs = inst(firm, "UNHELD"), inst(other, "THEIRS")
            await s.flush()

            def px(i, days, source="yahoo", real=True):
                s.add(Price(firm_id=i.firm_id, instrument_id=i.id, as_of=today - timedelta(days=days), close=10.0,
                            currency="NZD", source=source, is_real=real))

            px(fresh, 2)
            px(ageing, 14)
            px(stale, 80)
            px(stale, 200)                                   # an older one must not be mistaken for the latest
            px(placeholder, 290, "synthetic", False)
            px(stated, 60, "declared_valuation", False)
            px(unheld, 400)
            px(theirs, 1)
            hh = Household(id=uuid.uuid4(), firm_id=firm.id, name="H")
            s.add(hh)
            await s.flush()
            person = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="P")
            s.add(person)
            await s.flush()
            m1 = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=person.id, name="M1", mandate_type="advisory", is_active=True)
            m2 = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=person.id, name="M2", mandate_type="advisory", is_active=True)
            s.add_all([m1, m2])
            await s.flush()
            a1 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m1.id, name="A1", currency="NZD", cash_balance=Decimal("0"))
            a2 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m2.id, name="A2", currency="NZD", cash_balance=Decimal("0"))
            s.add_all([a1, a2])
            await s.flush()

            def hold(acct, i, value):
                s.add(Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=acct.id, instrument_id=i.id,
                              quantity=Decimal("1"), market_value=Decimal(str(value)), cost_basis=Decimal("0")))

            hold(a1, fresh, 1000)
            hold(a1, ageing, 2000)
            hold(a1, stale, 3000)
            hold(a1, placeholder, 4000)
            hold(a2, fresh, 500)
            hold(a2, stated, 500)
            hold(a2, nothing, 100)
            await s.flush()

            print("\n=== Per instrument, worst first ===")
            r = await pf.price_freshness(s, firm.id, today=today, now=now)
            rows = r["instruments"]
            check("only held instruments are listed", sorted(x["symbol"] for x in rows),
                  sorted(["FRESH", "AGEING", "STALE", "NOPRICE", "PPEF1", "PPEF2"]))
            check("another firm's instrument is never listed", "THEIRS" in [x["symbol"] for x in rows], False)
            check("worst first: missing, placeholder, stale, ageing, then current",
                  [x["status"] for x in rows], ["missing", "synthetic", "stale", "ageing", "current", "current"])
            by = {x["symbol"]: x for x in rows}
            check("the latest price is read, not an older one", (by["STALE"]["age_days"], by["STALE"]["status"]), (80, "stale"))
            check("a placeholder says what it is", (by["PPEF1"]["basis"], by["PPEF1"]["status"]), ("synthetic", "synthetic"))
            check("a stated valuation is declared and current", (by["PPEF2"]["basis"], by["PPEF2"]["status"]), ("declared", "current"))
            check("an instrument with no price is missing, with no age", (by["NOPRICE"]["status"], by["NOPRICE"]["age_days"]), ("missing", None))
            check("value is summed across accounts", by["FRESH"]["value_held"], 1500.0)
            check("and holdings counted", by["FRESH"]["holdings"], 2)
            check("each carries its own limit", (by["FRESH"]["limit_days"], by["PPEF2"]["limit_days"]), (35, 100))

            print("\n=== The summary says how much of the book rests on a price that cannot be trusted ===")
            sm = r["summary"]
            check("counts by status", (sm["missing"], sm["synthetic"], sm["stale"], sm["ageing"], sm["current"]), (1, 1, 1, 1, 2))
            check("total value held", sm["value_held"], 11100.0)
            check("value on missing, placeholder or stale prices", sm["value_not_current"], 7100.0)
            check("as a share of the book", sm["share_not_current"], round(7100 / 11100, 4))

            print("\n=== The feed's own record ===")
            check("no connector: not live, says so", (await pf.feed_status(s, firm.id))["live"], False)
            conn = Connector(id=uuid.uuid4(), firm_id=firm.id, domain=ConnectorDomain.MARKET_RESEARCH_DATA,
                             provider_key="marketdata.yahoo", display_name="Yahoo Finance",
                             status=ConnectorStatus.CONNECTED, use_mock=True, config={})
            s.add(conn)
            await s.flush()
            f = await pf.feed_status(s, firm.id, now=now)
            check("a mock feed is switched off, with no next run", (f["live"], f["next_runs"]), (False, []))
            conn.use_mock = False
            conn.last_synced_at = now - timedelta(hours=3)
            s.add(ConnectorSync(firm_id=firm.id, connector_id=conn.id, status="partial", records_ingested=4,
                                detail={"inserted": 3, "updated": 1, "resolved": 6, "instruments": 7,
                                        "failed": {"STALE": "response was not JSON (rate-limited or blocked?)"}},
                                started_at=now - timedelta(hours=3), finished_at=now - timedelta(hours=3)))
            await s.flush()
            f = await pf.feed_status(s, firm.id, now=now)
            check("a live feed has its next run", f["next_runs"][0]["at"], "2026-10-27T22:00:00+00:00")
            check("the last run says what failed and why",
                  f["last_run"]["failed"], [{"symbol": "STALE", "reason": "response was not JSON (rate-limited or blocked?)"}])
            check("and what it fetched", (f["last_run"]["inserted"], f["last_run"]["resolved"], f["last_run"]["instruments"]), (3, 6, 7))

            print("\n=== Which clients rest on an untrustworthy price ===")
            sbm = await pf.statuses_by_mandate(s, firm.id, today=today)
            check("M1 holds a stale and a placeholder price",
                  sorted(x["symbol"] for x in sbm[m1.id] if x["status"] in ("stale", "synthetic")), ["PPEF1", "STALE"])
            check("M2 holds a missing one", [x["symbol"] for x in sbm[m2.id] if x["status"] == "missing"], ["NOPRICE"])

            print("\n=== Refreshing prices now ===")
            calls: list[int] = []

            async def fake_sync(session, firm_id, **kw):
                calls.append(1)
                return {"instruments": 7, "resolved": 6, "inserted": 5, "updated": 1,
                        "failed": {"NOPRICE": "no data"}, "stale": [], "newest_price_date": "2026-10-27",
                        "provider": "yahoo", "revalued": {"holdings": 12, "real_priced": 10}}

            real_sync = service.sync_market_data
            service.sync_market_data = fake_sync
            try:
                try:
                    await pf.refresh_prices(s, firm.id, now=now)
                    check("ran 3 hours after the last fetch", True, True)
                except pf.RefreshRefused:
                    check("ran 3 hours after the last fetch", "refused", "ran")
                check("it went through the provider once", len(calls), 1)

                res = await s.get(Connector, conn.id)
                check("and recorded as a sync, so the next refusal has something to measure", res.last_synced_at is not None, True)
                try:
                    await pf.refresh_prices(s, firm.id, now=res.last_synced_at.replace(tzinfo=UTC) + timedelta(minutes=4))
                    check("a second refresh four minutes later is refused", "ran", "refused")
                except pf.RefreshRefused as exc:
                    check("a second refresh four minutes later is refused", "minute" in str(exc), True)
                check("the provider is not asked again", len(calls), 1)
                try:
                    await pf.refresh_prices(s, firm.id, now=res.last_synced_at.replace(tzinfo=UTC) + timedelta(minutes=11))
                    check("after ten minutes it runs again", len(calls), 2)
                except pf.RefreshRefused:
                    check("after ten minutes it runs again", "refused", "ran")
                conn.use_mock = True
                try:
                    await pf.refresh_prices(s, firm.id, now=now + timedelta(days=1))
                    check("a switched-off feed is refused", "ran", "refused")
                except pf.RefreshRefused as exc:
                    check("a switched-off feed is refused, and says where to turn it on", "Connectors" in str(exc), True)
                conn.use_mock = False

                print("\n=== Who may, and that it waits for a confirmation ===")
                t = TOOLS["refresh_prices"]
                check("the tool pauses for confirmation", t.change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
                check("operations, portfolio team and administrators hold it", t.roles_required,
                      {UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM, UserRole.ADMIN})
                check("reading freshness changes nothing", TOOLS["read_price_freshness"].change_state, ToolChangeState.NO)
                check("and every staff role may read it", UserRole.ADVISER in TOOLS["read_price_freshness"].roles_required, True)
                check("a client holds neither", (UserRole.CLIENT in TOOLS["read_price_freshness"].roles_required,
                                                 UserRole.CLIENT in t.roles_required), (False, False))
                try:
                    await ToolExecutors(s, uuid.uuid4(), UserRole.ADVISER, firm.id).execute("refresh_prices", {})
                    check("an adviser is refused by the tool", "ran", "refused")
                except ExecutorError:
                    check("an adviser is refused by the tool", "refused", "refused")
                for role in (UserRole.ADVISER, UserRole.COMPLIANCE):
                    try:
                        await prices_api.refresh(user=SimpleNamespace(role=role), firm=firm, db=s)
                        check(f"{role.value} is refused by the API", "ran", "refused")
                    except HTTPException as exc:
                        check(f"{role.value} is refused by the API", exc.status_code, 403)
                reading = await ToolExecutors(s, uuid.uuid4(), UserRole.ADVISER, firm.id).execute("read_price_freshness", {})
                check("an adviser may read it", reading["summary"]["total"], 6)
                check("and gets a one-line answer", "not trustworthy" in reading["message"], True)
                conn.last_synced_at = None
                done = await ToolExecutors(s, uuid.uuid4(), UserRole.OPERATIONS, firm.id).execute("refresh_prices", {})
                check("operations runs it and is told what happened", "revalued" in done["message"], True)
                try:
                    await prices_api.refresh(user=SimpleNamespace(role=UserRole.OPERATIONS), firm=firm, db=s)
                    check("straight after, the API refuses with 409", "ran", "refused")
                except HTTPException as exc:
                    check("straight after, the API refuses with 409", exc.status_code, 409)

                print("\n=== The admin data-quality view reads the prices, not the connector clock ===")
                from app.api import admin as admin_api
                dq = await admin_api.get_data_quality(user=SimpleNamespace(role=UserRole.ADMIN), firm=firm, db=s)
                h = dq["households"][0]
                check("the household rests on a stale, a placeholder and a missing price",
                      h["untrusted_prices"], ["NOPRICE", "PPEF1", "STALE"])
                # The admin view dates prices against the real clock, not the test's fixed day.
                check("its oldest price is named", (h["price_age_days"], h["stalest_symbol"]),
                      ((date.today() - (today - timedelta(days=290))).days, "PPEF1"))
                check("an untrusted price makes it critical however recent the feed run", h["health"], "red")

                print("\n=== The schedules the page shows are the ones the worker runs ===")
                from app.models.enums import AgentKey
                from app.models.tenant import AgentConfig
                s.add(AgentConfig(firm_id=firm.id, agent_key=AgentKey.ABANDONMENT_RECOVERY, enabled=True,
                                  schedule_cron="45 2 * * *", schedule_enabled=True))
                await s.flush()
                admin_user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.ADMIN, email="a@t")
                rows = await admin_api.get_schedules(user=admin_user, firm=firm, db=s)
                check("prices are listed first, as a fixed schedule",
                      (rows[0]["agent_key"], rows[0]["fixed"], rows[0]["cron"]), ("market_data", True, "Mon-Fri 06:00 and 22:00 UTC"))
                check("an agent shows the cron it is actually scheduled by",
                      [(r["agent_key"], r["cron"]) for r in rows if r["agent_key"] == "abandonment_recovery"],
                      [("abandonment_recovery", "45 2 * * *")])
                for bad in ("every day", "61 2 * * *", "1 2 3"):
                    try:
                        await admin_api.update_schedule("abandonment_recovery", admin_api.ScheduleUpdate(cron=bad),
                                                        actor=admin_user, firm=firm, db=s)
                        check(f"'{bad}' is refused", "saved", "refused")
                    except HTTPException as exc:
                        check(f"'{bad}' is refused", exc.status_code, 422)
                saved = await admin_api.update_schedule("abandonment_recovery", admin_api.ScheduleUpdate(cron="30 3 * * 1"),
                                                        actor=admin_user, firm=firm, db=s)
                check("a valid cron is saved and says when the worker picks it up",
                      (saved["cron"], saved["takes_effect"]), ("30 3 * * 1", "within 15 minutes"))
                try:
                    await admin_api.update_schedule("market_data", admin_api.ScheduleUpdate(cron="0 * * * *"),
                                                    actor=admin_user, firm=firm, db=s)
                    check("the fixed price schedule cannot be edited", "saved", "refused")
                except HTTPException as exc:
                    check("the fixed price schedule cannot be edited", exc.status_code, 409)
                try:
                    await admin_api.update_schedule("not_an_agent", admin_api.ScheduleUpdate(cron="0 * * * *"),
                                                    actor=admin_user, firm=firm, db=s)
                    check("an unknown key is refused", "saved", "refused")
                except HTTPException as exc:
                    check("an unknown key is refused", exc.status_code, 404)
            finally:
                service.sync_market_data = real_sync
    finally:
        await db.dispose()  # otherwise the process hangs on the open pool at exit

    print("\n" + "=" * 64)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
