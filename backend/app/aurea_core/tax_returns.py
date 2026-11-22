"""A filed US tax return's facts, recorded by a person from the document (B-183).

The Roth room, the harvesting findings and the withdrawal order read tax figures; until now those were whatever sat
on a profile, with no source and no year. A return here is what a person read off Form 1040 and its schedules: the
tax year, the filing status, who is on it, AGI, taxable income, the marginal and effective rate if they read them,
carryforwards, the QBI deduction, each person's Form 8606 basis, and the state. Each names the document.

  - It is a US return: a household taxed elsewhere records none here.
  - It is added and never edited. An amended return is recorded again; the latest recorded for the same people and
    year stands, and the latest year is the one a module reads, which names it.
  - Nothing is worked out from it beyond what the modules say they do; OCR of a return is not done.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.graph import Household, Person
from app.models.tax_return import TaxReturn
from app.models.tenant import Firm

FILING_STATUSES = {
    "single": "single",
    "married_filing_jointly": "married filing jointly",
    "married_filing_separately": "married filing separately",
    "head_of_household": "head of household",
    "qualifying_surviving_spouse": "qualifying surviving spouse",
}
CARRYFORWARDS = ("capital_loss", "charitable", "net_operating_loss", "investment_interest", "other")
STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
    "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR",
    "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}


class TaxReturnError(ValueError):
    pass


def _amount(v, label: str, *, required: bool = False, negative_ok: bool = False) -> float | None:
    if v in (None, ""):
        if required:
            raise TaxReturnError(f"Give the {label} as the return shows it.")
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise TaxReturnError(f"The {label} must be an amount.") from None
    if n < 0 and not negative_ok:
        raise TaxReturnError(f"The {label} cannot be below zero.")
    return round(n, 2)


def _rate(v, label: str) -> float | None:
    if v in (None, ""):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise TaxReturnError(f"The {label} must be a rate.") from None
    if n > 1:
        n = n / 100          # 24 means 24%
    if not 0 <= n <= 0.6:
        raise TaxReturnError(f"The {label} must be between 0% and 60%.")
    return round(n, 4)


async def prepare(session: AsyncSession, firm: Firm, household_id: uuid.UUID, *, person_ids: list | None, tax_year,
                  filing_status: str | None, agi, taxable_income, marginal_rate=None, effective_rate=None,
                  qbi_deduction=None, carryforwards: list | None = None, basis_8606: list | None = None,
                  state: str | None = None, source_document: str | None = None) -> dict:
    """Every fact checked, nothing written."""
    from app.aurea_core import firm_time, markets

    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm.id:
        raise TaxReturnError("That household is not one of this firm's.")
    market = markets.of_household(firm, household.values)
    if not market or market.code != "US":
        raise TaxReturnError("A return here is a US return (Form 1040); this household is not taxed in the US.")
    today = firm_time.today(firm)
    try:
        year = int(tax_year)
    except (TypeError, ValueError):
        raise TaxReturnError("Give the tax year the return is for.") from None
    if not 2000 <= year <= today.year:
        raise TaxReturnError(f"The tax year must be between 2000 and {today.year}.")
    if filing_status not in FILING_STATUSES:
        raise TaxReturnError(f"The filing status must be one of: {', '.join(FILING_STATUSES)}.")

    ids = []
    for raw in person_ids or []:
        try:
            ids.append(uuid.UUID(str(raw)))
        except ValueError:
            raise TaxReturnError("Name the people on the return by their ids, from read_household.") from None
    people = [p for p in (await session.execute(select(Person).where(Person.id.in_(ids)))).scalars().all()
              if p.household_id == household.id and p.firm_id == firm.id] if ids else []
    if not people or len(people) != len(set(ids)):
        raise TaxReturnError("Name the people on the return, each one of this household's.")
    joint = filing_status == "married_filing_jointly"
    if joint and len(people) != 2:
        raise TaxReturnError("A joint return names both spouses.")
    if not joint and len(people) != 1:
        raise TaxReturnError("A return that is not joint names one person.")

    carries = []
    for c in carryforwards or []:
        kind = str((c or {}).get("kind") or "").strip()
        if kind not in CARRYFORWARDS:
            raise TaxReturnError(f"A carryforward's kind must be one of: {', '.join(CARRYFORWARDS)}.")
        carries.append({"kind": kind, "amount": _amount(c.get("amount"), f"{kind.replace('_', ' ')} carryforward", required=True)})
    basis = {}
    on_return = {str(p.id) for p in people}
    for b in basis_8606 or []:
        pid = str((b or {}).get("person_id") or "")
        if pid not in on_return:
            raise TaxReturnError("Form 8606 basis belongs to a person on this return.")
        basis[pid] = _amount(b.get("amount"), "Form 8606 basis", required=True)
    st = (state or "").strip().upper() or None
    if st is not None and st not in STATES:
        raise TaxReturnError("The state is its two-letter code, as on the return.")
    document = (source_document or "").strip()
    if len(document) < 5:
        raise TaxReturnError("Say which document this comes from: the return, its year and who filed it.")
    if len(document) > 300:
        raise TaxReturnError("The document's name is at most 300 characters.")
    agi_v = _amount(agi, "AGI", required=True, negative_ok=True)
    taxable = _amount(taxable_income, "taxable income", required=True)
    return {
        "household": household, "people": people, "tax_year": year, "filing_status": filing_status,
        "agi": agi_v, "taxable_income": taxable, "marginal_rate": _rate(marginal_rate, "marginal rate"),
        "effective_rate": _rate(effective_rate, "effective rate"), "qbi_deduction": _amount(qbi_deduction, "QBI deduction"),
        "carryforwards": carries, "basis_8606": basis, "state": st, "document": document, "today": today,
    }


async def record(session: AsyncSession, firm: Firm, household_id: uuid.UUID, *, by: str, **facts) -> TaxReturn:
    from app.provenance import ledger

    ready = await prepare(session, firm, household_id, **facts)
    row = TaxReturn(
        firm_id=firm.id, household_id=household_id, person_ids=sorted(str(p.id) for p in ready["people"]),
        tax_year=ready["tax_year"], filing_status=ready["filing_status"], agi=ready["agi"],
        taxable_income=ready["taxable_income"], marginal_rate=ready["marginal_rate"], effective_rate=ready["effective_rate"],
        qbi_deduction=ready["qbi_deduction"], carryforwards=ready["carryforwards"], basis_8606=ready["basis_8606"],
        state=ready["state"], source_document=ready["document"], as_of=ready["today"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="tax_return_recorded", actor=by, content={
        "tax_return_id": str(row.id), "household_id": str(household_id), "tax_year": row.tax_year,
        "filing_status": row.filing_status, "people": row.person_ids, "document": row.source_document})
    return row


def _num(v) -> float | None:
    return float(v) if v is not None else None


def view(r: TaxReturn, names: dict[str, str]) -> dict:
    return {
        "id": str(r.id), "tax_year": r.tax_year, "filing_status": r.filing_status,
        "filing_label": FILING_STATUSES.get(r.filing_status, r.filing_status),
        "person_ids": list(r.person_ids or []), "people": [names.get(pid, "?") for pid in (r.person_ids or [])],
        "agi": _num(r.agi), "taxable_income": _num(r.taxable_income), "marginal_rate": _num(r.marginal_rate),
        "effective_rate": _num(r.effective_rate), "qbi_deduction": _num(r.qbi_deduction),
        "carryforwards": list(r.carryforwards or []), "basis_8606": dict(r.basis_8606 or {}),
        "basis_8606_by_name": {names.get(pid, "?"): amt for pid, amt in (r.basis_8606 or {}).items()},
        "state": r.state, "document": r.source_document, "as_of": r.as_of.isoformat() if r.as_of else None,
        "recorded_by": r.recorded_by,
    }


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> list[dict]:
    """Every return on file, newest year first; within a year, the latest recorded first."""
    rows = (await session.execute(select(TaxReturn).where(TaxReturn.household_id == household_id, TaxReturn.firm_id == firm_id)
                                  .order_by(TaxReturn.tax_year.desc(), TaxReturn.recorded_at.desc()))).scalars().all()
    if not rows:
        return []
    people = (await session.execute(select(Person).where(Person.household_id == household_id))).scalars().all()
    names = {str(p.id): p.preferred_name or p.full_name for p in people}
    return [view(r, names) for r in rows]


def standing(returns: list[dict]) -> list[dict]:
    """The return that stands for each set of people: the latest year, and within it the latest recorded."""
    out: dict[tuple, dict] = {}
    for r in returns:                      # already newest first
        key = tuple(sorted(r["person_ids"]))
        out.setdefault(key, r)
    return list(out.values())


def covering(brain: dict, person_id: str) -> dict | None:
    """The standing return a person is on, from the household brain (`tax_returns`), or None."""
    on = [r for r in (brain.get("tax_returns") or []) if person_id in r["person_ids"]]
    return max(on, key=lambda r: r["tax_year"]) if on else None
