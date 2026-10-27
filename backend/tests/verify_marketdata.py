"""Prove the price refresh (L100 §9): prices carry the date they TRADED, a feed gap is
backfilled, a failed symbol is reported not dropped, holdings are revalued after a sync, and
daily prices leave every monthly consumer's answer unchanged.

The HTTP layer is mocked (httpx.MockTransport) - nothing here touches the network. Runs against
a throwaway SQLite database so it needs no cloud connection.

    python -m tests.verify_marketdata
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.aurea_core import performance, pricing, valuation
from app.aurea_core.analytics import risk_data
from app.conduit import marketdata, service
from app.core.db import Base
from app.models.connectors import Connector, ConnectorSync
from app.models.enums import ConnectorDomain, ConnectorStatus, MarketType
from app.models.graph import Account
from app.models.portfolio import Holding, Instrument, Price
from app.models.tenant import Firm

PASS, FAIL = [], []
TODAY = date(2026, 10, 26)        # a Monday
marketdata._BACKOFF_SECONDS = 0   # retries are tested, not waited for


def check(label: str, got, want, tol: float = 0.0001) -> None:
    ok = (abs(float(got) - float(want)) < tol) if isinstance(want, (int, float)) and not isinstance(want, bool) \
        else got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def chart(points: list[tuple[date, float]], *, gmtoffset=0, hour=9, currency="USD") -> dict:
    """A Yahoo chart payload: one bar per date, stamped at `hour` local time, as the exchange does."""
    stamps = [int(datetime(d.year, d.month, d.day, hour, 0, tzinfo=timezone.utc).timestamp()) - gmtoffset
              for d, _ in points]
    return {"chart": {"result": [{"meta": {"gmtoffset": gmtoffset, "currency": currency},
                                  "timestamp": stamps,
                                  "indicators": {"quote": [{"close": [c for _, c in points]}]}}], "error": None}}


class Feed:
    """A scriptable Yahoo: per-symbol behaviour, a request log, and an in-flight counter."""

    def __init__(self):
        self.behaviour: dict[str, object] = {}
        self.calls: list[tuple[str, str]] = []
        self.in_flight = self.max_in_flight = 0
        self._flaky: dict[str, int] = {}

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        symbol = request.url.path.rsplit("/", 1)[-1]
        self.calls.append((symbol, request.url.params.get("range", "")))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            b = self.behaviour.get(symbol)
            if b == "404":
                return httpx.Response(404, json={"chart": {"result": None, "error": {"description": "Not Found"}}})
            if b == "500":
                return httpx.Response(500)
            if b == "garbage":
                return httpx.Response(200, text="<html>rate limited</html>")
            if b == "error":
                return httpx.Response(200, json={"chart": {"result": None, "error": {"description": "Delisted"}}})
            if b == "flaky":
                self._flaky[symbol] = self._flaky.get(symbol, 0) + 1
                if self._flaky[symbol] == 1:
                    return httpx.Response(503)
                b = self.behaviour[symbol + ":then"]
            return httpx.Response(200, json=b)
        finally:
            self.in_flight -= 1

    def transport(self):
        return httpx.MockTransport(self)


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)

        print("\n=== A bar is dated by the exchange's own day ===")
        nz, _ = marketdata.parse_chart(chart([(date(2026, 10, 26), 10.0)], gmtoffset=46800, hour=10))
        us, _ = marketdata.parse_chart(chart([(date(2026, 10, 26), 100.0)], gmtoffset=-14400, hour=9))
        check("a NZ open at 10:00 NZDT is still the 26th, not the 25th UTC", nz[0][0], date(2026, 10, 26))
        check("a US open at 09:30 EDT is the 26th", us[0][0], date(2026, 10, 26))
        check("a missing or zero close is skipped",
              len(marketdata.parse_chart(chart([(date(2026, 10, 26), 0.0)]))[0]), 0)
        try:
            marketdata.parse_chart({"chart": {"result": None, "error": {"description": "Delisted"}}})
            check("an error payload raises with its reason", "returned", "raised")
        except ValueError as exc:
            check("an error payload raises with its reason", str(exc), "Delisted")

        print("\n=== Helpers ===")
        check("month_end keeps the last close in each month",
              pricing.month_end_points({date(2026, 1, 1): 1, date(2026, 1, 30): 2, date(2026, 2, 2): 3}),
              {date(2026, 1, 30): 2, date(2026, 2, 2): 3})
        check("a 2-day gap needs 5 days of history", pricing.range_for_gap(2), "5d")
        check("a 111-day gap needs 6 months", pricing.range_for_gap(111), "6mo")
        check("an instrument never priced gets a year", pricing.range_for_gap(None), "1y")

        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD")
            s.add(firm)
            await s.flush()

            async def inst(symbol, *, market=MarketType.PUBLIC, currency="USD"):
                i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=symbol, name=symbol, asset_class="equity",
                               market_type=market, currency=currency)
                s.add(i)
                await s.flush()
                return i

            async def price(i, d, close, *, real=True):
                s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=d, close=close, currency="USD",
                            source="yahoo" if real else "synthetic", is_real=real))
                await s.flush()

            account = Account(id=uuid.uuid4(), firm_id=firm.id, name="A", currency="NZD", cash_balance=Decimal("0"))
            s.add(account)
            await s.flush()

            async def hold(i, qty, mv, lineage=None):
                h = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=account.id, instrument_id=i.id,
                            quantity=Decimal(str(qty)), market_value=Decimal(str(mv)), cost_basis=Decimal("0"),
                            lineage=lineage or {})
                s.add(h)
                await s.flush()
                return h

            # ── a weekend, a backfill, and revaluation ─────────────────────────────────────────
            aaa, bbb, private = await inst("AAA"), await inst("BBB.NZ", currency="NZD"), await inst(
                "PRIV", market=MarketType.PRIVATE)
            await price(aaa, date(2026, 7, 7), 100.0)
            await price(aaa, date(2026, 6, 1), 90.0)                     # settled history, must not move
            await price(bbb, date(2026, 7, 7), 5.0, real=False)          # a synthetic row a real bar replaces
            await price(private, date(2026, 1, 1), 100.0, real=False)
            h_aaa = await hold(aaa, 10, 1000, {"source": "onboarding"})
            h_priv = await hold(private, 3, 300)

            feed = Feed()
            sunday = date(2026, 10, 25)
            days = [date(2026, 7, 7) + timedelta(days=k) for k in range(1, 112) if (date(2026, 7, 7) + timedelta(days=k)).weekday() < 5
                    and date(2026, 7, 7) + timedelta(days=k) <= date(2026, 10, 23)]
            feed.behaviour["AAA"] = chart([(date(2026, 7, 7), 105.0)] + [(d, 110.0 + i * 0.1) for i, d in enumerate(days)])
            feed.behaviour["BBB.NZ"] = chart([(date(2026, 7, 7), 5.5)] + [(d, 6.0) for d in days], gmtoffset=46800,
                                              hour=10, currency="NZD")
            report = await service.sync_market_data(s, firm.id, today=sunday, transport=feed.transport())

            check("both public instruments resolved", report["resolved"], 2)
            check("a private instrument is not in the feed", ("PRIV", "6mo") in feed.calls, False)
            check("the gap since 7 July is asked for as 6 months", ("AAA", "6mo") in feed.calls, True)
            check("the newest price is Friday's, not Sunday's", report["newest_price_date"], "2026-10-23")
            n_aaa = (await s.execute(select(func.count()).select_from(Price).where(Price.instrument_id == aaa.id))).scalar_one()
            check("one row per traded day, inserted", report["inserted"] >= len(days), True)
            check("nothing is dated on the weekend it ran", (await s.execute(
                select(func.count()).select_from(Price).where(Price.as_of == sunday))).scalar_one(), 0)
            june = (await s.execute(select(Price).where(Price.instrument_id == aaa.id, Price.as_of == date(2026, 6, 1)))).scalar_one()
            check("settled history was not rewritten", float(june.close), 90.0)
            last = (await s.execute(select(Price).where(Price.instrument_id == aaa.id, Price.as_of == date(2026, 7, 7)))).scalar_one()
            check("the latest stored day IS refreshed (it may have been a part-session)", float(last.close), 105.0)
            old_syn = (await s.execute(select(Price).where(Price.instrument_id == bbb.id, Price.as_of == date(2026, 7, 7)))).scalar_one()
            check("a synthetic row on a returned date is replaced by the real close", (float(old_syn.close), old_syn.is_real), (5.5, True))

            await s.refresh(h_aaa)
            await s.refresh(h_priv)
            want = 10 * (110.0 + (len(days) - 1) * 0.1)
            check("the holding is revalued to quantity x the newest close", float(h_aaa.market_value), want, tol=0.01)
            check("the revaluation says which price and when",
                  (h_aaa.lineage["price_as_of"], h_aaa.lineage["is_real_price"]), ("2026-10-23", True))
            check("a holding's own provenance survives the revaluation", h_aaa.lineage["source"], "onboarding")
            check("a private holding is untouched", float(h_priv.market_value), 300.0)
            check("the report says how many holdings were revalued", report["revalued"]["holdings"] >= 2, True)

            again = await service.sync_market_data(s, firm.id, today=sunday, transport=feed.transport())
            check("running it again inserts nothing", again["inserted"], 0)
            check("and changes nothing, so does not revalue", (again["updated"], again["revalued"]), (0, None))

            # ── latest_prices is one row per instrument, chosen in the database ────────────────
            lp = await valuation.latest_prices(s, firm.id)
            check("latest price per instrument is the newest date", lp[aaa.id].as_of, date(2026, 10, 23))
            check("and there is exactly one per instrument", len(lp), 3)

            # ── failures are reported, retried, bounded ────────────────────────────────────────
            feed2 = Feed()
            symbols = ["OK1", "GONE", "ALWAYS500", "FLAKY", "JUNK", "DELISTED"] + [f"S{i}" for i in range(8)]
            insts = {sym: await inst(sym) for sym in symbols}
            ok = chart([(date(2026, 10, 22), 10.0), (date(2026, 10, 23), 11.0)])
            for sym in symbols:
                feed2.behaviour[sym] = ok
            feed2.behaviour.update({"GONE": "404", "ALWAYS500": "500", "JUNK": "garbage", "DELISTED": "error",
                                    "FLAKY": "flaky", "FLAKY:then": ok})
            # AAA and BBB.NZ already current; give them nothing new
            feed2.behaviour["AAA"] = chart([(date(2026, 10, 23), 110.0)])
            feed2.behaviour["BBB.NZ"] = chart([(date(2026, 10, 23), 6.0)], gmtoffset=46800, hour=10)
            r2 = await service.sync_market_data(s, firm.id, today=sunday, transport=feed2.transport())
            check("an unknown symbol says so", r2["failed"]["GONE"], "unknown symbol (HTTP 404)")
            check("a symbol that keeps failing reports its status after a retry", r2["failed"]["ALWAYS500"], "HTTP 500")
            check("a flaky symbol is retried and succeeds", "FLAKY" in r2["failed"], False)
            check("the retry was actually made", sum(1 for sym, _ in feed2.calls if sym == "FLAKY"), 2)
            check("a rate-limit page is a failure, not data, in plain words",
                  r2["failed"]["JUNK"], "response was not JSON (rate-limited or blocked?)")
            check("a provider's own reason is kept", r2["failed"]["DELISTED"], "Delisted")
            check("the other symbols still resolved", r2["resolved"] >= 10, True)
            check("failures never exceeded the concurrency bound", feed2.max_in_flight <= marketdata.MAX_CONCURRENCY, True)
            check("and it did run several at once", feed2.max_in_flight > 1, True)
            stale = {x["symbol"] for x in r2["stale"]}
            check("a symbol that could not be fetched and has no price is listed as stale", {"GONE", "ALWAYS500"} <= stale, True)
            check("a fresh one is not", "OK1" in stale, False)

            # ── never a future date ────────────────────────────────────────────────────────────
            feed3 = Feed()
            feed3.behaviour = {"AAA": chart([(date(2026, 10, 23), 110.0), (date(2026, 10, 30), 999.0)])}
            for sym in ["BBB.NZ"] + symbols:
                feed3.behaviour[sym] = chart([(date(2026, 10, 23), 1.0)])
            await service.sync_market_data(s, firm.id, today=sunday, transport=feed3.transport())
            check("a bar dated after today is dropped",
                  (await s.execute(select(func.count()).select_from(Price).where(Price.as_of == date(2026, 10, 30)))).scalar_one(), 0)

            # ── the connector records an honest status ─────────────────────────────────────────
            connector = Connector(firm_id=firm.id, domain=ConnectorDomain.MARKET_RESEARCH_DATA,
                                  provider_key="marketdata.yahoo", display_name="Yahoo", status=ConnectorStatus.CONNECTED,
                                  use_mock=False, sync_cron="0 6 * * *", config={})
            s.add(connector)
            await s.flush()
            real_fetch = service.fetch_feed

            def with_transport(feedobj):
                async def f(symbols_, ranges_, transport=None):
                    return await marketdata.fetch_feed(symbols_, ranges_, transport=feedobj.transport())
                return f

            dead = Feed()
            for sym in ["AAA", "BBB.NZ"] + symbols:
                dead.behaviour[sym] = "500"
            service.fetch_feed = with_transport(dead)
            before = connector.last_synced_at
            sync = await service.run_sync(s, connector)
            check("a feed that returned nothing is an error, not a success", sync.status, "error")
            check("the connector shows the error", connector.status, ConnectorStatus.ERROR)
            check("and does not claim to have synced", connector.last_synced_at, before)
            check("the reason is on the connector", "No prices could be fetched" in (connector.last_error or ""), True)

            part = Feed()
            for sym in ["AAA", "BBB.NZ"] + symbols:
                part.behaviour[sym] = chart([(date(2026, 10, 23), 2.0)])
            part.behaviour["GONE"] = "404"
            service.fetch_feed = with_transport(part)
            sync = await service.run_sync(s, connector)
            check("a feed with some failures is partial", sync.status, "partial")
            check("and the failure is on the record", "GONE" in sync.detail["failed"], True)
            check("the connector is healthy again", connector.status, ConnectorStatus.CONNECTED)
            check("and counts as synced", connector.last_synced_at is not None, True)
            service.fetch_feed = real_fetch

        # ── daily rows must not change what monthly consumers say ──────────────────────────────
        print("\n=== Daily prices leave the performance engine's answer unchanged ===")

        async def twr(dense: bool) -> dict:
            async with Session() as s:
                f = Firm(id=uuid.uuid4(), name="P", slug=f"p{dense}", base_currency="NZD")
                s.add(f)
                await s.flush()
                a = Account(id=uuid.uuid4(), firm_id=f.id, name="A", currency="NZD", cash_balance=Decimal("0"))
                s.add(a)
                await s.flush()
                months = [(date(2026, 1, 30), 100.0), (date(2026, 2, 27), 110.0), (date(2026, 3, 31), 99.0)]
                for sym, scale in (("X1", 1.0), ("X2", 2.0)):
                    i = Instrument(id=uuid.uuid4(), firm_id=f.id, symbol=sym, name=sym, asset_class="equity")
                    s.add(i)
                    await s.flush()
                    for d, c in months:
                        s.add(Price(firm_id=f.id, instrument_id=i.id, as_of=d, close=c * scale, currency="USD", source="t", is_real=True))
                    if dense:   # a wandering close every weekday between the month-ends
                        d = date(2026, 1, 31)
                        while d < date(2026, 3, 31):
                            if d.weekday() < 5 and d not in {m[0] for m in months} and d.day != 28:
                                s.add(Price(firm_id=f.id, instrument_id=i.id, as_of=d, close=(60 + d.day) * scale,
                                            currency="USD", source="t", is_real=True))
                            d += timedelta(days=1)
                    s.add(Holding(id=uuid.uuid4(), firm_id=f.id, account_id=a.id, instrument_id=i.id,
                                  quantity=Decimal("10"), market_value=Decimal(str(990 * scale)), cost_basis=Decimal("0")))
                await s.flush()
                return await performance.account_performance(s, a.id, firm_id=f.id)

        monthly, daily = await twr(False), await twr(True)
        check("monthly history: 2 periods", monthly["months"], 2)
        check("the same history with a price every weekday still has 2 periods, not ~40", daily["months"], 2)
        check("and the same return", daily["twr"], monthly["twr"])
        check("(+10% then -10% = -1%)", monthly["twr"], -0.01)

        print("\n=== Data quality judges the latest price per instrument, not every row ===")
        async with Session() as s:
            f = Firm(id=uuid.uuid4(), name="D", slug="dq", base_currency="NZD")
            s.add(f)
            await s.flush()
            fresh_i = Instrument(id=uuid.uuid4(), firm_id=f.id, symbol="F", name="F", asset_class="equity")
            old_i = Instrument(id=uuid.uuid4(), firm_id=f.id, symbol="O", name="O", asset_class="equity")
            s.add_all([fresh_i, old_i])
            await s.flush()
            today = date.today()
            for k in range(200):        # 200 days of history on one instrument: used to drown the score
                s.add(Price(firm_id=f.id, instrument_id=fresh_i.id, as_of=today - timedelta(days=k + 1), close=10,
                            currency="USD", source="t", is_real=True))
            s.add(Price(firm_id=f.id, instrument_id=old_i.id, as_of=today - timedelta(days=40), close=10,
                        currency="USD", source="t", is_real=False))
            await s.flush()
            dq = (await risk_data.compute(s, f.id, []))["data_quality"]
            check("1 of 2 instruments has a current price", dq["timeliness"], 0.5)
            check("1 of 2 latest prices is real", dq["accuracy"], 0.5)
    finally:
        await db.dispose()  # otherwise the process hangs on the open pool at exit

    print("\n" + "=" * 64)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
