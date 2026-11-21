"""Where held-away holdings come from when no person types them: the feed registry (B-198).

An aggregation vendor (or a custodian's statement export) is a feed. It registers here once, at start-up, with
`register(feed)`, and `pull` takes what it delivers for one firm into `held_away_holding`, keyed by the feed's own id
for each line (`source_system` = the feed's key, `external_id` = its line id), so a second pull of the same statement
adds nothing. A line that has changed ends the one it supersedes, which stays on file; a line the feed no longer
delivers is ended, not deleted. A line that would be refused if a person recorded it (another firm's household, a
value that is not a number, a date to come) is reported and not written.

A feed says whether it is simulated, and every line it delivers carries that, so a card shows it. Which firms a feed
serves is the feed's own business (`enabled_for`): this module never reads a simulation's opt-in.

A pull writes no ledger entry: each row carries its feed, its id there and when it arrived, which is its record. A
holding a person records goes on the ledger (`aurea_core/held_away.py`).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time
from app.models.graph import Household, LegalEntity, Person
from app.models.held_away import HeldAwayHolding
from app.models.tenant import Firm


@dataclass
class FeedLine:
    """One holding, as the feed delivers it."""
    external_id: str
    household_id: uuid.UUID
    institution: str
    instrument_name: str
    value: float
    currency: str
    value_on: date
    document: str                       # the statement or feed it came from, as a person would name it
    person_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None
    account_label: str | None = None
    symbol: str | None = None
    isin: str | None = None
    asset_class: str | None = None
    quantity: float | None = None


class HeldAwayFeed(Protocol):
    key: str          # becomes every line's source_system
    name: str
    simulated: bool

    def enabled_for(self, firm: Firm) -> bool: ...

    async def lines(self, session: AsyncSession, firm: Firm) -> list[FeedLine]: ...


FEEDS: dict[str, HeldAwayFeed] = {}


def register(feed: HeldAwayFeed) -> None:
    FEEDS[feed.key] = feed


def unregister(key: str) -> None:
    FEEDS.pop(key, None)


def for_firm(firm: Firm) -> list[HeldAwayFeed]:
    return [f for f in FEEDS.values() if f.enabled_for(firm)]


def describe(source_system: str | None) -> dict:
    """What a line's source says about it: who delivered it, and whether that is simulated."""
    if not source_system:
        return {"source": "statement", "feed": None, "simulated": False}
    feed = FEEDS.get(source_system)
    return {"source": "feed", "feed": feed.name if feed else source_system,
            # A feed no longer installed still names itself by its key; a simulated one's key says so.
            "simulated": bool(feed.simulated) if feed else str(source_system).startswith("simulated:")}


_FIELDS = ("household_id", "person_id", "entity_id", "institution", "account_label", "symbol", "isin", "instrument_name",
           "asset_class", "quantity", "value", "currency", "value_on")


def _same(row: HeldAwayHolding, values: dict) -> bool:
    def norm(v):
        return round(float(v), 6) if isinstance(v, (int, float)) or (v is not None and type(v).__name__ == "Decimal") else v
    return all(norm(getattr(row, k)) == norm(values[k]) for k in _FIELDS)


async def _check(session: AsyncSession, firm: Firm, line: FeedLine, today: date, cache: dict) -> dict:
    """The line as a row would hold it, or a ValueError with why it is refused."""
    hh = cache.get(line.household_id)
    if hh is None:
        hh = await session.get(Household, line.household_id)
        cache[line.household_id] = hh
    if hh is None or hh.firm_id != firm.id:
        raise ValueError("the household is not this firm's")
    if line.person_id is not None:
        p = await session.get(Person, line.person_id)
        if p is None or p.household_id != hh.id:
            raise ValueError("the person is not in the household")
    if line.entity_id is not None:
        e = await session.get(LegalEntity, line.entity_id)
        if e is None or e.household_id != hh.id:
            raise ValueError("the entity is not in the household")
    try:
        value = float(line.value)
    except (TypeError, ValueError):
        raise ValueError("the value is not a number")
    if value < 0:
        raise ValueError("the value is negative")
    if line.value_on is None or line.value_on > today:
        raise ValueError("the statement date is missing or to come")
    code = (line.currency or "").strip().upper()
    if len(code) != 3:
        raise ValueError("the currency is not a three-letter code")
    if not (line.instrument_name or "").strip() or not (line.institution or "").strip():
        raise ValueError("the line names no instrument or institution")
    return {"household_id": hh.id, "person_id": line.person_id, "entity_id": line.entity_id,
            "institution": line.institution.strip()[:120], "account_label": (line.account_label or "").strip()[:120] or None,
            "symbol": (line.symbol or "").strip().upper()[:32] or None, "isin": (line.isin or "").strip().upper()[:12] or None,
            "instrument_name": line.instrument_name.strip()[:200], "asset_class": (line.asset_class or "").strip().lower()[:24] or None,
            "quantity": float(line.quantity) if line.quantity is not None else None, "value": round(value, 2), "currency": code,
            "value_on": line.value_on, "source_document": (line.document or "").strip()[:255] or "Aggregation feed"}


async def pull(session: AsyncSession, firm: Firm, *, key: str | None = None) -> dict:
    """Take what each feed serving this firm delivers. Safe to repeat: an unchanged line adds nothing."""
    today = firm_time.today(firm)
    out = {"feeds": [], "added": 0, "superseded": 0, "ended": 0, "unchanged": 0, "refused": []}
    for feed in for_firm(firm):
        if key and feed.key != key:
            continue
        out["feeds"].append(feed.key)
        delivered = await feed.lines(session, firm)
        current = {r.external_id: r for r in (await session.execute(select(HeldAwayHolding).where(
            HeldAwayHolding.firm_id == firm.id, HeldAwayHolding.source_system == feed.key,
            HeldAwayHolding.status == "current"))).scalars().all()}
        seen: set[str] = set()
        cache: dict = {}
        by = f"feed:{feed.key}"[:120]
        for line in delivered:
            try:
                values = await _check(session, firm, line, today, cache)
            except ValueError as exc:
                out["refused"].append({"external_id": line.external_id, "reason": str(exc)})
                continue
            seen.add(line.external_id)
            row = current.get(line.external_id)
            if row is not None and _same(row, values):
                out["unchanged"] += 1
                continue
            if row is not None:
                row.status, row.ended_on, row.ended_by = "ended", values["value_on"], by
                row.ended_reason = f"Superseded by the feed's statement of {values['value_on'].isoformat()}"
                out["superseded"] += 1
            session.add(HeldAwayHolding(firm_id=firm.id, **values, source_system=feed.key, external_id=line.external_id,
                                        recorded_by=by, status="current", attributes={"simulated": bool(feed.simulated)}))
            out["added"] += 1
        for ext, row in current.items():
            if ext not in seen:
                row.status, row.ended_on, row.ended_by, row.ended_reason = "ended", today, by, "No longer delivered by the feed"
                out["ended"] += 1
        await session.flush()
    return out
