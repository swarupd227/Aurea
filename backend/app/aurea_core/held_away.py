"""How money held elsewhere is doing (B-198).

What a household holds at other institutions is listed line by line (`models/held_away.HeldAwayHolding`): recorded
by a person from a statement, or delivered by a feed (`aurea_core/held_away_feeds.py`). Each line is valued at what
its statement states, on its date, and nothing here estimates one.

Where a line names an instrument this firm prices (its symbol matches one of the firm's instruments), its return over
the last twelve months is read from real closing prices, and set beside:

  - a reference for its asset class, measured over the same dates: SPY (the S&P 500) for equity, AGG (US aggregate
    bonds) for fixed income and VNQ (US real estate) for property - the same proxies the performance engine uses;
  - the firm's shelf products in the same asset class, measured the same way, leaving out anything the shelf bars for
    this household, anything under watch or closed, anything its mandates exclude, and anything not reviewed while
    "approved products only" is on.

The rule, stated once: a line **underperforms** when its price return over the twelve months trails its class
reference by five percentage points or more. It is then raised to the RM with its evidence, and beside it the
best-returning shelf product in the same class that beat it over the same dates, if any did: an approved one before
one not reviewed. A line **overlaps** when the same instrument is also held in the household's managed book.

Every figure is an estimate and says so: price return only (no dividends, distributions or fees), each instrument in
its own currency with no currency effect modelled, and nothing on a synthetic price. A line with no instrument here,
or with too little real history, is listed at its stated value with no performance. This is advice and goes to the
adviser: no client reads it (the client's net worth shows what is on file and nothing of this).
"""
from __future__ import annotations

import uuid
from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, held_away_feeds
from app.models.enums import AssetClass, MarketType
from app.models.graph import Household, LegalEntity, Mandate, Person
from app.models.held_away import HeldAwayHolding
from app.models.portfolio import Holding, Instrument, Price
from app.models.tenant import Firm
from app.provenance import ledger

PERIOD_DAYS = 365
UNDERPERFORM_GAP = 0.05
START_TOLERANCE_DAYS = 35   # older history here is stored as month-end bars
END_TOLERANCE_DAYS = 7
STALE_DAYS = 10
REFERENCES = {
    AssetClass.EQUITY.value: ("SPY", "S&P 500 (SPY)"),
    AssetClass.FIXED_INCOME.value: ("AGG", "US aggregate bonds (AGG)"),
    AssetClass.PROPERTY.value: ("VNQ", "US real estate (VNQ)"),
}
RULE = (f"A holding underperforms when its price return over the last {PERIOD_DAYS} days trails its asset class's "
        f"reference by {UNDERPERFORM_GAP:.0%} points or more, on real closing prices.")
NOT_MODELLED = ["dividends, distributions and fees", "currency movements: each instrument is measured in its own currency",
                "anything before the statement's date: the holding is measured as if held for the whole period"]
ASSET_CLASSES = tuple(a.value for a in AssetClass)
SHOWN_ENDED = 10


class HeldAwayError(ValueError):
    """A holding that cannot be recorded or ended, with the reason."""


# ── Prices ──────────────────────────────────────────────────────────────────────────────


def _point(series: list[tuple[date, float]], day: date, tolerance: int, *, after_ok: bool = False) -> tuple[date, float] | None:
    """The close on or before `day`, within `tolerance` days; or, with `after_ok`, the first after it within that."""
    dates = [d for d, _ in series]
    i = bisect_right(dates, day)
    if i and (day - dates[i - 1]).days <= tolerance:
        return series[i - 1]
    if after_ok and i < len(series) and (dates[i] - day).days <= tolerance:
        return series[i]
    return None


def measure(series: list[tuple[date, float]], today: date) -> dict | None:
    """The return over the last PERIOD_DAYS on a series, ending at its latest close on or before today."""
    usable = [(d, c) for d, c in series if d <= today and c > 0]
    if len(usable) < 2:
        return None
    end = usable[-1]
    start = _point(usable, end[0] - timedelta(days=PERIOD_DAYS), START_TOLERANCE_DAYS, after_ok=True)
    if start is None or start[0] >= end[0]:
        return None
    return {"start": start[0].isoformat(), "end": end[0].isoformat(), "start_price": round(start[1], 6),
            "end_price": round(end[1], 6), "return": round(end[1] / start[1] - 1, 4), "stale_days": (today - end[0]).days}


def over(series: list[tuple[date, float]], start: str, end: str) -> float | None:
    """Another instrument's return over exactly the dates a line was measured on."""
    s = _point(series, date.fromisoformat(start), START_TOLERANCE_DAYS, after_ok=True)
    e = _point(series, date.fromisoformat(end), END_TOLERANCE_DAYS)
    if s is None or e is None or s[1] <= 0 or e[0] <= s[0]:
        return None
    return round(e[1] / s[1] - 1, 4)


async def _series(session: AsyncSession, ids: set) -> dict[uuid.UUID, list[tuple[date, float]]]:
    """Real closes only: a synthetic or declared price measures nothing here."""
    if not ids:
        return {}
    out: dict = defaultdict(list)
    for p in (await session.execute(select(Price).where(Price.instrument_id.in_(list(ids)), Price.is_real.is_(True))
                                    .order_by(Price.as_of))).scalars().all():
        if p.source != "declared_valuation":
            out[p.instrument_id].append((p.as_of, float(p.close)))
    return out


# ── Reading ─────────────────────────────────────────────────────────────────────────────


async def current_lines(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> list[HeldAwayHolding]:
    return (await session.execute(select(HeldAwayHolding).where(
        HeldAwayHolding.firm_id == firm_id, HeldAwayHolding.household_id == household_id, HeldAwayHolding.status == "current",
    ).order_by(HeldAwayHolding.institution, HeldAwayHolding.instrument_name))).scalars().all()


def _match(symbol: str | None, instruments: list[Instrument]) -> Instrument | None:
    """The firm's instrument the line names, by its symbol (or the symbol used at the data provider). Never by name."""
    if not symbol:
        return None
    want = symbol.strip().upper()
    for i in instruments:
        if i.symbol.upper() == want or (i.market_symbol or "").upper() == want:
            return i
    return None


async def _members(session: AsyncSession, household_id: uuid.UUID) -> tuple[list[Person], list[LegalEntity], list[Mandate]]:
    persons = (await session.execute(select(Person).where(Person.household_id == household_id))).scalars().all()
    entities = (await session.execute(select(LegalEntity).where(LegalEntity.household_id == household_id))).scalars().all()
    ids = [p.id for p in persons], [e.id for e in entities]
    conds = ([Mandate.person_id.in_(ids[0])] if ids[0] else []) + ([Mandate.entity_id.in_(ids[1])] if ids[1] else [])
    from sqlalchemy import or_

    mandates = (await session.execute(select(Mandate).where(or_(*conds)))).scalars().all() if conds else []
    return persons, entities, mandates


async def _managed(session: AsyncSession, mandates: list[Mandate], firm_id: uuid.UUID) -> dict[uuid.UUID, float]:
    """What the managed book holds of each instrument, for this household."""
    from app.models.graph import Account

    if not mandates:
        return {}
    rows = (await session.execute(select(Holding.instrument_id, Holding.market_value).join(Account, Account.id == Holding.account_id)
                                  .where(Holding.firm_id == firm_id, Account.mandate_id.in_([m.id for m in mandates]),
                                         Holding.quantity > 0))).all()
    out: dict = defaultdict(float)
    for iid, mv in rows:
        out[iid] += float(mv or 0)
    return out


async def _buyable(session: AsyncSession, firm: Firm, household: Household, mandates: list[Mandate],
                   instruments: list[Instrument]) -> dict[uuid.UUID, str]:
    """The firm's products this household could be offered, with their shelf standing: approved, or not reviewed while
    the firm allows it. Never one the shelf bars for any of its mandates, one under watch or closed, or one excluded."""
    from app.aurea_core import shelf

    decided = await shelf.decisions(session, firm.id)
    barred: set = set()
    for m in mandates:
        barred |= set(await shelf.barred(session, firm, m))
    excluded_symbols = {str(s).upper() for m in mandates for s in ((m.suitability or {}).get("values_exclusions") or [])}
    excluded_flags = {str(f).lower() for f in ((household.values or {}).get("exclusions") or [])}
    out = {}
    for i in instruments:
        if i.market_type != MarketType.PUBLIC or i.id in barred or i.symbol.upper() in excluded_symbols:
            continue
        if excluded_flags & {str(f).lower() for f in ((i.values_tags or {}).get("flags") or [])}:
            continue
        row = decided.get(i.id)
        if row is not None:
            if row.status != "approved":
                continue      # under watch or closed: not something to put forward
            if row.minimum_class and not mandates:
                continue      # gated, and no mandate here to read a certification against
            out[i.id] = "approved"
        elif not shelf.enforced(firm):
            out[i.id] = "not_reviewed"
    return out


def _owner(line: HeldAwayHolding, names: dict) -> str:
    if line.entity_id:
        return names.get(line.entity_id) or "entity"
    if line.person_id:
        return names.get(line.person_id) or "person"
    return "joint"


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID,
                        today: date | None = None) -> dict | None:
    """One household's holdings held elsewhere, each measured where it can be, or None if not this firm's."""
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    firm = await session.get(Firm, firm_id)
    today = today or firm_time.today(firm)
    base = (firm.base_currency or "INR").upper()
    persons, entities, mandates = await _members(session, household_id)
    names = {p.id: p.full_name for p in persons} | {e.id: e.name for e in entities}
    lines = await current_lines(session, household_id, firm_id=firm_id)
    instruments = (await session.execute(select(Instrument).where(Instrument.firm_id == firm_id))).scalars().all()
    by_symbol = {i.symbol.upper(): i for i in instruments}
    matched = {l.id: _match(l.symbol, instruments) for l in lines}
    classes = {(l.asset_class or (str(matched[l.id].asset_class) if matched[l.id] else None)) for l in lines} - {None}
    candidates = [i for i in instruments if str(i.asset_class) in classes]
    buyable = await _buyable(session, firm, household, mandates, candidates) if lines else {}
    refs = {cls: by_symbol.get(sym) for cls, (sym, _) in REFERENCES.items() if cls in classes}
    series = await _series(session, {m.id for m in matched.values() if m} | {r.id for r in refs.values() if r} | set(buyable))
    managed = await _managed(session, mandates, firm_id) if lines else {}

    rows, findings = [], []
    for l in lines:
        inst = matched[l.id]
        cls = l.asset_class or (str(inst.asset_class) if inst else None)
        source = held_away_feeds.describe(l.source_system)
        row = {
            "id": str(l.id), "institution": l.institution, "account_label": l.account_label, "owner": _owner(l, names),
            "person_id": str(l.person_id) if l.person_id else None, "entity_id": str(l.entity_id) if l.entity_id else None,
            "symbol": l.symbol, "isin": l.isin, "name": l.instrument_name, "asset_class": cls,
            "quantity": float(l.quantity) if l.quantity is not None else None, "value": float(l.value), "currency": l.currency,
            "value_on": l.value_on.isoformat(), "source_document": l.source_document, "recorded_by": l.recorded_by,
            "source": source["source"], "feed": source["feed"], "simulated": source["simulated"] or bool((l.attributes or {}).get("simulated")),
            "counted": (l.currency or base).upper() == base, "instrument": inst.symbol if inst else None,
            "performance": None, "not_measured": None, "reference": None, "gap": None, "underperforming": False,
            "alternative": None, "alternatives_considered": 0, "overlap": None,
        }
        if inst is None:
            row["not_measured"] = "no instrument here with this symbol" if l.symbol else "no symbol on the statement"
        else:
            perf = measure(series.get(inst.id, []), today)
            if perf is None:
                row["not_measured"] = f"no real price history over {PERIOD_DAYS} days"
            else:
                row["performance"] = {**perf, "stale": perf["stale_days"] > STALE_DAYS, "estimate": True}
                ref_sym, ref_label = REFERENCES.get(cls, (None, None))
                ref = refs.get(cls)
                ref_return = over(series.get(ref.id, []), perf["start"], perf["end"]) if ref else None
                row["reference"] = {"symbol": ref_sym, "label": ref_label, "return": ref_return} if ref_sym else None
                if ref_return is not None:
                    row["gap"] = round(perf["return"] - ref_return, 4)
                    row["underperforming"] = row["gap"] <= -UNDERPERFORM_GAP
                options = []
                for cand in candidates:
                    if cand.id == inst.id or cand.id not in buyable or str(cand.asset_class) != cls:
                        continue
                    r = over(series.get(cand.id, []), perf["start"], perf["end"])
                    if r is not None:
                        options.append((cand, r))
                row["alternatives_considered"] = len(options)
                better = [(c, r) for c, r in options if r > perf["return"]]
                better.sort(key=lambda cr: (buyable[cr[0].id] != "approved", -cr[1], cr[0].symbol))
                if better:
                    c, r = better[0]
                    row["alternative"] = {"symbol": c.symbol, "name": c.name, "return": r, "shelf": buyable[c.id],
                                          "ahead_by": round(r - perf["return"], 4), "same_dates": True}
            if managed.get(inst.id):
                row["overlap"] = {"managed_value": round(managed[inst.id], 2)}
        if row["underperforming"]:
            findings.append({"kind": "underperforming", "id": row["id"], "symbol": l.symbol, "name": l.instrument_name,
                             "institution": l.institution, "owner": row["owner"], "value": row["value"], "currency": l.currency,
                             "return": row["performance"]["return"], "reference": row["reference"], "gap": row["gap"],
                             "period": [row["performance"]["start"], row["performance"]["end"]], "alternative": row["alternative"],
                             "simulated": row["simulated"]})
        if row["overlap"]:
            findings.append({"kind": "overlap", "id": row["id"], "symbol": l.symbol, "name": l.instrument_name,
                             "institution": l.institution, "owner": row["owner"], "value": row["value"], "currency": l.currency,
                             "managed_value": row["overlap"]["managed_value"], "simulated": row["simulated"]})
        rows.append(row)

    ended = (await session.execute(select(HeldAwayHolding).where(
        HeldAwayHolding.firm_id == firm_id, HeldAwayHolding.household_id == household_id, HeldAwayHolding.status == "ended",
    ).order_by(HeldAwayHolding.ended_on.desc()).limit(SHOWN_ENDED))).scalars().all()
    counted = [r for r in rows if r["counted"]]
    return {
        "household_id": str(household_id), "household": household.name, "currency": firm.base_currency, "as_of": today.isoformat(),
        "people": [{"id": str(p.id), "name": p.full_name} for p in persons] + [{"id": str(e.id), "name": e.name, "entity": True} for e in entities],
        "holdings": rows, "findings": findings,
        "summary": {"holdings": len(rows), "value": round(sum(r["value"] for r in counted), 2),
                    "not_converted": len(rows) - len(counted), "measured": sum(1 for r in rows if r["performance"]),
                    "not_measured": sum(1 for r in rows if not r["performance"]),
                    "underperforming": sum(1 for r in rows if r["underperforming"]), "overlaps": sum(1 for r in rows if r["overlap"]),
                    "simulated": sum(1 for r in rows if r["simulated"])},
        "rule": {"text": RULE, "period_days": PERIOD_DAYS, "gap": UNDERPERFORM_GAP,
                 "references": {cls: label for cls, (_, label) in REFERENCES.items()}},
        "not_modelled": NOT_MODELLED, "estimate": True,
        "ended": [{"id": str(e.id), "name": e.instrument_name, "institution": e.institution, "value": float(e.value),
                   "currency": e.currency, "ended_on": e.ended_on.isoformat() if e.ended_on else None, "reason": e.ended_reason}
                  for e in ended],
    }


async def for_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Every household with something held elsewhere listed, the most findings first."""
    firm = await session.get(Firm, firm_id)
    ids = (await session.execute(select(HeldAwayHolding.household_id).join(Household, Household.id == HeldAwayHolding.household_id)
                                 .where(HeldAwayHolding.firm_id == firm_id, HeldAwayHolding.status == "current",
                                        Household.left_on.is_(None)).distinct())).scalars().all()
    rows = []
    for hid in ids:
        v = await for_household(session, hid, firm_id=firm_id)
        if v:
            rows.append({"household_id": v["household_id"], "household": v["household"], **v["summary"],
                         "findings": v["findings"]})
    rows.sort(key=lambda r: (-r["underperforming"], -r["overlaps"], -r["value"], r["household"]))
    return {"households": rows, "currency": firm.base_currency if firm else None,
            "as_of": firm_time.today(firm).isoformat(), "rule": {"text": RULE, "period_days": PERIOD_DAYS, "gap": UNDERPERFORM_GAP},
            "not_modelled": NOT_MODELLED, "estimate": True,
            "summary": {"households": len(rows), "holdings": sum(r["holdings"] for r in rows),
                        "underperforming": sum(r["underperforming"] for r in rows), "overlaps": sum(r["overlaps"] for r in rows),
                        "simulated": sum(r["simulated"] for r in rows)}}


# ── Recording ───────────────────────────────────────────────────────────────────────────


def _number(value, label: str, *, required: bool) -> float | None:
    if value in (None, ""):
        if required:
            raise HeldAwayError(f"Give the {label} from the statement.")
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise HeldAwayError(f"The {label} must be a number.")
    if out < 0:
        raise HeldAwayError(f"The {label} cannot be negative.")
    return out


async def prepare_holding(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, institution: str | None,
                          instrument_name: str | None, value, value_on: date | None, source_document: str | None,
                          currency: str | None = None, person_id: uuid.UUID | None = None, entity_id: uuid.UUID | None = None,
                          account_label: str | None = None, symbol: str | None = None, isin: str | None = None,
                          asset_class: str | None = None, quantity=None, replaces_id: uuid.UUID | None = None) -> dict:
    """Everything recording would refuse, refused without writing, so a Confirm card that would only fail is not
    shown. Returns what would be written."""
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise HeldAwayError("Household not found.")
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    where = (institution or "").strip()
    if len(where) < 2:
        raise HeldAwayError("Name the institution that holds it.")
    what = (instrument_name or "").strip()
    if len(what) < 2:
        raise HeldAwayError("Name the holding as the statement does.")
    amount = _number(value, "value", required=True)
    units = _number(quantity, "quantity", required=False)
    if value_on is None:
        raise HeldAwayError("Give the statement's date.")
    if value_on > today:
        raise HeldAwayError("A statement cannot be dated in the future.")
    doc = (source_document or "").strip()
    if len(doc) < 3:
        raise HeldAwayError("Say which statement this comes from - its institution and date. A figure with no source cannot be taken to a client.")
    code = (currency or firm.base_currency or "INR").strip().upper()
    if len(code) != 3:
        raise HeldAwayError("Give the currency as a three-letter code.")
    cls = (asset_class or "").strip().lower() or None
    if cls and cls not in ASSET_CLASSES:
        raise HeldAwayError(f"The asset class must be one of: {', '.join(ASSET_CLASSES)}.")
    if person_id and entity_id:
        raise HeldAwayError("A holding is a person's, an entity's or joint - not two of them.")
    person = entity = None
    if person_id is not None:
        person = await session.get(Person, person_id)
        if person is None or person.household_id != household_id:
            raise HeldAwayError("That person is not in this household.")
    if entity_id is not None:
        entity = await session.get(LegalEntity, entity_id)
        if entity is None or entity.household_id != household_id:
            raise HeldAwayError("That entity is not in this household.")
    sym = (symbol or "").strip().upper() or None
    isin_code = (isin or "").strip().upper() or None
    if isin_code and len(isin_code) != 12:
        raise HeldAwayError("An ISIN has twelve characters.")
    replacing = None
    if replaces_id is not None:
        replacing = await session.get(HeldAwayHolding, replaces_id)
        if replacing is None or replacing.household_id != household_id or replacing.status != "current":
            raise HeldAwayError("The holding to replace is not on file for this household.")
        if replacing.source_system:
            raise HeldAwayError("That holding comes from a feed; its next statement replaces it.")
    else:
        same = (await session.execute(select(HeldAwayHolding.id).where(
            HeldAwayHolding.household_id == household_id, HeldAwayHolding.status == "current",
            HeldAwayHolding.institution == where, HeldAwayHolding.instrument_name == what,
            HeldAwayHolding.person_id == person_id if person_id else HeldAwayHolding.person_id.is_(None),
            HeldAwayHolding.entity_id == entity_id if entity_id else HeldAwayHolding.entity_id.is_(None),
        ))).first()
        if same:
            raise HeldAwayError(f"{what} at {where} is already on file. Replace it with the new statement rather than adding it twice.")
    instruments = (await session.execute(select(Instrument).where(Instrument.firm_id == firm_id))).scalars().all()
    inst = _match(sym, instruments)
    return {"household": household, "person": person, "entity": entity, "institution": where[:120], "instrument_name": what[:200],
            "account_label": (account_label or "").strip()[:120] or None, "symbol": sym, "isin": isin_code,
            "asset_class": cls or (str(inst.asset_class) if inst else None), "quantity": units, "value": round(amount, 2),
            "currency": code, "value_on": value_on, "source": doc[:255], "replacing": replacing, "instrument": inst,
            "not_converted": code != (firm.base_currency or "INR").upper()}


async def record_holding(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, **fields) -> dict:
    ready = await prepare_holding(session, household_id, firm_id=firm_id, **fields)
    old = ready["replacing"]
    row = HeldAwayHolding(firm_id=firm_id, household_id=household_id, person_id=ready["person"].id if ready["person"] else None,
                          entity_id=ready["entity"].id if ready["entity"] else None, institution=ready["institution"],
                          account_label=ready["account_label"], symbol=ready["symbol"], isin=ready["isin"],
                          instrument_name=ready["instrument_name"], asset_class=ready["asset_class"], quantity=ready["quantity"],
                          value=ready["value"], currency=ready["currency"], value_on=ready["value_on"],
                          source_document=ready["source"], recorded_by=by, status="current", attributes={})
    session.add(row)
    if old is not None:
        old.status, old.ended_on, old.ended_by = "ended", ready["value_on"], by
        old.ended_reason = f"Replaced by the statement of {ready['value_on'].isoformat()}"
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="held_away_holding_recorded", actor=by, content={
        "holding_id": str(row.id), "household_id": str(household_id), "institution": row.institution, "symbol": row.symbol,
        "name": row.instrument_name, "value": ready["value"], "currency": row.currency, "value_on": ready["value_on"].isoformat(),
        "source_document": row.source_document, "replaces": str(old.id) if old else None,
    })
    view = await for_household(session, household_id, firm_id=firm_id)
    return {**view, "holding_id": str(row.id), "recorded": f"{row.instrument_name} at {row.institution}"}


async def prepare_end(session: AsyncSession, holding_id: uuid.UUID, *, firm_id: uuid.UUID, reason: str | None) -> dict:
    text = (reason or "").strip()
    if len(text) < 3:
        raise HeldAwayError("Say why this no longer stands - sold, transferred in, closed.")
    row = await session.get(HeldAwayHolding, holding_id)
    if row is None or row.firm_id != firm_id:
        raise HeldAwayError("That holding was not found.")
    if row.status != "current":
        raise HeldAwayError("This holding has already ended.")
    return {"holding": row, "reason": text[:255], "household": await session.get(Household, row.household_id)}


async def end_holding(session: AsyncSession, holding_id: uuid.UUID, *, firm_id: uuid.UUID, reason: str | None, by: str) -> dict:
    """Mark a holding as no longer held there. Kept, with why. The row is locked and re-read, so two people ending it
    cannot both record a reason. A feed's holding can be ended too; if the feed delivers it again, it comes back."""
    ready = await prepare_end(session, holding_id, firm_id=firm_id, reason=reason)
    row = (await session.execute(select(HeldAwayHolding).where(HeldAwayHolding.id == holding_id).with_for_update()
                                 .execution_options(populate_existing=True))).scalar_one()
    if row.status != "current":
        raise HeldAwayError("This holding has already ended.")
    row.status, row.ended_on, row.ended_by, row.ended_reason = "ended", await firm_time.today_of(session, firm_id), by, ready["reason"]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="held_away_holding_ended", actor=by, content={
        "holding_id": str(row.id), "household_id": str(row.household_id), "name": row.instrument_name,
        "institution": row.institution, "reason": ready["reason"],
    })
    view = await for_household(session, row.household_id, firm_id=firm_id)
    return {**view, "ended_holding": f"{row.instrument_name} at {row.institution}"}
