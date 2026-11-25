"""How a mutual fund order travelled, and which day's NAV it got (L100 India edition section 6.1, B-294).

A mutual fund order does not settle like a share. It reaches the fund house by a rail (BSE StAR MF, NSE NMF II, MFCentral, the AMC's own
site), the registrar (CAMS or KFintech) holds the folio and allots the units, and the day's NAV it gets depends on a cut-off time and, for a
large purchase, on when the money was realised. `blotter.py` works a settlement date from the market and says nothing of that, so the order
date and the NAV date were not told apart. This module records the facts and works the one thing the rule gives:

  recorded     the rail, the registrar, the folio, the rule the order was treated under, when the rail took it, when funds were realised (a
               purchase) and the NAV day the registrar reports, each from the rail's or the registrar's confirmation, never invented
  worked       the NAV day the cut-off rule gives from the order's time (`law_figures`: the cut-off times and the large-amount line, each
               ASSUMED from SEBI's circulars as understood and unreviewed by counsel, because the L100 edition gives the rule and no figure)
  reported     an order whose recorded NAV day differs from the one the rule gives, as a break with its likely cause. Nothing is repaired: no
               holding, unit or cash is written, and a break is cleared by a corrected record from the registrar's confirmation or a person's note

The rule: an order taken up to the cut-off on a business day gets that day's NAV, one taken after it (or on a weekend) the next business
day's. A purchase at or above the large-amount line, and any purchase in a liquid or overnight scheme, gets the later of that and the day the
funds were realised, so the day the funds were realised must be on file or the order is "not assessed"; the day recorded is read as realised
before the cut-off. A redemption has no realisation step. Business days are weekdays: exchange and AMC holidays are not modelled, so near a
holiday the rule's day may be earlier than the registrar's, and that shows as a break a person can clear. A scheme's category is not read from
its name: the person records the rule the order was treated under. No record does not mean the order was mishandled.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, licences, markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Account, Household, Mandate
from app.models.india_batch_c import MfOrderRecord
from app.models.portfolio import Instrument
from app.models.tenant import Firm
from app.models.trading import Order
from app.provenance import ledger

RECORDERS = frozenset({UserRole.OPERATIONS, UserRole.ADVISER, UserRole.PARAPLANNER})
RAILS = {"bse_star_mf": "BSE StAR MF", "nse_nmf": "NSE NMF II", "mfcentral": "MFCentral", "amc_direct": "The AMC directly", "other": "Another rail"}
RTAS = {"cams": "CAMS", "kfintech": "KFintech"}
RULES = {"standard": "Standard cut-off", "liquid_overnight": "Liquid or overnight scheme"}
FIGURES = ("in_mf_cutoff_standard", "in_mf_cutoff_liquid_purchase", "in_mf_cutoff_liquid_redemption", "in_mf_large_amount")
NOT_MODELLED = ["exchange and AMC holidays (a business day here is a weekday)", "a scheme's category (the person records the rule it was treated under)",
                "the time funds were realised within the day (a day recorded is read as before the cut-off)", "switches and systematic transfers"]
SAID = ("The NAV day the cut-off rule gives, from the order's time. The cut-off times and the large-amount line are assumed from SEBI's circulars and not "
        "reviewed by counsel; a difference is reported for a person to look at and nothing is changed.")


class MfOrderError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def in_force(firm: Firm | None) -> bool:
    """Whether the blotter reads the records: an Indian firm that holds a licence. The blotter asks this and not `applies`, so a firm with none,
    which has no mutual fund order to read, never touches the table."""
    return applies(firm) and bool(licences.held(firm))


def is_fund(instrument: Instrument | None) -> bool:
    tags = (instrument.values_tags or {}) if instrument else {}
    return bool(tags.get("amfi_code") or tags.get("fund_plan"))


def _business(day: date) -> bool:
    return day.weekday() < 5


def _next_business(day: date, *, inclusive: bool) -> date:
    d = day if inclusive else day + timedelta(days=1)
    while not _business(d):
        d += timedelta(days=1)
    return d


def _minutes(value: str | None) -> int:
    try:
        hh, mm = str(value or "").strip().split(":")
        h, m = int(hh), int(mm)
    except ValueError:
        raise MfOrderError("Give the time the rail took the order as HH:MM, 24-hour, in the firm's time.")
    if not (0 <= h < 24 and 0 <= m < 60):
        raise MfOrderError("Give the time the rail took the order as HH:MM, 24-hour, in the firm's time.")
    return h * 60 + m


def cutoff_for(rule: str, side: str, today: date) -> int:
    if rule == "liquid_overnight":
        return int(law_figures.value("in_mf_cutoff_liquid_purchase" if side == "buy" else "in_mf_cutoff_liquid_redemption", today))
    return int(law_figures.value("in_mf_cutoff_standard", today))


def _clock(minutes: int) -> str:
    return f"{minutes // 60}:{minutes % 60:02d}"


def nav_date_for(*, side: str, rule: str, order_on: date, order_time: str, amount: float | None, funds_realised_on: date | None, today: date) -> dict:
    """The NAV day the rule gives, with how it got there, or why it cannot be said. Pure: it reads only `law_figures`."""
    cutoff = cutoff_for(rule, side, today)
    mins = _minutes(order_time)
    in_time = _business(order_on) and mins <= cutoff
    base = _next_business(order_on, inclusive=in_time)
    basis = [f"Taken at {_time_label(mins)} against a cut-off of {_time_label(cutoff)}: "
             + ("in time, so that day's NAV" if in_time else "after the cut-off, or not a business day, so the next business day's NAV")]
    large = float(law_figures.value("in_mf_large_amount", today))
    needs_realisation = side == "buy" and (rule == "liquid_overnight" or (amount is not None and float(amount) >= large))
    if side == "buy" and rule != "liquid_overnight" and amount is None:
        return {"state": "not_assessed", "nav_date": None, "base": base, "basis": basis, "cutoff": cutoff,
                "needs": "the amount, to say whether the realisation rule applies"}
    if needs_realisation:
        if funds_realised_on is None:
            return {"state": "not_assessed", "nav_date": None, "base": base, "basis": basis, "cutoff": cutoff,
                    "needs": "the day the funds were realised, which the NAV of a large or liquid-scheme purchase follows"}
        realised = _next_business(funds_realised_on, inclusive=True)
        nav = max(base, realised)
        basis.append(f"Funds realised {realised.isoformat()}: the NAV follows the later of the two")
        return {"state": "assessed", "nav_date": nav, "base": base, "basis": basis, "cutoff": cutoff, "realisation": True}
    return {"state": "assessed", "nav_date": base, "base": base, "basis": basis, "cutoff": cutoff, "realisation": False}


def _time_label(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h % 12 or 12}:{m:02d} {'am' if h < 12 else 'pm'}"


def compare(rec: MfOrderRecord, order: Order | None, today: date) -> dict:
    """The record against the rule: the day the rule gives, the day reported, whether they differ and the likely cause."""
    amount = float(rec.amount_inr) if rec.amount_inr is not None else (
        round(float(order.filled_quantity or 0) * float(order.avg_fill_price or 0), 2) if order is not None and float(order.filled_quantity or 0) else None)
    side = str(order.side) if order is not None else "buy"
    rule = nav_date_for(side=side, rule=rec.cutoff_rule, order_on=rec.order_on, order_time=rec.order_time, amount=amount,
                        funds_realised_on=rec.funds_realised_on, today=today)
    out = {"side": side, "amount": amount, "expected": rule["nav_date"].isoformat() if rule["nav_date"] else None, "state": rule["state"],
           "basis": rule["basis"], "needs": rule.get("needs"), "reported": rec.nav_date_reported.isoformat(), "differs": False, "gap_days": None, "likely_cause": None}
    if rule["state"] != "assessed":
        return out
    gap = (rec.nav_date_reported - rule["nav_date"]).days
    out["gap_days"] = gap
    if gap:
        out["state"], out["differs"] = "differs", True
        out["likely_cause"] = ("The registrar allotted a later day than the rule gives: the funds may have been realised later than recorded, the order may have been "
                               "treated as after the cut-off, or a holiday the rule does not know intervened." if gap > 0 else
                               "The registrar allotted an earlier day than the rule gives: the time the rail took the order, or the day funds were realised, may be mis-recorded.")
    else:
        out["state"] = "matches"
    return out


async def _latest(session: AsyncSession, firm_id: uuid.UUID, order_ids: list[uuid.UUID] | None = None) -> dict[uuid.UUID, MfOrderRecord]:
    stmt = select(MfOrderRecord).where(MfOrderRecord.firm_id == firm_id)
    if order_ids is not None:
        stmt = stmt.where(MfOrderRecord.order_id.in_(order_ids or [uuid.uuid4()]))
    out: dict[uuid.UUID, MfOrderRecord] = {}
    for r in (await session.execute(stmt.order_by(MfOrderRecord.created_at, MfOrderRecord.id))).scalars().all():
        out[r.order_id] = r
    return out


async def for_blotter(session: AsyncSession, firm: Firm, order_ids: list[uuid.UUID]) -> dict[str, dict]:
    """What the blotter shows beside an order: the order date and the NAV date apart, the rail and the registrar. Empty where the firm does not read them."""
    if not in_force(firm) or not order_ids:
        return {}
    today = firm_time.today(firm)
    recs = await _latest(session, firm.id, order_ids)
    orders = {o.id: o for o in (await session.execute(select(Order).where(Order.id.in_(list(recs) or [uuid.uuid4()])))).scalars().all()}
    out = {}
    for oid, r in recs.items():
        c = compare(r, orders.get(oid), today)
        out[str(oid)] = {"order_on": r.order_on.isoformat(), "order_time": r.order_time, "nav_date": r.nav_date_reported.isoformat(), "expected_nav_date": c["expected"],
                         "nav_state": c["state"], "rail": r.rail, "rail_label": RAILS.get(r.rail, r.rail), "rta": r.rta, "rta_label": RTAS.get(r.rta, r.rta),
                         "folio": r.folio}
    return out


async def prepare(session: AsyncSession, firm: Firm, role, *, order_id, rail: str | None = None, rta: str | None = None, cutoff_rule: str | None = None,
                  order_on=None, order_time: str | None = None, nav_date_reported=None, document: str | None = None, folio: str | None = None,
                  funds_realised_on=None, amount_inr=None, reason: str | None = None) -> dict:
    if not applies(firm):
        raise MfOrderError("A mutual fund order's rail and NAV day are recorded for an Indian firm.")
    if role not in RECORDERS:
        raise MfOrderError("Operations, an adviser or a paraplanner records how an order travelled, from the rail's or the registrar's confirmation.")
    try:
        oid = order_id if isinstance(order_id, uuid.UUID) else uuid.UUID(str(order_id))
    except ValueError:
        raise MfOrderError("Give the order from the blotter.")
    order = await session.get(Order, oid)
    if order is None or order.firm_id != firm.id:
        raise MfOrderError("Order not found.")
    inst = await session.get(Instrument, order.instrument_id)
    if not is_fund(inst):
        raise MfOrderError(f"{inst.symbol if inst else 'That instrument'} is not a mutual fund on file, so it has no rail or NAV day to record.")
    r = (rail or "").strip().lower()
    if r not in RAILS:
        raise MfOrderError(f"The rail is one of {', '.join(RAILS)}.")
    t = (rta or "").strip().lower()
    if t not in RTAS:
        raise MfOrderError("The registrar is cams or kfintech, as the confirmation names it.")
    rule = (cutoff_rule or "").strip().lower()
    if rule not in RULES:
        raise MfOrderError(f"The cut-off rule is one of {', '.join(RULES)}: say which the order was treated under.")
    today = firm_time.today(firm)
    days = {}
    for label, v, needed in (("order date", order_on, True), ("NAV date", nav_date_reported, True), ("funds-realised date", funds_realised_on, False)):
        if v in (None, ""):
            if needed:
                raise MfOrderError(f"Give the {label} as YYYY-MM-DD, as the confirmation shows it.")
            days[label] = None
            continue
        try:
            days[label] = v if isinstance(v, date) else date.fromisoformat(str(v))
        except ValueError:
            raise MfOrderError(f"Give the {label} as YYYY-MM-DD.")
    day, nav, realised = days["order date"], days["NAV date"], days["funds-realised date"]
    if day > today:
        raise MfOrderError("An order cannot be dated ahead.")
    if nav < day:
        raise MfOrderError("The NAV date cannot be before the order date.")
    if realised is not None and realised < day - timedelta(days=5):
        raise MfOrderError("The funds were realised too long before the order to be read as its funds.")
    _minutes(order_time)
    if len((document or "").strip()) < 5:
        raise MfOrderError("Say where the rail's or the registrar's confirmation is filed.")
    amount = None
    if amount_inr not in (None, ""):
        try:
            amount = round(float(str(amount_inr).replace(",", "")), 2)
        except ValueError:
            raise MfOrderError("Give the amount in rupees, as the confirmation shows it.")
        if amount <= 0:
            raise MfOrderError("The amount must be more than nothing.")
    before = (await _latest(session, firm.id, [oid])).get(oid)
    if before is not None and not (reason or "").strip():
        raise MfOrderError("A record is already on file for this order: a correction says why, and the earlier one stays.")
    probe = MfOrderRecord(firm_id=firm.id, order_id=oid, rail=r, rta=t, folio=(folio or "").strip() or None, cutoff_rule=rule, order_on=day,
                          order_time=order_time.strip(), funds_realised_on=realised, nav_date_reported=nav, amount_inr=amount, source_document=document.strip(),
                          reason=(reason or "").strip() or None, recorded_by="")
    return {"order": order, "instrument": inst, "record": probe, "before": before, "check": compare(probe, order, today), "today": today}


async def record(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    row = p["record"]
    row.recorded_by = by
    row.created_at = utcnow()
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="mf_order_recorded", actor=by, content={
        "order_id": str(row.order_id), "rail": row.rail, "rta": row.rta, "folio": row.folio, "cutoff_rule": row.cutoff_rule, "order_on": row.order_on.isoformat(),
        "order_time": row.order_time, "nav_date_reported": row.nav_date_reported.isoformat(), "nav_date_by_rule": p["check"]["expected"], "state": p["check"]["state"],
        "document": row.source_document, "reason": row.reason})
    return await overview(session, firm, order_ids=[row.order_id])


async def overview(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None, order_ids: list[uuid.UUID] | None = None) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "orders": []}
    recs = await _latest(session, firm.id, order_ids)
    orders = {o.id: o for o in (await session.execute(select(Order).where(Order.firm_id == firm.id))).scalars().all()}
    accounts = {a.id: a for a in (await session.execute(select(Account).where(Account.firm_id == firm.id))).scalars().all()}
    insts = {i.id: i for i in (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()}
    homes: dict = {}
    if household_id is not None:
        from app.aurea_core.performance import household_account_ids

        keep = set(await household_account_ids(session, household_id, firm_id=firm.id) or [])
    else:
        keep = None
    rows = []
    for oid, rec in recs.items():
        o = orders.get(oid)
        if o is None or (keep is not None and o.account_id not in keep):
            continue
        inst, acc = insts.get(o.instrument_id), accounts.get(o.account_id)
        c = compare(rec, o, today)
        rows.append({"order_id": str(oid), "symbol": inst.symbol if inst else None, "fund": inst.name if inst else None, "account": acc.name if acc else None,
                     "side": c["side"], "amount": c["amount"], "rail": rec.rail, "rail_label": RAILS.get(rec.rail, rec.rail), "rta": rec.rta,
                     "rta_label": RTAS.get(rec.rta, rec.rta), "folio": rec.folio, "rule": rec.cutoff_rule, "rule_label": RULES.get(rec.cutoff_rule),
                     "order_on": rec.order_on.isoformat(), "order_time": rec.order_time, "funds_realised_on": rec.funds_realised_on.isoformat() if rec.funds_realised_on else None,
                     "nav_date": rec.nav_date_reported.isoformat(), "expected_nav_date": c["expected"], "state": c["state"], "gap_days": c["gap_days"],
                     "likely_cause": c["likely_cause"], "needs": c["needs"], "basis": c["basis"], "document": rec.source_document, "recorded_by": rec.recorded_by,
                     "amc": ((inst.values_tags or {}).get("amc") if inst else None)})
    rows.sort(key=lambda r: (r["state"] != "differs", r["order_on"]), reverse=False)
    folios: dict[tuple, dict] = {}
    for r in rows:
        if r["folio"]:
            folios[(r["account"], r["amc"], r["folio"])] = {"account": r["account"], "amc": r["amc"], "folio": r["folio"], "rta": r["rta_label"]}
    # Mutual fund orders the blotter holds that no one has recorded a rail for: counted, never guessed at.
    unrecorded = 0
    if order_ids is None:
        for oid, o in orders.items():
            inst = insts.get(o.instrument_id)
            if oid not in recs and is_fund(inst) and float(o.filled_quantity or 0) > 0 and (keep is None or o.account_id in keep):
                unrecorded += 1
    return {"applies": True, "as_of": today.isoformat(), "orders": rows, "breaks": sum(1 for r in rows if r["state"] == "differs"),
            "not_assessed": sum(1 for r in rows if r["state"] == "not_assessed"), "unrecorded": unrecorded, "folios": list(folios.values()),
            "rails": RAILS, "figures": law_figures.cited(list(FIGURES), today), "not_modelled": NOT_MODELLED, "said": SAID,
            "note": "A break is reported and nothing is repaired: no holding, unit or cash is written. An order with no record does not mean it was mishandled."}
