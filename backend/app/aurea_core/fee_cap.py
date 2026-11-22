"""An Investment Adviser's fee per family, against SEBI's cap (R5).

The IA Master Circular (6 Feb 2026) para 1(iii) caps what an adviser charges an individual or HUF who is not an
accredited investor, per family of client across all its services, a year: Rs 1,51,000 in fixed-fee mode, or 2.5% of
assets under advice in AUA mode, and after a change of mode the higher of the two. Both figures are read from
`law_figures` with their source. AUA is the value of securities in SEBI's purview, so cash and anything recorded outside
the managed book are left out of it.

This reads a billing preview and adds a block to each bill that would take a family over the cap for its financial year
(what is already billed in that year, net of credit notes, plus this bill, or the schedule's own yearly charge, whichever
is more), and to any bill for a family with no signed agreement on file (no fee before it, para 1(ii)(g)). A block on a
bill holds it at approval; nothing here lowers a fee, because what the client is charged is the agreement's, and a
person changes the agreement. A mandate held by a trust, company or other non-individual is outside the cap. The
household stands in for the family of client, and an accredited investor is not recorded here, so it is not exempted.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import law_figures, licences, markets, pre_advice
from app.models.billing import Invoice
from app.models.enums import EntityType
from app.models.graph import LegalEntity, Mandate

BILLED = ("issued", "debited", "credited")     # with the credit notes, which carry their own negative amount
CAPPED_ENTITIES = {EntityType.HUF.value}       # an individual's or an HUF's; a trust or company is bilateral


def _check(code: str, detail: str) -> dict:
    return {"code": code, "severity": "block", "detail": detail}


def cap_for(modes: set[str], aua: float, today: date) -> tuple[float, str]:
    """The family's yearly cap for the modes its schedules bill in, and how it was reached."""
    fixed = float(law_figures.value("in_ia_fee_fixed_cap", today))
    rate = float(law_figures.value("in_ia_fee_aua_cap", today))
    if modes == {"flat_fee"}:
        return fixed, "fixed-fee mode"
    if "flat_fee" not in modes:
        return rate * aua, f"AUA mode, {rate:.1%} of {markets.money(aua, 'INR')}"
    return max(fixed, rate * aua), "both modes: the higher of the two"


async def _capped(session: AsyncSession, mandate_ids: list[str]) -> set[str]:
    """The mandates the cap reaches: a person's, or an HUF's."""
    mandates = (await session.execute(select(Mandate).where(Mandate.id.in_([uuid.UUID(m) for m in mandate_ids])))).scalars().all()
    out = set()
    for m in mandates:
        if m.person_id:
            out.add(str(m.id))
        elif m.entity_id:
            entity = await session.get(LegalEntity, m.entity_id)
            if entity is not None and str(entity.entity_type) in CAPPED_ENTITIES:
                out.add(str(m.id))
    return out


async def apply(session: AsyncSession, firm, entries: list[dict], as_of: date) -> None:
    """Adds the cap and agreement blocks to an Indian adviser's bills, in place, with what each family is billed."""
    market = markets.get(getattr(firm, "jurisdiction", None))
    if market is None or market.code != "IN" or not licences.held(firm):
        return
    by_household: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        if e.get("household_id"):
            by_household[e["household_id"]].append(e)
    for household_id, rows in by_household.items():
        hh = uuid.UUID(household_id)
        adviser = {str(m.id) for m in await pre_advice.adviser_mandates(session, firm, hh)}
        rows = [e for e in rows if e["mandate_id"] in adviser]
        if not rows:
            continue
        if not await pre_advice.signed(session, firm, hh):
            for e in rows:
                e["checks"].append(_check("no_signed_agreement",
                                          "No fee before the client's signed agreement is on file "
                                          f"({pre_advice.CITATION}, para 1(ii)(g))."))
        capped = await _capped(session, [e["mandate_id"] for e in rows])
        rows = [e for e in rows if e["mandate_id"] in capped]
        if rows:
            year = markets.tax_year(market, date.fromisoformat(rows[0]["period_end"]))
            start, end = year["start"], year["end"]
            billed = float(sum(float(a) for (a,) in (await session.execute(select(Invoice.amount).where(
                Invoice.firm_id == firm.id, Invoice.household_id == hh, Invoice.status.in_(BILLED),
                Invoice.period_end >= start, Invoice.period_end <= end))).all()))
            this_bill = sum(e["amount"] for e in rows)
            yearly = sum(e["amount"] * e["periods_per_year"] / e["period_fraction"] for e in rows if e["period_fraction"])
            aua = sum(a["securities"] for e in rows for a in e["accounts"])
            modes = {(e.get("schedule") or {}).get("fee_type") for e in rows}
            cap, basis = cap_for(modes, aua, as_of)
            charged = max(billed + this_bill, yearly)
            view = {"cap": round(cap, 2), "basis": basis, "billed_this_year": round(billed, 2), "this_bill": round(this_bill, 2),
                    "yearly_charge": round(yearly, 2), "aua": round(aua, 2), "year": year["label"],
                    "over": charged > cap + 0.005, "stands_in": pre_advice.STANDS_IN,
                    "figures": law_figures.cited(["in_ia_fee_fixed_cap", "in_ia_fee_aua_cap"], as_of)}
            for e in rows:
                e["fee_cap"] = view
                if view["over"]:
                    e["checks"].append(_check("sebi_fee_cap",
                                              f"Charges {markets.money(charged, 'INR')} for {year['label']} against the family's cap of "
                                              f"{markets.money(cap, 'INR')} ({basis}; {pre_advice.CITATION}, para 1(iii))."))
        for e in by_household[household_id]:
            e["blocked"] = any(c["severity"] == "block" for c in e["checks"])
