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
family of client is what a person recorded (sebi_family.py; the household stands in for it where none is, and says so).

An accredited investor is outside the cap too, but only as a person recorded the certificate (accredited.py, B-291): that person's own
mandates are left out of every sum and each bill says so; a lapsed or withdrawn certificate puts them back inside. The adviser's
fee terms since 2025 (adviser_fees.py, B-292) sit beside it, each from a document: an invoice period longer than a year, or than the
months the agreement allows in advance, is held; the mode the adviser elected is shown, and a switch within the financial year
lifts the cap to the higher of the two modes; a second-opinion fee on assets held under a distribution arrangement counts toward what
the family is charged. Nothing here claims the adviser complies.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import accredited, adviser_fees, firm_time, law_figures, licences, markets, pre_advice
from app.models.billing import Invoice
from app.models.enums import EntityType
from app.models.graph import LegalEntity, Mandate

BILLED = ("issued", "debited", "credited")     # with the credit notes, which carry their own negative amount
CAPPED_ENTITIES = {EntityType.HUF.value}       # an individual's or an HUF's; a trust or company is bilateral


def _check(code: str, detail: str, severity: str = "block") -> dict:
    return {"code": code, "severity": severity, "detail": detail}


def cap_for(modes: set[str], aua: float, today: date, *, switched_on: date | None = None) -> tuple[float, str]:
    """The family's yearly cap for the modes its schedules bill in, and how it was reached. After a recorded switch of mode within the
    year it is the higher of the two, as the circular has it."""
    fixed = float(law_figures.value("in_ia_fee_fixed_cap", today))
    rate = float(law_figures.value("in_ia_fee_aua_cap", today))
    if switched_on is not None:
        return max(fixed, rate * aua), f"after a switch of mode on {switched_on.isoformat()}: the higher of the two"
    if modes == {"flat_fee"}:
        return fixed, "fixed-fee mode"
    if "flat_fee" not in modes:
        return rate * aua, f"AUA mode, {rate:.1%} of {markets.money(aua, 'INR')}"
    return max(fixed, rate * aua), "both modes: the higher of the two"


async def _capped(session: AsyncSession, mandate_ids: list[str], exempt_people: set | None = None) -> tuple[dict[str, uuid.UUID | None], dict[str, uuid.UUID]]:
    """The mandates the cap reaches (a person's, or an HUF's), each with its person owner (None for an HUF), and apart from them the
    mandates of a person whose accredited-investor certificate stands, which it does not reach."""
    mandates = (await session.execute(select(Mandate).where(Mandate.id.in_([uuid.UUID(m) for m in mandate_ids])))).scalars().all()
    out: dict[str, uuid.UUID | None] = {}
    exempt: dict[str, uuid.UUID] = {}
    for m in mandates:
        if m.person_id and m.person_id in (exempt_people or set()):
            exempt[str(m.id)] = m.person_id
        elif m.person_id:
            out[str(m.id)] = m.person_id
        elif m.entity_id:
            entity = await session.get(LegalEntity, m.entity_id)
            if entity is not None and str(entity.entity_type) in CAPPED_ENTITIES:
                out[str(m.id)] = None
    return out, exempt


async def apply(session: AsyncSession, firm, entries: list[dict], as_of: date) -> None:
    """Adds the cap and agreement blocks to an Indian adviser's bills, in place, with what each family is billed.

    A family is SEBI's family of client as a person recorded it (`sebi_family`), which can split a household or join people across
    two; with none recorded the household stands in for it and the view says so."""
    market = markets.get(getattr(firm, "jurisdiction", None))
    if market is None or market.code != "IN" or not licences.held(firm):
        return
    from app.aurea_core import sebi_family

    fams = await sebi_family.load(session, firm)
    today = firm_time.today(firm)
    accredited_people = await accredited.accredited_persons(session, firm, today)
    by_household: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        if e.get("household_id"):
            by_household[e["household_id"]].append(e)
    by_family: dict[str, list[dict]] = defaultdict(list)
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
        capped, exempt = await _capped(session, [e["mandate_id"] for e in rows], accredited_people)
        # An advance fee is held if its period runs past what the law, or the agreement as recorded, allows (B-292): every adviser bill.
        terms = await adviser_fees.household_terms(session, firm, hh)
        for e in rows:
            blocked = adviser_fees.advance_check(e, terms, as_of)
            if blocked:
                e["checks"].append(_check("advance_fee_period", blocked))
        for e in rows:
            if e["mandate_id"] in exempt:
                e["fee_cap"] = {"exempt": True, "reason": "accredited_investor", "year": None, "over": False, "cap": None, "basis": "accredited investor: not capped",
                                "said": "Accredited investor: the Investment Adviser fee cap does not reach this client (certificate recorded). "
                                        "A lapsed or withdrawn certificate puts the cap back.",
                                "fee_terms": adviser_fees.terms_view(terms), "figures": law_figures.cited(adviser_fees.LAW_KEYS, as_of)}
            elif e["mandate_id"] in capped:
                by_family[fams.key_of(capped[e["mandate_id"]], hh)].append(e)
    for key, rows in by_family.items():
        family = fams.keyed(key)
        recorded = family is not None and family.basis == "recorded"
        year = markets.tax_year(market, date.fromisoformat(rows[0]["period_end"]))
        start, end = year["start"], year["end"]
        if recorded:
            scope = [Invoice.mandate_id.in_(fams.mandates.get(key) or [uuid.uuid4()])]
        else:
            scope = [Invoice.household_id == uuid.UUID(rows[0]["household_id"])]
        billed = float(sum(float(a) for (a,) in (await session.execute(select(Invoice.amount).where(
            Invoice.firm_id == firm.id, *scope, Invoice.status.in_(BILLED),
            Invoice.period_end >= start, Invoice.period_end <= end))).all()))
        this_bill = sum(e["amount"] for e in rows)
        yearly = sum(e["amount"] * e["periods_per_year"] / e["period_fraction"] for e in rows if e["period_fraction"])
        aua = sum(a["securities"] for e in rows for a in e["accounts"])
        modes = {(e.get("schedule") or {}).get("fee_type") for e in rows}
        household_ids = sorted({uuid.UUID(e["household_id"]) for e in rows})
        fee_terms = await adviser_fees.family_terms(session, firm, household_ids, start, end)
        cap, basis = cap_for(modes, aua, as_of, switched_on=fee_terms["switched_on"])
        second = fee_terms["second_opinion_fee"]
        charged = max(billed + this_bill, yearly) + second
        view = {"cap": round(cap, 2), "basis": basis, "billed_this_year": round(billed, 2), "this_bill": round(this_bill, 2),
                "yearly_charge": round(yearly, 2), "aua": round(aua, 2), "year": year["label"],
                "over": charged > cap + 0.005, "second_opinion_fee": round(second, 2), "fee_terms": adviser_fees.terms_view(fee_terms, start, end),
                "stands_in": None if recorded else pre_advice.STANDS_IN,
                "family": {"basis": "recorded" if recorded else "household",
                           "members": [m["name"] for m in family.members] if family else []},
                "figures": law_figures.cited(["in_ia_fee_fixed_cap", "in_ia_fee_aua_cap", *adviser_fees.LAW_KEYS], as_of)}
        elected = fee_terms["mode"]
        billed_mode = "fixed" if modes == {"flat_fee"} else "aua"
        for e in rows:
            e["fee_cap"] = view
            if elected is not None and elected.mode != billed_mode:
                e["checks"].append(_check("fee_mode_differs", f"The family's recorded election is {adviser_fees.MODES[elected.mode]} mode; this bill is "
                                          f"worked in {adviser_fees.MODES[billed_mode]} mode. A person confirms which the agreement carries.", "warn"))
            if view["over"]:
                e["checks"].append(_check("sebi_fee_cap",
                                          f"Charges {markets.money(charged, 'INR')} for {year['label']} against the family's cap of "
                                          f"{markets.money(cap, 'INR')} ({basis}; {pre_advice.CITATION}, para 1(iii))."))
    for rows in by_household.values():
        for e in rows:
            e["blocked"] = any(c["severity"] == "block" for c in e["checks"])
