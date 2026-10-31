"""Indian capital gains, per taxpayer and per demat, for Tax Year 2026-27.

Indian tax has no household. A husband, a wife and their HUF are three taxpayers, a loss never
crosses between them, and a sale relieves the oldest lot in that demat whatever anyone would prefer.
So this reads the book taxpayer by taxpayer, where the other reports in tax_intelligence read it
household-wide.

Everything here is an estimate for an adviser and a qualified person to check. The rates are the
Income-tax Act 2025 as read in the Gazette copy, overlaid with the Finance Bill 2026; the enacted
Finance Act and the 2026 Rules were not read. What it leaves out is listed in NOT_MODELLED and
travels with every report, and a figure it cannot work out is reported missing, not filled in.

    st_equity  listed equity, equity funds, REITs and InvITs held 12 months or less: 20%
    lt_equity  the same held longer: 12.5% above the Rs 1.25 lakh exemption
    st_slab    specified funds, always; other listed funds and bonds held 12 months or less: slab
    lt_other   other listed funds and bonds held longer: 12.5%, no exemption
"""
from __future__ import annotations

import calendar
import math
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, markets
from app.aurea_core.valuation import latest_prices
from app.models.graph import Account, LegalEntity, Mandate, Person
from app.models.portfolio import Holding, Instrument, TaxLot, Transaction

LAW = "Income-tax Act 2025"
ST_EQUITY, LT_EQUITY, ST_SLAB, LT_OTHER = "st_equity", "lt_equity", "st_slab", "lt_other"
BUCKETS = (ST_EQUITY, LT_EQUITY, ST_SLAB, LT_OTHER)
LONG_TERM_BUCKETS = (LT_EQUITY, LT_OTHER)

LTCG_EXEMPTION = 125_000
RATE_ST_EQUITY = 0.20
RATE_LT = 0.125
CESS = 0.04
SURCHARGE_CAP = 0.15        # on dividends and gains at special rates; slab-taxed gains are not capped
GRANDFATHER_BEFORE = date(2018, 2, 1)
GAAR_THRESHOLD = 30_000_000  # Rs 3 crore of tax benefit in a year; Rule 10U, 2026 equivalent unconfirmed
HARVEST_MIN = 10_000        # below this a harvest is not worth a trade
TURNING_LONG_DAYS = 30      # a short-term loss within this of going long-term is the better loss

EQUITY_CLASSES = ("listed_equity", "equity_fund", "reit_invit")

NOT_MODELLED = [
    "Marginal relief on surcharge",
    "Section 156 rebate",
    "Basic exemption shortfall",
    "Old and new regime comparison",
    "Grandfathering to 31 Jan 2018 value",
    "Advance tax",
    "Dividends and interest",
    "TDS",
]


def tax_class_of(instrument: Instrument | None) -> str:
    """How the Act treats an instrument: set per instrument, because an ETF can be either."""
    tags = (instrument.values_tags or {}) if instrument else {}
    if tags.get("tax_class"):
        return tags["tax_class"]
    return "listed_equity" if instrument and str(instrument.asset_class) == "equity" else "other_listed"


def add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def long_term_from(tax_class: str, acquired_on: date) -> date | None:
    """The first day a sale of this lot is long-term. None for what never goes long-term."""
    if tax_class == "specified_fund":
        return None
    months = 24 if tax_class == "unlisted" else 12
    return date.fromordinal(add_months(acquired_on, months).toordinal() + 1)


def bucket(tax_class: str, acquired_on: date, sold_on: date) -> str:
    begins = long_term_from(tax_class, acquired_on)
    long_term = begins is not None and sold_on >= begins
    if tax_class in EQUITY_CLASSES:
        return LT_EQUITY if long_term else ST_EQUITY
    return LT_OTHER if long_term else ST_SLAB


def set_off(gains: dict, brought: dict | None = None, *, slab_rate: float | None = None) -> dict:
    """Losses against gains: short-term against anything, long-term against long-term only.

    The Act does not order the buckets, so the highest-taxed goes first, the taxpayer's reading and
    one for a tax professional to confirm. lt_other goes before lt_equity because lt_equity has the
    exemption to fall back on. What is left over carries forward."""
    net = {b: max(float(gains.get(b) or 0), 0.0) for b in BUCKETS}
    current_lt = sum(-min(float(gains.get(b) or 0), 0.0) for b in LONG_TERM_BUCKETS)
    current_st = sum(-min(float(gains.get(b) or 0), 0.0) for b in (ST_EQUITY, ST_SLAB))
    brought = brought or {}
    long_losses = current_lt + float(brought.get("long_term") or 0)
    short_losses = current_st + float(brought.get("short_term") or 0)

    def absorb(loss: float, order: list[str]) -> float:
        for b in order:
            used = min(loss, net[b])
            net[b] -= used
            loss -= used
        return loss

    long_left = absorb(long_losses, [LT_OTHER, LT_EQUITY])
    slab_first = (slab_rate or 0) > RATE_ST_EQUITY
    short_order = [ST_SLAB, ST_EQUITY] if slab_first else [ST_EQUITY, ST_SLAB]
    short_left = absorb(short_losses, short_order + [LT_OTHER, LT_EQUITY])

    exempt_used = min(net[LT_EQUITY], LTCG_EXEMPTION)
    return {
        "net": {b: round(v, 2) for b, v in net.items()},
        "exempt_used": round(exempt_used, 2),
        "exempt_left": round(LTCG_EXEMPTION - exempt_used, 2),
        "taxable_lt_equity": round(net[LT_EQUITY] - exempt_used, 2),
        "carry_forward": {"short_term": round(short_left, 2), "long_term": round(long_left, 2)},
    }


def surcharge_rate(total_income: float) -> float:
    """New-regime surcharge by income. Tested on the total here; the Act tests two ways."""
    if total_income > 20_000_000:
        return 0.25
    if total_income > 10_000_000:
        return 0.15
    if total_income > 5_000_000:
        return 0.10
    return 0.0


def tax_on(position: dict, *, slab_rate: float | None, income: float | None) -> dict:
    """The tax on what set_off left. Slab gains with no slab rate on file are not guessed at."""
    net = position["net"]
    special = (net[ST_EQUITY] * RATE_ST_EQUITY + position["taxable_lt_equity"] * RATE_LT
               + net[LT_OTHER] * RATE_LT)
    total_gains = sum(net.values())
    surcharge = surcharge_rate((income or 0) + total_gains)
    amount = special * (1 + min(surcharge, SURCHARGE_CAP)) * (1 + CESS)
    if slab_rate is not None:
        amount += net[ST_SLAB] * slab_rate * (1 + surcharge) * (1 + CESS)
    return {
        "amount": round(amount, 2),
        "surcharge_rate": surcharge,
        "incomplete": [m for m, gap in (("slab_rate", slab_rate is None and net[ST_SLAB] > 0),
                                        ("income", income is None)) if gap],
    }


def _merge(a: dict, b: dict) -> dict:
    return {k: round(float(a.get(k) or 0) + float(b.get(k) or 0), 2) for k in BUCKETS}


def _tax_inputs(person: Person | None, entity: LegalEntity | None) -> dict:
    tax = ((person.profile or {}).get("tax") if person else ((entity.governance or {}).get("tax") if entity else None)) or {}
    return {
        "slab_rate": tax.get("marginal_rate"), "income": tax.get("annual_income"),
        "regime": tax.get("regime") or "new", "brought": tax.get("losses_brought_forward") or {},
    }


def _realised(txn: Transaction, tax_class: str) -> dict:
    """A sale's gain by bucket, from the lots settlement relieved. It carries them because a lot's
    date is gone once the lot is."""
    out = {b: 0.0 for b in BUCKETS}
    lineage = txn.lineage or {}
    qty = float(txn.quantity or 0) or 1.0
    fees = float(lineage.get("fees") or 0)
    for lot in lineage.get("lots") or []:
        share = float(lot["quantity"]) / qty
        gain = float(lot["quantity"]) * float(txn.price) - float(lot["cost"]) - fees * share
        out[bucket(tax_class, date.fromisoformat(lot["acquired_on"]), txn.trade_date)] += gain
    return out


async def _sales(session: AsyncSession, account_ids: list, year: dict) -> list[Transaction]:
    if not account_ids:
        return []
    return (await session.execute(
        select(Transaction).where(Transaction.account_id.in_(account_ids), Transaction.txn_type == "sell",
                                  Transaction.trade_date >= year["start"], Transaction.trade_date <= year["end"])
    )).scalars().all()


async def _instruments(session: AsyncSession, ids: set) -> dict:
    if not ids:
        return {}
    return {i.id: i for i in (await session.execute(select(Instrument).where(Instrument.id.in_(ids)))).scalars().all()}


async def realised_this_year(session: AsyncSession, account_ids: list, year: dict) -> dict:
    sales = await _sales(session, account_ids, year)
    instruments = await _instruments(session, {t.instrument_id for t in sales if t.instrument_id})
    total = {b: 0.0 for b in BUCKETS}
    for t in sales:
        total = _merge(total, _realised(t, tax_class_of(instruments.get(t.instrument_id))))
    return total


def _harvest_benefit(realised: dict, inputs: dict) -> float:
    """Tax this year's losses saved, for the GAAR tracker: what the same gains would have cost
    without them."""
    with_losses = tax_on(set_off(realised, inputs["brought"], slab_rate=inputs["slab_rate"]),
                         slab_rate=inputs["slab_rate"], income=inputs["income"])
    gains_only = {b: max(float(realised.get(b) or 0), 0.0) for b in BUCKETS}
    without = tax_on(set_off(gains_only, inputs["brought"], slab_rate=inputs["slab_rate"]),
                     slab_rate=inputs["slab_rate"], income=inputs["income"])
    return round(max(without["amount"] - with_losses["amount"], 0.0), 2)


async def owner_accounts(session: AsyncSession, firm_id: uuid.UUID, mandate: Mandate) -> list[Account]:
    """Every account the mandate's owner holds. One taxpayer can sit behind several mandates."""
    owner = Mandate.person_id == mandate.person_id if mandate.person_id else Mandate.entity_id == mandate.entity_id
    return (await session.execute(
        select(Account).join(Mandate, Mandate.id == Account.mandate_id).where(Mandate.firm_id == firm_id, owner)
    )).scalars().all()


async def position_for(session: AsyncSession, firm, mandate: Mandate, *, today: date) -> dict:
    """Where this mandate's taxpayer stands in the tax year, for sizing a proposal against it."""
    person = await session.get(Person, mandate.person_id) if mandate.person_id else None
    entity = await session.get(LegalEntity, mandate.entity_id) if mandate.entity_id else None
    inputs = _tax_inputs(person, entity)
    year = markets.tax_year(markets.MARKETS["IN"], today)
    accounts = await owner_accounts(session, firm.id, mandate)
    realised = await realised_this_year(session, [a.id for a in accounts], year)
    return {
        "taxpayer": person.full_name if person else (entity.name if entity else None),
        "tax_year": year["label"], "tax_year_end": year["end"].isoformat(), "realised": realised,
        "brought": inputs["brought"], "slab_rate": inputs["slab_rate"], "income": inputs["income"],
        "exempt_left": set_off(realised, inputs["brought"], slab_rate=inputs["slab_rate"])["exempt_left"],
        "benefit_used": _harvest_benefit(realised, inputs), "benefit_limit": GAAR_THRESHOLD,
    }


def proposal_tax(buckets: dict, position: dict) -> dict:
    """What a proposal adds to the year's tax: the year with it, less the year without it."""
    slab, income = position.get("slab_rate"), position.get("income")
    before = tax_on(set_off(position["realised"], position["brought"], slab_rate=slab), slab_rate=slab, income=income)
    after_gains = _merge(position["realised"], buckets)
    settled = set_off(after_gains, position["brought"], slab_rate=slab)
    after = tax_on(settled, slab_rate=slab, income=income)
    return {
        "estimated_tax": round(after["amount"] - before["amount"], 2),
        "exempt_used_by_this": round(settled["exempt_used"] - (125_000 - position["exempt_left"]), 2),
        "exempt_left_after": settled["exempt_left"],
        "incomplete": after["incomplete"],
    }


def _opportunities(row: dict, position: dict, inputs: dict, *, today: date, tax_year_end: date) -> list[dict]:
    """What a sale of this holding would do, walking its lots the only way the law allows."""
    out: list[dict] = []
    price, lots, tax_class = row["price"], row["lots"], row["tax_class"]
    if price <= 0:
        return out
    base = dict(symbol=row["symbol"], instrument=row["name"], account=row["account"], account_id=row["account_id"])

    # A loss is what the walk reaches after whatever gain sits on older lots in front of it.
    running, deepest, deepest_at = 0.0, 0.0, -1
    for i, lot in enumerate(lots):
        running += (price - lot["cost"]) * lot["quantity"]
        if running < deepest:
            deepest, deepest_at = running, i
    if -deepest >= HARVEST_MIN:
        taken = lots[:deepest_at + 1]
        split = {b: 0.0 for b in BUCKETS}
        for lot in taken:
            split[bucket(tax_class, lot["acquired_on"], today)] += (price - lot["cost"]) * lot["quantity"]
        with_it = _merge(position["realised"], split)
        now = tax_on(set_off(position["realised"], inputs["brought"], slab_rate=inputs["slab_rate"]),
                     slab_rate=inputs["slab_rate"], income=inputs["income"])
        settled = set_off(with_it, inputs["brought"], slab_rate=inputs["slab_rate"])
        then = tax_on(settled, slab_rate=inputs["slab_rate"], income=inputs["income"])
        out.append({
            **base, "kind": "loss", "quantity": sum(l["quantity"] for l in taken), "net_loss": round(deepest, 2),
            "gain_en_route": round(sum(max((price - l["cost"]) * l["quantity"], 0.0) for l in taken), 2),
            "long_term": sum(split[b] for b in LONG_TERM_BUCKETS) < 0,
            "tax_saved": round(max(now["amount"] - then["amount"], 0.0), 2),
            "carries_forward": round(sum(settled["carry_forward"].values())
                                     - sum(float(v or 0) for v in inputs["brought"].values()), 2),
        })

    # A short-term loss at the front of the queue that will soon be long-term, and worth less.
    first = lots[0] if lots else None
    if first and tax_class in EQUITY_CLASSES and price < first["cost"]:
        turns = long_term_from(tax_class, first["acquired_on"])
        if turns and 0 <= (turns - today).days <= TURNING_LONG_DAYS:
            out.append({**base, "kind": "turning_long", "turns_on": turns.isoformat(),
                        "days_left": (turns - today).days,
                        "loss": round((price - first["cost"]) * first["quantity"], 2),
                        "quantity": first["quantity"]})
    return out


def _exemption_harvest(rows: list[dict], exempt_left: float, today: date, rate: float) -> list[dict]:
    """How to use what is left of the exemption: long-term gains at the front of each FIFO queue."""
    out, left = [], exempt_left
    for row in sorted(rows, key=lambda r: r["symbol"]):
        if left < HARVEST_MIN or row["price"] <= 0 or row["tax_class"] not in EQUITY_CLASSES:
            continue
        qty = gain = 0.0
        for lot in row["lots"]:
            per_unit = row["price"] - lot["cost"]
            if per_unit <= 0 or bucket(row["tax_class"], lot["acquired_on"], today) != LT_EQUITY:
                break
            units = min(lot["quantity"], math.floor(left / per_unit))
            qty, gain = qty + units, gain + units * per_unit
            left -= units * per_unit
            if units < lot["quantity"]:
                break
        if qty > 0:
            out.append({"kind": "exemption", "symbol": row["symbol"], "instrument": row["name"],
                        "account": row["account"], "account_id": row["account_id"], "quantity": qty,
                        "gain": round(gain, 2), "tax_saved": round(gain * rate, 2)})
    return out


async def report(session: AsyncSession, brain: dict, *, firm, today: date | None = None) -> dict:
    """One household's Indian tax position, taxpayer by taxpayer. Estimates, with what they omit."""
    today = today or firm_time.today(firm)
    year = markets.tax_year(markets.MARKETS["IN"], today)
    return_due = date(year["end"].year, 7, 31)

    account_ids = [uuid.UUID(a["id"]) for a in brain.get("accounts", [])]
    accounts = {a.id: a for a in (await session.execute(
        select(Account).where(Account.id.in_(account_ids)))).scalars().all()} if account_ids else {}
    holdings = (await session.execute(
        select(Holding).where(Holding.account_id.in_(account_ids)))).scalars().all() if account_ids else []
    lots_by_holding: dict = {}
    if holdings:
        for lot in (await session.execute(
                select(TaxLot).where(TaxLot.holding_id.in_([h.id for h in holdings])))).scalars().all():
            lots_by_holding.setdefault(lot.holding_id, []).append(lot)
    instruments = await _instruments(session, {h.instrument_id for h in holdings})
    prices = await latest_prices(session, firm.id)

    owner_of: dict = {}     # account id -> taxpayer key
    for m in brain.get("mandates", []):
        owner_of[m["id"]] = m.get("person_id") or m.get("entity_id")
    payers: dict = {}
    for acc in accounts.values():
        key = owner_of.get(str(acc.mandate_id)) or f"account:{acc.id}"
        payers.setdefault(key, {"accounts": [], "rows": []})["accounts"].append(acc)

    for h in holdings:
        acc = accounts[h.account_id]
        key = owner_of.get(str(acc.mandate_id)) or f"account:{acc.id}"
        inst = instruments.get(h.instrument_id)
        price_row = prices.get(h.instrument_id)
        qty = float(h.quantity or 0)
        price = float(price_row.close) if price_row else (float(h.market_value or 0) / qty if qty else 0.0)
        lots = sorted(lots_by_holding.get(h.id, []), key=lambda l: l.acquired_on)
        payers[key]["rows"].append({
            "symbol": inst.symbol if inst else "?", "name": inst.name if inst else "",
            "tax_class": tax_class_of(inst), "account": acc.name, "account_id": str(acc.id), "price": price,
            "lots": [{"quantity": float(l.quantity), "cost": float(l.cost_per_unit), "acquired_on": l.acquired_on}
                     for l in lots],
        })

    taxpayers: list[dict] = []
    for key, data in payers.items():
        person = await session.get(Person, uuid.UUID(key)) if not key.startswith("account:") else None
        entity = None
        if person is None and not key.startswith("account:"):
            entity = await session.get(LegalEntity, uuid.UUID(key))
        inputs = _tax_inputs(person, entity)
        ids = [a.id for a in data["accounts"]]
        realised = await realised_this_year(session, ids, year)
        position = {"realised": realised}
        settled = set_off(realised, inputs["brought"], slab_rate=inputs["slab_rate"])
        tax = tax_on(settled, slab_rate=inputs["slab_rate"], income=inputs["income"])

        unrealised = {b: 0.0 for b in BUCKETS}
        grandfathered = []
        opportunities: list[dict] = []
        for row in data["rows"]:
            for lot in row["lots"]:
                unrealised[bucket(row["tax_class"], lot["acquired_on"], today)] += (row["price"] - lot["cost"]) * lot["quantity"]
            old = [l for l in row["lots"] if l["acquired_on"] < GRANDFATHER_BEFORE]
            if old:
                grandfathered.append({"symbol": row["symbol"], "account": row["account"],
                                      "quantity": sum(l["quantity"] for l in old)})
            opportunities += _opportunities(row, position, inputs, today=today, tax_year_end=year["end"])

        lt_rate = RATE_LT * (1 + min(tax["surcharge_rate"], SURCHARGE_CAP)) * (1 + CESS)
        clean_rows = [r for r in data["rows"]
                      if not any(l["acquired_on"] < GRANDFATHER_BEFORE for l in r["lots"])]
        opportunities += _exemption_harvest(clean_rows, settled["exempt_left"], today, lt_rate)

        realised_loss = any(float(v) < 0 for v in realised.values())
        taxpayers.append({
            "id": key,
            "name": person.full_name if person else (entity.name if entity else data["accounts"][0].name),
            "kind": str(entity.entity_type) if entity else "individual",
            "regime": inputs["regime"], "slab_rate": inputs["slab_rate"], "income": inputs["income"],
            "demats": [{"id": str(a.id), "name": a.name, "custodian": a.custodian} for a in data["accounts"]],
            "realised": realised, "brought_forward": inputs["brought"], "position": settled,
            "estimated_tax": tax["amount"], "surcharge_rate": tax["surcharge_rate"], "incomplete": tax["incomplete"],
            "unrealised": {b: round(v, 2) for b, v in unrealised.items()},
            "opportunities": sorted(opportunities, key=lambda o: -float(o.get("tax_saved") or 0)),
            "grandfathered": grandfathered,
            "return_needed_by": return_due.isoformat() if realised_loss else None,
            "benefit": {"used": _harvest_benefit(realised, inputs), "limit": GAAR_THRESHOLD},
        })

    taxpayers.sort(key=lambda t: (t["kind"] != "individual", t["name"]))
    opportunities = [o for t in taxpayers for o in t["opportunities"]]
    return {
        "jurisdiction": "IN", "currency": "INR", "law": LAW, "tax_year": year["label"],
        "tax_year_end": year["end"].isoformat(), "days_to_year_end": year["days_left"],
        "taxpayers": taxpayers, "not_modelled": NOT_MODELLED, "estimate_only": True,
        "summary": {
            "total_flags": len(opportunities) + sum(1 for t in taxpayers if t["grandfathered"]),
            "taxpayers": len(taxpayers),
            "estimated_tax": round(sum(t["estimated_tax"] for t in taxpayers), 2),
            "harvestable_loss": round(sum(-o["net_loss"] for o in opportunities if o["kind"] == "loss"), 2),
            "estimated_tax_saving": round(sum(float(o.get("tax_saved") or 0) for o in opportunities), 2),
            "exemption_unused": round(sum(t["position"]["exempt_left"] for t in taxpayers), 2),
            "turning_long": sum(1 for o in opportunities if o["kind"] == "turning_long"),
        },
    }
