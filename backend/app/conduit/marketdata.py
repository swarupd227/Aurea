"""Real market-data fetch (Conduit market-data connector).

Default provider is Stooq, which serves end-of-day prices over plain HTTP/CSV with NO API
key — ideal for a self-contained demo with genuinely real data. Alpha Vantage is supported
as a keyed alternative. If the network is unavailable the caller keeps the last known
(possibly synthetic) price, so the platform still functions fully offline."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("aurea.marketdata")


@dataclass
class Quote:
    symbol: str
    close: float
    currency: str
    source: str


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
                out[symbol] = Quote(
                    symbol=symbol, close=float(close_raw), currency=currency, source="stooq"
                )
            except Exception as exc:  # network/parse — skip, keep prior price
                log.debug("stooq_fetch_failed", symbol=symbol, error=str(exc))
                continue
    log.info("stooq_quotes", requested=len(symbols), resolved=len(out))
    return out


async def fetch_quotes_yahoo(symbols: list[tuple[str, str | None, str]]) -> dict[str, Quote]:
    """Real quotes from Yahoo Finance's public chart API (no key). Instrument symbols
    (AAPL, AIR.NZ, SPK.NZ, AGG…) are already Yahoo-compatible."""
    out: dict[str, Quote] = {}
    if not symbols:
        return out
    headers = {"User-Agent": "Mozilla/5.0 (Aurea Conduit market-data connector)"}
    async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
        for symbol, _market_symbol, currency in symbols:
            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                "?interval=1d&range=1d"
            )
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                meta = resp.json()["chart"]["result"][0]["meta"]
                price = meta.get("regularMarketPrice")
                if price is not None:
                    out[symbol] = Quote(
                        symbol=symbol, close=float(price),
                        currency=meta.get("currency", currency), source="yahoo",
                    )
            except Exception as exc:
                log.debug("yahoo_fetch_failed", symbol=symbol, error=str(exc))
                continue
    log.info("yahoo_quotes", requested=len(symbols), resolved=len(out))
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
                    out[symbol] = Quote(symbol, float(price), currency, "alphavantage")
            except Exception as exc:
                log.debug("av_fetch_failed", symbol=symbol, error=str(exc))
    return out


async def fetch_history_yahoo(symbol: str, *, rng: str = "1y", interval: str = "1mo") -> list[tuple[str, float]]:
    """Real monthly close history for a symbol from Yahoo. Returns [(YYYY-MM-DD, close), ...]."""
    from datetime import date as _date

    headers = {"User-Agent": "Mozilla/5.0 (Aurera Conduit market-data connector)"}
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?interval={interval}&range={rng}")
    out: list[tuple[str, float]] = []
    try:
        async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            res = resp.json()["chart"]["result"][0]
            ts = res.get("timestamp") or []
            closes = res["indicators"]["quote"][0].get("close") or []
            for t, c in zip(ts, closes):
                if c is None:
                    continue
                out.append((_date.fromtimestamp(t).isoformat(), float(c)))
    except Exception as exc:
        log.debug("yahoo_history_failed", symbol=symbol, error=str(exc))
    return out


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
