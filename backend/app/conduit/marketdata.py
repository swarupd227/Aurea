"""Real market-data fetch (Conduit market-data connector).

The default provider is Yahoo Finance's public chart endpoint (no key). It is unofficial and
carries no service guarantee, so it sits behind `fetch_feed` - the one function the rest of
the platform calls - and a licensed provider can replace it without touching a caller.
Stooq and Alpha Vantage remain as keyed/alternative sources for the latest price only.

What a price means here, because it is easy to get wrong:

  * Every price carries the date it was TRADED on, taken from the exchange's own timezone
    (`meta.gmtoffset`). Stamping a quote "today" is wrong on a weekend or holiday (it is
    Friday's close), and converting a timestamp in the server's timezone puts a NZ stock's
    close a day before a US stock's.
  * A failed symbol is recorded with the reason, never silently dropped: the caller decides
    what a partial feed means.
  * If the network is unavailable nothing is invented. The last known price stays, and the
    sync reports that it did not move.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("aurea.marketdata")

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Astra Conduit market-data connector)"}
MAX_CONCURRENCY = 4          # polite to an unofficial endpoint; ~15 instruments finish in seconds
_RETRIES = 1                 # one retry on a network error, 429 or 5xx
_BACKOFF_SECONDS = 0.5


@dataclass
class Quote:
    symbol: str
    close: float
    currency: str
    source: str
    as_of: date | None = None      # the trading date; None where the provider does not say


@dataclass
class FeedResult:
    """What a feed returned: a dated close series per symbol, and why any symbol failed."""
    series: dict[str, list[tuple[date, float]]] = field(default_factory=dict)
    currency: dict[str, str] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    source: str = "yahoo"


def parse_chart(payload: dict) -> tuple[list[tuple[date, float]], str | None]:
    """Dated closes from a Yahoo chart payload. Dates are the EXCHANGE's local trading date."""
    chart = payload.get("chart") or {}
    results = chart.get("result")
    if not results:
        err = chart.get("error") or {}
        raise ValueError(err.get("description") or "no result")
    res = results[0]
    meta = res.get("meta") or {}
    offset = int(meta.get("gmtoffset") or 0)
    stamps = res.get("timestamp") or []
    closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    by_date: dict[date, float] = {}
    for t, c in zip(stamps, closes):
        if c is None or float(c) <= 0:
            continue
        by_date[datetime.fromtimestamp(int(t) + offset, tz=timezone.utc).date()] = float(c)
    return sorted(by_date.items()), meta.get("currency")


async def _fetch_one(client: httpx.AsyncClient, sem: asyncio.Semaphore, symbol: str, rng: str,
                     interval: str, out: FeedResult) -> None:
    reason = "no data returned"
    for attempt in range(_RETRIES + 1):
        try:
            async with sem:
                resp = await client.get(YAHOO_CHART.format(symbol=symbol),
                                        params={"interval": interval, "range": rng})
            if resp.status_code == 404:
                out.failures[symbol] = "unknown symbol (HTTP 404)"
                return
            if resp.status_code == 429 or resp.status_code >= 500:
                reason = f"HTTP {resp.status_code}"
                if attempt < _RETRIES:
                    await asyncio.sleep(_BACKOFF_SECONDS * (attempt + 1))
                continue
            resp.raise_for_status()
            points, currency = parse_chart(resp.json())
            if not points:
                out.failures[symbol] = "no prices in the requested range"
                return
            out.series[symbol] = points
            if currency:
                out.currency[symbol] = currency
            return
        except json.JSONDecodeError:
            reason = "response was not JSON (rate-limited or blocked?)"
            if attempt < _RETRIES:
                await asyncio.sleep(_BACKOFF_SECONDS * (attempt + 1))
        except ValueError as exc:   # parse_chart: the provider's own stated reason
            out.failures[symbol] = str(exc)[:200]
            return
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
            reason = f"{type(exc).__name__}: {exc}"[:200]
            if attempt < _RETRIES:
                await asyncio.sleep(_BACKOFF_SECONDS * (attempt + 1))
    out.failures[symbol] = reason
    log.warning("yahoo_fetch_failed", symbol=symbol, reason=reason)


async def fetch_series(requests: dict[str, str], *, interval: str = "1d",
                       transport: httpx.AsyncBaseTransport | None = None,
                       concurrency: int = MAX_CONCURRENCY) -> FeedResult:
    """Dated closes for each symbol over its own range ({symbol: '5d' | '3mo' | ...})."""
    out = FeedResult()
    if not requests:
        return out
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=10.0, headers=_HEADERS, transport=transport) as client:
        await asyncio.gather(*(_fetch_one(client, sem, s, r, interval, out) for s, r in requests.items()))
    log.info("yahoo_series", requested=len(requests), resolved=len(out.series), failed=len(out.failures))
    return out


def to_stooq_symbol(symbol: str, market_symbol: str | None) -> str:
    """Map an instrument symbol to Stooq's convention (e.g. AAPL -> aapl.us, AIR.NZ -> air.nz)."""
    if market_symbol:
        return market_symbol.lower()
    s = symbol.lower()
    if "." in s:
        return s  # already exchange-qualified (e.g. air.nz)
    return f"{s}.us"


async def fetch_quotes_stooq(symbols: list[tuple[str, str | None, str]]) -> dict[str, Quote]:
    """symbols: list of (symbol, market_symbol, currency). Returns {symbol: Quote}."""
    out: dict[str, Quote] = {}
    if not symbols:
        return out
    async with httpx.AsyncClient(timeout=10.0) as client:
        for symbol, market_symbol, currency in symbols:
            stq = to_stooq_symbol(symbol, market_symbol)
            url = f"https://stooq.com/q/l/?s={stq}&f=sd2t2ohlcv&h&e=csv"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                rows = resp.text.strip().splitlines()
                if len(rows) < 2:
                    continue
                header = [h.strip().lower() for h in rows[0].split(",")]
                values = rows[1].split(",")
                rec = dict(zip(header, values))
                close_raw = rec.get("close", "")
                if close_raw in ("", "N/D"):
                    continue
                traded = None
                try:
                    traded = date.fromisoformat(rec.get("date", ""))
                except ValueError:
                    pass
                out[symbol] = Quote(symbol=symbol, close=float(close_raw), currency=currency,
                                    source="stooq", as_of=traded)
            except Exception as exc:  # network/parse — skip, keep prior price
                log.debug("stooq_fetch_failed", symbol=symbol, error=str(exc))
                continue
    log.info("stooq_quotes", requested=len(symbols), resolved=len(out))
    return out


async def fetch_quotes_yahoo(symbols: list[tuple[str, str | None, str]]) -> dict[str, Quote]:
    """The latest close for each symbol, with the date it was traded on."""
    feed = await fetch_series({s: "5d" for s, _m, _c in symbols})
    currencies = {s: c for s, _m, c in symbols}
    out: dict[str, Quote] = {}
    for symbol, points in feed.series.items():
        traded, close = points[-1]
        out[symbol] = Quote(symbol=symbol, close=close, currency=feed.currency.get(symbol, currencies[symbol]),
                            source="yahoo", as_of=traded)
    return out


async def fetch_quotes_alphavantage(
    symbols: list[tuple[str, str | None, str]]
) -> dict[str, Quote]:
    out: dict[str, Quote] = {}
    key = settings.alphavantage_api_key
    if not key:
        return out
    async with httpx.AsyncClient(timeout=10.0) as client:
        for symbol, market_symbol, currency in symbols:
            q = market_symbol or symbol
            url = (
                "https://www.alphavantage.co/query?function=GLOBAL_QUOTE"
                f"&symbol={q}&apikey={key}"
            )
            try:
                resp = await client.get(url)
                data = resp.json().get("Global Quote", {})
                price = data.get("05. price")
                if price:
                    traded = None
                    try:
                        traded = date.fromisoformat(data.get("07. latest trading day", ""))
                    except ValueError:
                        pass
                    out[symbol] = Quote(symbol, float(price), currency, "alphavantage", traded)
            except Exception as exc:
                log.debug("av_fetch_failed", symbol=symbol, error=str(exc))
    return out


async def fetch_history_yahoo(symbol: str, *, rng: str = "1y", interval: str = "1mo") -> list[tuple[str, float]]:
    """Dated close history for a symbol from Yahoo. Returns [(YYYY-MM-DD, close), ...]."""
    feed = await fetch_series({symbol: rng}, interval=interval)
    return [(d.isoformat(), c) for d, c in feed.series.get(symbol, [])]


async def fetch_quotes(symbols: list[tuple[str, str | None, str]]) -> dict[str, Quote]:
    provider = settings.marketdata_provider
    if provider == "alphavantage" and settings.alphavantage_api_key:
        quotes = await fetch_quotes_alphavantage(symbols)
        if quotes:
            return quotes
    if provider == "stooq":
        quotes = await fetch_quotes_stooq(symbols)
        if quotes:
            return quotes
    # Default real feed: Yahoo Finance (no key). Falls through to here for provider=yahoo
    # and whenever the configured provider returns nothing.
    return await fetch_quotes_yahoo(symbols)


async def fetch_feed(symbols: list[tuple[str, str | None, str]], ranges: dict[str, str], *,
                     transport: httpx.AsyncBaseTransport | None = None) -> FeedResult:
    """The one entry point the sync uses: dated closes per symbol, and why any failed.

    Yahoo is asked for each symbol over the range that covers ITS gap since the last stored
    price, so a long outage is backfilled in the same call that fetches today. The other
    providers return only the latest price, so they cannot backfill."""
    provider = settings.marketdata_provider
    if provider in ("stooq", "alphavantage"):
        fetch = fetch_quotes_stooq if provider == "stooq" else fetch_quotes_alphavantage
        quotes = await fetch(symbols)
        if quotes:
            feed = FeedResult(source=provider)
            for symbol, q in quotes.items():
                feed.series[symbol] = [(q.as_of or datetime.now(timezone.utc).date(), q.close)]
                feed.currency[symbol] = q.currency
            for symbol, _m, _c in symbols:
                if symbol not in quotes:
                    feed.failures[symbol] = f"{provider} returned no price"
            return feed
    return await fetch_series({s: ranges.get(s, "5d") for s, _m, _c in symbols}, transport=transport)
