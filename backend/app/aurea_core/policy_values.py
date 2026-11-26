"""An endowment, money-back, whole-life or unit-linked policy, laid out three ways: surrender, paid-up, continue
(B-397, L200-2 India section 6).

A person records what the insurer's statement says: premiums paid, surrender value, paid-up value, the fund value of a
unit-linked policy, the premiums still due and the maturity benefit, each only if the statement gives it. The statement
hangs off the life policy that holds the sum assured, so the cover is counted once, in the death gap, as any life cover.
Statements are added and never edited; the latest stands.

The layout is those figures side by side. The one figure worked from them is the yearly return that continuing would
imply against taking the surrender value today: only where the statement gives the surrender value, the premium, the years
left, the maturity benefit and its date, with premiums taken as paid yearly in advance, life cover and tax left out, and an
illustrated benefit marked as one. Anything missing reads "not on file". It recommends none of the three.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, health_cover, law_figures, protection
from app.models.graph import Household, Person
from app.models.health_cover import MATURITY_BASES, SAVINGS_PRODUCTS, PolicyValueStatement
from app.models.protection import ProtectionPolicy
from app.models.tenant import Firm
from app.provenance import ledger

READERS = health_cover.READERS
RECORDERS = health_cover.RECORDERS
PRODUCT_TEXT = {"endowment": "Endowment", "money_back": "Money-back", "whole_life": "Whole life", "unit_linked": "Unit-linked"}
LOCK_IN = "in_ulip_lock_in_years"
NOT_MODELLED = ["tax_on_surrender_or_maturity", "bonuses_not_yet_declared", "unit_linked_market_returns", "charges_beyond_those_stated",
                "cost_and_underwriting_of_replacement_cover", "life_cover_after_surrender_or_paid_up_beyond_the_statement", "policy_loan",
                "surrender_value_is_not_counted_in_net_worth"]


class PolicyValueError(ValueError):
    """This cannot be recorded, with the reason."""


def implied_return(*, surrender: float, premium: float, years_left: int, maturity: float, years: float) -> float | None:
    """The yearly rate at which today's surrender value and the premiums still due grow to the maturity benefit. Pure.
    Premiums are paid at the start of each remaining year. None where no rate between -99% and 100% does it."""
    if years <= 0 or years_left < 0 or years_left - 1 >= years or surrender < 0 or premium < 0 or maturity <= 0:
        return None
    if surrender == 0 and (premium == 0 or years_left == 0):
        return None
    grown = lambda r: surrender * (1 + r) ** years + sum(premium * (1 + r) ** (years - k) for k in range(years_left))  # noqa: E731
    low, high = -0.99, 1.0
    if not grown(low) <= maturity <= grown(high):
        return None
    for _ in range(80):
        middle = (low + high) / 2
        low, high = (middle, high) if grown(middle) < maturity else (low, middle)
    return round((low + high) / 2, 4)


def lay_out(statement: dict, sum_assured: float | None, today: date) -> dict:
    """Surrender, paid-up and continue as figures. Pure. A None is a figure the statement did not give."""
    paid, surrender, paid_up = statement["premiums_paid"], statement["surrender_value"], statement["paid_up_value"]
    premium, years_left, maturity = statement["annual_premium"], statement["premium_years_left"], statement["maturity_value"]
    ahead = 0.0 if years_left == 0 else (round(premium * years_left, 2) if premium is not None and years_left is not None else None)
    options = [
        {"key": "surrender", "label": "Surrender", "cash_now": surrender, "premiums_still_to_pay": 0.0, "stated_value": None,
         "stated_value_is": None, "life_cover": 0.0,
         "against_premiums_paid": round(surrender - paid, 2) if surrender is not None else None,
         "missing": [] if surrender is not None else ["surrender_value"]},
        {"key": "paid_up", "label": "Make paid-up", "cash_now": 0.0, "premiums_still_to_pay": 0.0, "stated_value": paid_up,
         "stated_value_is": "paid-up value", "life_cover": None, "against_premiums_paid": None,
         "missing": [] if paid_up is not None else ["paid_up_value"]},
        {"key": "continue", "label": "Continue", "cash_now": 0.0, "premiums_still_to_pay": ahead, "stated_value": maturity,
         "stated_value_is": f"maturity benefit, {statement['maturity_basis']}" if maturity is not None else "maturity benefit",
         "life_cover": sum_assured, "against_premiums_paid": None,
         "missing": [name for name, have in (("annual_premium", premium is not None or years_left == 0),
                                             ("premium_years_left", years_left is not None),
                                             ("maturity_value", maturity is not None)) if not have]},
    ]
    need = {"surrender_value": surrender, "annual_premium": premium if years_left != 0 else 0.0, "premium_years_left": years_left,
            "maturity_value": maturity, "maturity_on": statement["maturity_on"]}
    missing = [k for k, v in need.items() if v is None]
    rate = None
    if not missing:
        years = (date.fromisoformat(statement["maturity_on"]) - date.fromisoformat(statement["statement_date"])).days / 365.25
        rate = implied_return(surrender=surrender, premium=need["annual_premium"], years_left=years_left, maturity=maturity, years=years)
        if rate is None:
            missing = ["figures_that_give_one_rate"]
    lock = None
    if statement["product"] == "unit_linked":
        years = int(law_figures.value(LOCK_IN))
        if statement["commenced_on"] is None:
            lock = {"status": "not_assessed", "missing": ["commencement_date"], "years": years, "assumed": True}
        else:
            until = health_cover.add_months(date.fromisoformat(statement["commenced_on"]), 12 * years)
            lock = {"status": "locked" if until > today else "passed", "until": until.isoformat(), "years": years, "assumed": True}
    return {"options": options,
            "continuing_return": {"rate": rate, "missing": missing, "basis": statement["maturity_basis"],
                                  "assumes": "premiums yearly in advance; life cover and tax left out"},
            "premiums_by_the_end": round(paid + ahead, 2) if ahead is not None else None,
            "lock_in": lock}


def _num(value: Any) -> float | None:
    return float(value) if value is not None else None


def _statement(r: PolicyValueStatement) -> dict:
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {"id": str(r.id), "product": r.product, "statement_date": r.statement_date.isoformat(), "commenced_on": iso(r.commenced_on),
            "premiums_paid": float(r.premiums_paid), "annual_premium": _num(r.annual_premium), "premium_years_left": r.premium_years_left,
            "surrender_value": _num(r.surrender_value), "paid_up_value": _num(r.paid_up_value), "fund_value": _num(r.fund_value),
            "maturity_on": iso(r.maturity_on), "maturity_value": _num(r.maturity_value), "maturity_basis": r.maturity_basis,
            "charges_stated": r.charges_stated, "source_document": r.source_document, "recorded_by": r.recorded_by}


async def standing(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID | None = None) -> dict[uuid.UUID, list]:
    """Each policy's statements, oldest first, so the last is the one that stands."""
    query = select(PolicyValueStatement).where(PolicyValueStatement.firm_id == firm_id)
    if household_id is not None:
        query = query.where(PolicyValueStatement.household_id == household_id)
    out: dict[uuid.UUID, list] = {}
    for r in (await session.execute(query.order_by(PolicyValueStatement.statement_date, PolicyValueStatement.created_at,
                                                   PolicyValueStatement.id))).scalars().all():
        out.setdefault(r.policy_id, []).append(r)
    return out


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID,
                        today: date | None = None) -> dict | None:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    firm = await session.get(Firm, firm_id)
    today = today or firm_time.today(firm)
    base = {"household_id": str(household_id), "household": household.name, "currency": firm.base_currency}
    if not health_cover.applies(firm, household.values):
        return {**base, "applies": False, "policies": [],
                "why": "The surrender and paid-up layout is for an Indian household."}
    policies = []
    for policy_id, rows in (await standing(session, firm_id, household_id)).items():
        policy = await session.get(ProtectionPolicy, policy_id)
        if policy is None or policy.firm_id != firm_id:
            continue
        person = await session.get(Person, policy.person_id)
        latest = _statement(rows[-1])
        ended = policy.status != "in_force"
        sum_assured = _num(policy.sum_insured)
        policies.append({
            "policy_id": str(policy.id), "person_id": str(policy.person_id), "person": person.full_name if person else None,
            "household": household.name, "household_id": str(household_id), "provider": policy.provider, "policy_number": policy.policy_number,
            "product": latest["product"], "product_label": PRODUCT_TEXT[latest["product"]], "sum_assured": sum_assured,
            "status": "ended" if ended else "in_force", "ended_reason": policy.ended_reason,
            "statement": latest, "earlier_statements": len(rows) - 1,
            **(lay_out(latest, sum_assured, today) if not ended else {"options": [], "continuing_return": None,
                                                                     "premiums_by_the_end": None, "lock_in": None}),
        })
    policies.sort(key=lambda p: (p["status"] != "in_force", p["person"] or "", p["provider"]))
    live = [p for p in policies if p["status"] == "in_force"]
    figures = [LOCK_IN] if any(p["product"] == "unit_linked" for p in live) else []
    return {**base, "applies": True, "as_of": today.isoformat(), "policies": policies,
            "summary": {"policies": len(live), "premiums_paid": round(sum(p["statement"]["premiums_paid"] for p in live), 2),
                        "surrender_value": round(sum(p["statement"]["surrender_value"] or 0 for p in live), 2),
                        "without_surrender_value": sum(1 for p in live if p["statement"]["surrender_value"] is None),
                        "sum_assured": round(sum(p["sum_assured"] or 0 for p in live), 2)},
            "law": law_figures.cited(figures, today), "not_modelled": NOT_MODELLED, "recommends": "nothing"}


async def for_firm(session: AsyncSession, firm_id: uuid.UUID, *, today: date | None = None) -> dict:
    firm = await session.get(Firm, firm_id)
    today = today or firm_time.today(firm)
    homes = sorted({r.household_id for rows in (await standing(session, firm_id)).values() for r in rows}, key=str)
    views = [v for h in homes if (v := await for_household(session, h, firm_id=firm_id, today=today)) and v["policies"]]
    views.sort(key=lambda v: v["household"])
    policies = [p for v in views for p in v["policies"]]
    live = [p for p in policies if p["status"] == "in_force"]
    return {"applies": health_cover.applies(firm) or bool(views), "currency": firm.base_currency, "as_of": today.isoformat(),
            "policies": policies,
            "summary": {"households": len(views), "policies": len(live),
                        "premiums_paid": round(sum(p["statement"]["premiums_paid"] for p in live), 2),
                        "surrender_value": round(sum(p["statement"]["surrender_value"] or 0 for p in live), 2),
                        "sum_assured": round(sum(p["sum_assured"] or 0 for p in live), 2),
                        "without_surrender_value": sum(1 for p in live if p["statement"]["surrender_value"] is None)},
            "law": law_figures.cited([LOCK_IN] if any(p["product"] == "unit_linked" for p in live) else [], today),
            "not_modelled": NOT_MODELLED, "recommends": "nothing"}


def _amount(value: Any, label: str, *, required: bool = False) -> float | None:
    if value in (None, ""):
        if required:
            raise PolicyValueError(f"{label} is needed, as the statement gives it.")
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise PolicyValueError(f"{label} must be a number.")
    if amount < 0:
        raise PolicyValueError(f"{label} cannot be negative.")
    return amount


def _date(value: Any, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise PolicyValueError(f"{label} must be a date, YYYY-MM-DD.")


async def prepare_statement(
    session: AsyncSession, firm_id: uuid.UUID, *, role: Any, product: Any, statement_date: Any, premiums_paid: Any,
    source_document: Any, policy_id: Any = None, person_id: Any = None, provider: Any = None, sum_assured: Any = None,
    policy_number: Any = None, commenced_on: Any = None, annual_premium: Any = None, premium_years_left: Any = None,
    surrender_value: Any = None, paid_up_value: Any = None, fund_value: Any = None, maturity_on: Any = None,
    maturity_value: Any = None, maturity_basis: Any = None, charges_stated: Any = None, today: date | None = None,
) -> dict:
    """Everything recording a statement would refuse, refused without writing, and what would be written. With no
    `policy_id` the life policy itself is recorded too, from `person_id`, `provider` and `sum_assured`."""
    if str(role) not in RECORDERS:
        raise PolicyValueError("A policy statement is recorded by an adviser, a paraplanner or operations, from the insurer's statement.")
    firm = await session.get(Firm, firm_id)
    today = today or firm_time.today(firm)
    kind = str(product or "").strip().lower().replace("-", "_").replace("ulip", "unit_linked")
    if kind not in SAVINGS_PRODUCTS:
        raise PolicyValueError(f"The product must be one of {', '.join(SAVINGS_PRODUCTS)}.")
    document = str(source_document or "").strip()
    if len(document) < 5:
        raise PolicyValueError("Say which statement this comes from: the insurer, its date and what it is.")
    dated = _date(statement_date, "The statement date")
    if dated is None:
        raise PolicyValueError("Give the date of the statement.")
    if dated > today:
        raise PolicyValueError("The statement date is in the future.")
    maturity_day = _date(maturity_on, "The maturity date")
    if maturity_day is not None and maturity_day <= dated:
        raise PolicyValueError("The maturity date is not after the statement date: a matured policy is ended, not valued.")
    began = _date(commenced_on, "The commencement date")
    if began is not None and began > dated:
        raise PolicyValueError("The commencement date is after the statement date.")
    paid = _amount(premiums_paid, "The premiums paid", required=True)
    surrender = _amount(surrender_value, "The surrender value")
    paid_up = _amount(paid_up_value, "The paid-up value")
    fund = _amount(fund_value, "The fund value")
    if fund is not None and kind != "unit_linked":
        raise PolicyValueError("Only a unit-linked policy has a fund value.")
    if surrender is None and paid_up is None and fund is None:
        raise PolicyValueError("Give at least one of the surrender value, the paid-up value or the fund value the statement states.")
    maturity = _amount(maturity_value, "The maturity benefit")
    basis = str(maturity_basis or "").strip().lower() or None
    if maturity is not None and basis not in MATURITY_BASES:
        raise PolicyValueError("Say whether the statement gives the maturity benefit as guaranteed or illustrated.")
    left = None
    if premium_years_left not in (None, ""):
        try:
            left = int(premium_years_left)
        except (TypeError, ValueError):
            raise PolicyValueError("The premium years left must be a whole number.")
        if not 0 <= left <= 80:
            raise PolicyValueError("The premium years left must be between 0 and 80.")

    policy, new_policy = None, None
    if policy_id not in (None, ""):
        try:
            policy = await session.get(ProtectionPolicy, uuid.UUID(str(policy_id)))
        except (TypeError, ValueError):
            raise PolicyValueError("policy_id must be an id from a read tool.")
        if policy is None or policy.firm_id != firm_id:
            raise PolicyValueError("That policy was not found.")
        if policy.kind != "life" or policy.rider_of is not None or policy.group_cover:
            raise PolicyValueError("A statement of values attaches to a person's own life policy, not a rider, group cover or another kind.")
        if policy.status != "in_force":
            raise PolicyValueError("This policy has ended.")
        person = await session.get(Person, policy.person_id)
    else:
        try:
            pid = uuid.UUID(str(person_id))
        except (TypeError, ValueError):
            raise PolicyValueError("Give the life policy's policy_id, or the insured person_id, provider and sum_assured to record it.")
        cover = _amount(sum_assured, "The sum assured", required=True)
        try:
            new_policy = {"person_id": pid, "kind": "life", "provider": str(provider or ""), "source_document": document,
                          "sum_insured": cover, "annual_premium": _amount(annual_premium, "The annual premium"),
                          "expires_on": maturity_day}
            ready = await protection.prepare_policy(session, firm_id, today=today, **new_policy)
        except protection.ProtectionError as exc:
            raise PolicyValueError(str(exc))
        person = ready["person"]
        existing = (await session.execute(select(ProtectionPolicy).where(
            ProtectionPolicy.firm_id == firm_id, ProtectionPolicy.person_id == pid, ProtectionPolicy.kind == "life",
            ProtectionPolicy.status == "in_force"))).scalars().all()
        number = str(policy_number or "").strip()[:64] or None
        for row in existing:
            if row.provider.strip().lower() == ready["provider"].lower() and (not number or not row.policy_number or row.policy_number == number):
                raise PolicyValueError(f"A life policy with {row.provider} for {person.full_name} is already on file. "
                                       f"Give its policy_id ({row.id}) so the cover is not counted twice.")
        new_policy["policy_number"] = number
    household = await session.get(Household, person.household_id) if person and person.household_id else None
    if household is None or not health_cover.applies(firm, household.values):
        raise PolicyValueError("The surrender and paid-up layout is for an Indian household.")
    standing_rows = (await standing(session, firm_id, household.id)).get(policy.id, []) if policy is not None else []
    latest = standing_rows[-1] if standing_rows else None
    values = {"product": kind, "statement_date": dated, "commenced_on": began, "premiums_paid": paid,
              "annual_premium": _amount(annual_premium, "The annual premium"), "premium_years_left": left, "surrender_value": surrender,
              "paid_up_value": paid_up, "fund_value": fund, "maturity_on": maturity_day, "maturity_value": maturity,
              "maturity_basis": basis if maturity is not None else None, "charges_stated": str(charges_stated or "").strip()[:300] or None,
              "source_document": document[:255]}
    as_text = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in values.items()}
    sum_now = new_policy["sum_insured"] if new_policy else _num(policy.sum_insured)
    return {"person": person, "household": household, "policy": policy, "new_policy": new_policy, "values": values,
            "sum_assured": sum_now, "provider": policy.provider if policy else new_policy["provider"].strip()[:120],
            "layout": lay_out(as_text, sum_now, today),
            "stands": latest is None or dated >= latest.statement_date,
            "previous": latest}


async def record_statement(session: AsyncSession, firm_id: uuid.UUID, *, recorded_by: str, **terms) -> dict:
    ready = await prepare_statement(session, firm_id, **terms)
    firm = await session.get(Firm, firm_id)
    policy_id = ready["policy"].id if ready["policy"] else None
    if policy_id is None:
        made = await protection.record_policy(session, firm_id, recorded_by=recorded_by, today=terms.get("today"), **ready["new_policy"])
        policy_id = uuid.UUID(made["policy_id"])
    row = PolicyValueStatement(firm_id=firm_id, household_id=ready["household"].id, policy_id=policy_id, currency=firm.base_currency,
                               recorded_by=recorded_by, **ready["values"])
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="policy_values_recorded", actor=recorded_by, content={
        "statement_id": str(row.id), "policy_id": str(policy_id), "statement_date": row.statement_date.isoformat(),
        "document": row.source_document})
    return {"statement_id": str(row.id), "policy_id": str(policy_id), "household_id": str(ready["household"].id),
            "household": ready["household"].name, "person": ready["person"].full_name, "provider": ready["provider"],
            "product": row.product, "product_label": PRODUCT_TEXT[row.product], "sum_assured": ready["sum_assured"],
            "statement": _statement(row), "policy_recorded": ready["new_policy"] is not None, "stands": ready["stands"],
            **ready["layout"], "not_modelled": NOT_MODELLED, "recommends": "nothing", "currency": firm.base_currency,
            "law": law_figures.cited([LOCK_IN] if row.product == "unit_linked" else [], firm_time.today(firm))}
