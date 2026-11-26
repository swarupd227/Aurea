"""What the Indian regime comparison reads about a person, as a person recorded it (B-393).

Salary, the old regime's deductions, the regime the person is taxed under and their slab rate sit on the person's tax
profile, where `tax_india` and `tax_india_full` already read them. An adviser or a paraplanner records them from a document;
each keeps the day it was recorded, and the ledger keeps what it replaced. Nothing is worked out: a salary is not read from
an income, a deduction is not read from a policy, and a figure not given stays as it was. Total income is its own record
(`protection.record_income`), and a salary or a deduction above the income on file is refused rather than reconciled here.
An HUF's or a trust's regime is not recorded here: the comparison is for a person.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.aurea_core import firm_time, law_figures, markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Household, Person
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER})
REGIMES = {"new": "New regime", "old": "Old regime"}
FIELDS = ("salary_income", "old_regime_deductions", "regime", "marginal_rate")
LABELS = {"salary_income": "Salary a year", "old_regime_deductions": "Old-regime deductions", "regime": "Tax regime",
          "marginal_rate": "Slab rate"}


class TaxInputError(ValueError):
    """This cannot be recorded, with the reason."""


def slab_rates(regime: str) -> list[float]:
    """The rates the regime's bands carry, from `law_figures`: a slab rate is before surcharge and cess."""
    bands = law_figures.bands("in_new_regime_bands" if regime == "new" else "in_old_regime_bands")
    return sorted({float(rate) for _, rate in bands})


def on_file(tax: dict | None) -> dict:
    """What stands on a tax profile, for showing beside the comparison. None is "not on file"."""
    tax = tax or {}
    source = tax.get("tax_inputs_source") or {}
    return {**{k: tax.get(k) for k in FIELDS}, "annual_income": tax.get("annual_income"),
            "document": source.get("document"), "recorded_by": source.get("recorded_by")}


def _amount(value, label: str) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise TaxInputError(f"{label} is an amount in rupees.")
    if n < 0:
        raise TaxInputError(f"{label} cannot be negative.")
    return n


async def prepare(session: AsyncSession, firm: Firm, role, *, person_id, document: str | None, salary_income=None,
                  old_regime_deductions=None, regime=None, marginal_rate=None) -> dict:
    """What recording would change, checked, and nothing written."""
    if role not in RECORDERS:
        raise TaxInputError("Salary, deductions, regime and slab rate are recorded by an adviser or a paraplanner.")
    try:
        pid = person_id if isinstance(person_id, uuid.UUID) else uuid.UUID(str(person_id))
    except ValueError:
        raise TaxInputError("Give the person's id, from read_tax_position or the client's page.")
    person = await session.get(Person, pid)
    if person is None or person.firm_id != firm.id:
        raise TaxInputError("That person is not a client of this firm.")
    household = await session.get(Household, person.household_id) if person.household_id else None
    market = markets.of_household(firm, household.values if household else None)
    if market is None or market.code != "IN":
        raise TaxInputError("The two tax regimes are India's, and this client is not taxed there.")
    source = (document or "").strip()
    if len(source) < 4:
        raise TaxInputError("Say which document this comes from: the Form 16, salary slip, return or investment proofs.")
    tax = dict((person.profile or {}).get("tax") or {})
    new: dict = {}
    if salary_income not in (None, ""):
        new["salary_income"] = _amount(salary_income, LABELS["salary_income"])
    if old_regime_deductions not in (None, ""):
        new["old_regime_deductions"] = _amount(old_regime_deductions, LABELS["old_regime_deductions"])
    if regime not in (None, ""):
        chosen = str(regime).strip().lower()
        if chosen not in REGIMES:
            raise TaxInputError("The tax regime is new or old, as the return or the declaration to the employer states it.")
        new["regime"] = chosen
    after_regime = new.get("regime") or tax.get("regime")
    if marginal_rate not in (None, ""):
        rate = _amount(marginal_rate, LABELS["marginal_rate"])
        rate = round(rate / 100, 4) if rate > 1 else round(rate, 4)
        allowed = sorted(set(slab_rates(after_regime)) if after_regime else set(slab_rates("new")) | set(slab_rates("old")))
        if rate not in allowed:
            raise TaxInputError(f"A slab rate is one of {', '.join(f'{r:.0%}' for r in allowed)}"
                                + (f" under the {after_regime} regime" if after_regime else "") + ", before surcharge and cess.")
        new["marginal_rate"] = rate
    if not new:
        raise TaxInputError("Give at least one figure to record: salary, old-regime deductions, regime or slab rate.")
    income = tax.get("annual_income")
    if income is not None:
        for key in ("salary_income", "old_regime_deductions"):
            if key in new and new[key] > float(income):
                raise TaxInputError(f"{LABELS[key]} is above the annual income on file ({markets.money(float(income), 'INR')}). "
                                    "Record the income from its document first (record_income).")
    changes = [{"field": k, "label": LABELS[k], "before": tax.get(k), "after": v} for k, v in new.items() if tax.get(k) != v]
    if not changes:
        raise TaxInputError("Nothing changes: those figures are already on file.")
    return {"person": person, "changes": changes, "document": source[:255], "annual_income": income}


def shown(field: str, value) -> str:
    if value is None:
        return "not on file"
    if field == "regime":
        return REGIMES.get(value, str(value))
    if field == "marginal_rate":
        return f"{float(value):.0%}"
    return markets.money(float(value), "INR")


async def record(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    ready = await prepare(session, firm, role, **terms)
    person = ready["person"]
    profile = dict(person.profile or {})
    tax = dict(profile.get("tax") or {})
    today = firm_time.today(firm).isoformat()
    dated = dict(tax.get("as_of") or {})
    for c in ready["changes"]:
        tax[c["field"]] = c["after"]
        dated[c["field"]] = today
    tax["as_of"] = dated
    tax["tax_inputs_source"] = {"document": ready["document"], "recorded_by": by, "recorded_at": utcnow().isoformat()}
    profile["tax"] = tax
    person.profile = profile
    flag_modified(person, "profile")
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="tax_inputs_recorded", actor=by, content={
        "person_id": str(person.id), "document": ready["document"],
        "changes": [{"field": c["field"], "before": c["before"], "after": c["after"]} for c in ready["changes"]]})
    return {"person_id": str(person.id), "person": person.full_name, "household_id": str(person.household_id),
            "changed": [c["field"] for c in ready["changes"]]}
