"""Tax documents: what a year's activity should produce, whether it has arrived, and the figures behind it (L100 §5.9).

The custodian or fund issues the official form, and this is never one. It is a summary from the book for the
client's accountant, and it says so, and a tracker of what is expected. A document is expected only where the book
shows a reason: a taxable account with activity, a retirement account that paid out, a private fund held in the year.
A person records one as received, from having it. No document on file does not mean it was not issued.

Realised gains come from the lots each sale relieved, split at a year's holding, which is how the US reads them.
Elsewhere the split is shown and the tax on it is the tax engine's. A sale with no lots on its ledger line is counted
in proceeds and named, because its gain cannot be known.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, markets
from app.aurea_core.billing import _mandate_households
from app.models.enums import MarketType
from app.models.graph import Account, Household, Mandate
from app.models.portfolio import Holding, Instrument, Transaction
from app.models.tax_documents import TaxDocument
from app.models.tenant import Firm
from app.provenance import ledger

RETIREMENT = {"traditional_ira", "roth_ira", "employer_rollover", "inherited_ira"}
# market -> (taxable statement, retirement distribution, private fund), each (kind, label).
FORMS = {
    "US": (("1099_composite", "Consolidated 1099"), ("1099_r", "1099-R"), ("k1", "Schedule K-1")),
    "UK": (("tax_certificate", "Consolidated tax certificate"), ("pension_statement", "Pension statement"), ("fund_statement", "Fund tax statement")),
    "IN": (("capital_gains_statement", "Capital gains statement"), ("capital_gains_statement", "Capital gains statement"), ("fund_statement", "Fund tax statement")),
}
DEFAULT_FORMS = (("annual_tax_statement", "Annual tax statement"), ("annual_tax_statement", "Annual tax statement"), ("fund_statement", "Fund tax statement"))
# When a US form is due, as (month, day) after the year ends. A K-1 comes late, which is why it is chased.
DUE = {"k1": (3, 15), "1099_composite": (2, 15), "1099_r": (1, 31)}
DAYS_AFTER_YEAR_END = 90    # elsewhere, the point at which a person should go and ask


class TaxDocumentError(ValueError):
    """This cannot be recorded, with the reason."""


def _gain_lines(t: Transaction) -> tuple[float, float, float, bool]:
    """Proceeds, gain held a year or less, gain held longer, and whether the lots were there to tell."""
    lineage = t.lineage or {}
    lots = lineage.get("lots") or []
    proceeds = float(t.amount or 0)
    if not lots:
        return proceeds, 0.0, 0.0, False
    short = long = 0.0
    price, fees = float(t.price or 0), float(lineage.get("fees") or 0)
    units = sum(float(x["quantity"]) for x in lots) or 1.0
    for lot in lots:
        gain = float(lot["quantity"]) * price - float(lot["cost"]) - fees * float(lot["quantity"]) / units
        if (t.trade_date - date.fromisoformat(lot["acquired_on"])).days > 365:
            long += gain
        else:
            short += gain
    return proceeds, short, long, True


async def _accounts(session: AsyncSession, firm_id: uuid.UUID) -> tuple[dict, dict]:
    mandates = (await session.execute(select(Mandate).where(Mandate.firm_id == firm_id))).scalars().all()
    owners = await _mandate_households(session, firm_id, list(mandates))
    accounts = (await session.execute(select(Account).where(Account.firm_id == firm_id))).scalars().all()
    return {a.id: a for a in accounts if a.mandate_id in owners}, owners


def _span(market, year: int) -> dict:
    """The tax year that began in `year`, on the market's calendar. Where there is no market, the calendar's own."""
    if market:
        return markets.tax_year(market, date(year, *market.tax_year_starts))
    return {"label": str(year), "start": date(year, 1, 1), "end": date(year, 12, 31)}


def _due(kind: str, span: dict) -> date:
    if kind in DUE:
        return date(span["end"].year + 1, *DUE[kind])
    return span["end"] + timedelta(days=DAYS_AFTER_YEAR_END)


async def household_year(session: AsyncSession, firm: Firm, household: Household, year: int, today: date, accounts: list[Account], received: list[TaxDocument]) -> dict:
    market = markets.of_household(firm, household.values)
    taxable, paid_out, private = FORMS.get(market.code if market else "", DEFAULT_FORMS)
    ids = [a.id for a in accounts]
    span = _span(market, year)
    txns = (await session.execute(select(Transaction).where(Transaction.account_id.in_(ids), Transaction.trade_date >= span["start"], Transaction.trade_date <= span["end"]))).scalars().all() if ids else []
    by_account: dict = defaultdict(lambda: {"proceeds": 0.0, "short": 0.0, "long": 0.0, "dividends": 0.0, "distributions": 0.0, "fees": 0.0, "withdrawals": 0.0, "no_lots": 0, "trades": 0})
    for t in txns:
        row, kind = by_account[t.account_id], str(t.txn_type)
        if kind == "sell":
            proceeds, short, long, known = _gain_lines(t)
            row["proceeds"] += proceeds
            row["short"] += short
            row["long"] += long
            row["no_lots"] += 0 if known else 1
            row["trades"] += 1
        elif kind == "dividend":
            row["dividends"] += float(t.amount or 0)
        elif kind == "distribution":
            row["distributions"] += float(t.amount or 0)
        elif kind == "fee":
            row["fees"] += float(t.amount or 0)
        elif kind == "fee_credit":
            row["fees"] -= float(t.amount or 0)
        elif kind == "withdrawal":
            row["withdrawals"] += float(t.amount or 0)
    summaries, expected = [], []

    def expect(kind: str, label: str, issuer: str) -> None:
        # With no market there are no rules to say what a year should produce.
        if market is None:
            return
        got = next((d for d in received if d.kind == kind and d.issuer == issuer and d.tax_year == year), None)
        due = _due(kind, span)
        expected.append({"kind": kind, "label": label, "issuer": issuer, "due": due.isoformat(), "status": "received" if got else "overdue" if today > due else "awaited",
                         "received_on": got.received_on.isoformat() if got else None, "document": got.source_document if got else None})

    for a in accounts:
        row = by_account.get(a.id)
        if row is None:
            continue
        summaries.append({"account_id": str(a.id), "account": a.name, "registration_type": a.registration_type, **{k: round(v, 2) if isinstance(v, float) else v for k, v in row.items()}})
        if a.registration_type in RETIREMENT:
            if row["withdrawals"] > 0:
                expect(paid_out[0], paid_out[1], a.name)
        elif row["trades"] or row["dividends"] or row["distributions"]:
            expect(taxable[0], taxable[1], a.name)
    if ids:
        funds = (await session.execute(select(Instrument.name).join(Holding, Holding.instrument_id == Instrument.id).where(Holding.account_id.in_(ids), Instrument.market_type == MarketType.PRIVATE).distinct())).scalars().all()
        for fund in sorted(funds):
            expect(private[0], private[1], fund)
    return {"household_id": str(household.id), "household": household.name, "tax_year": year, "tax_year_label": span["label"], "summaries": summaries, "expected": expected,
            "received": sum(1 for e in expected if e["status"] == "received"), "awaited": sum(1 for e in expected if e["status"] == "awaited"), "overdue": sum(1 for e in expected if e["status"] == "overdue"),
            "realised": {"short": round(sum(s["short"] for s in summaries), 2), "long": round(sum(s["long"] for s in summaries), 2), "proceeds": round(sum(s["proceeds"] for s in summaries), 2)}}


def _last_year(market, today: date) -> int:
    """The last tax year to have ended."""
    return (markets.tax_year(market, today)["start"].year if market else today.year) - 1


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, tax_year: int | None = None) -> dict | None:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    year = tax_year or _last_year(markets.of_household(firm, household.values), today)
    accounts, owners = await _accounts(session, firm_id)
    mine = [a for a in accounts.values() if owners.get(a.mandate_id) == household_id]
    received = (await session.execute(select(TaxDocument).where(TaxDocument.household_id == household_id))).scalars().all()
    return {**await household_year(session, firm, household, year, today, mine, received), "as_of": today.isoformat(), "not_the_official_form": True}


async def for_firm(session: AsyncSession, firm_id: uuid.UUID, *, tax_year: int | None = None) -> dict:
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    market = markets.for_firm(firm)
    year = tax_year or _last_year(market, today)
    accounts, owners = await _accounts(session, firm_id)
    households = (await session.execute(select(Household).where(Household.firm_id == firm_id, Household.left_on.is_(None)).order_by(Household.name))).scalars().all()
    received = defaultdict(list)
    for d in (await session.execute(select(TaxDocument).where(TaxDocument.firm_id == firm_id))).scalars().all():
        received[d.household_id].append(d)
    rows = []
    for h in households:
        mine = [a for a in accounts.values() if owners.get(a.mandate_id) == h.id]
        view = await household_year(session, firm, h, year, today, mine, received[h.id])
        if view["expected"]:
            rows.append(view)
    return {"households": rows, "tax_year": year, "tax_year_label": _span(market, year)["label"], "as_of": today.isoformat(), "not_the_official_form": True, "summary": {
        "expected": sum(len(r["expected"]) for r in rows), "received": sum(r["received"] for r in rows), "awaited": sum(r["awaited"] for r in rows), "overdue": sum(r["overdue"] for r in rows)}}


async def prepare_received(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, kind: str | None, tax_year, issuer: str | None, source_document: str | None) -> dict:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise TaxDocumentError("Household not found.")
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    try:
        year = int(tax_year)
    except (TypeError, ValueError):
        raise TaxDocumentError("Give the tax year.")
    market = markets.of_household(firm, household.values)
    if year > _last_year(market, today) + 1:
        raise TaxDocumentError("That tax year has not happened.")
    if not (kind or "").strip():
        raise TaxDocumentError("Say which document this is.")
    who, named = (issuer or "").strip(), (source_document or "").strip()
    if len(who) < 2:
        raise TaxDocumentError("Give the account or fund it is for.")
    if len(named) < 5:
        raise TaxDocumentError("Say where it is filed.")
    view = await for_household(session, household_id, firm_id=firm_id, tax_year=year)
    match = next((e for e in view["expected"] if e["kind"] == kind and e["issuer"] == who), None)
    if match is not None and match["status"] == "received":
        raise TaxDocumentError("This document is already recorded.")
    return {"household": household, "kind": kind.strip(), "year": year, "issuer": who, "document": named, "expected": match is not None, "label": match["label"] if match else kind, "year_label": _span(market, year)["label"]}


async def record_received(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, **terms) -> TaxDocument:
    ready = await prepare_received(session, household_id, firm_id=firm_id, **terms)
    today = firm_time.today(await session.get(Firm, firm_id))
    row = TaxDocument(firm_id=firm_id, household_id=household_id, kind=ready["kind"], tax_year=ready["year"], issuer=ready["issuer"], received_on=today, source_document=ready["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="tax_document_received", actor=by, content={"household_id": str(household_id), "kind": row.kind, "tax_year": row.tax_year, "issuer": row.issuer})
    return row
