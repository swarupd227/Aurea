"""Guardrail spending, run beside fixed spending (B-180, L200-2 §5.1).

Fixed spending draws the same real amount every year whatever the portfolio does. A guardrail rule lets the draw follow it:
start at a withdrawal rate, raise the draw with inflation (not after a year the portfolio fell, if the terms say so), cut it
when the rate it would be passes the upper guardrail and raise it when it falls below the lower one. This runs that rule on the
same random returns as the retirement plan and sets the two side by side: the starting income, the likelihood the portfolio
lasts, and how often and how deep the cuts come. Nothing here changes spending, a plan or a mandate: it is a run, and a client
who wants to live by a rule has it recorded on their IPS by an adviser, from the document, as terms that sit beside the others.

Terms are the client's, never ours: the start rate, the upper and lower guardrails (each a share above or below the start
rate), the cut and the raise, and whether inflation is skipped after a down year. A start rate left out is the plan's own
(its first draw over the pot at retirement), and says so. A run with no terms given and none on the IPS is not assessed.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, retirement
from app.models.graph import LegalEntity, Mandate, Person
from app.models.portfolio import InvestmentPolicy
from app.models.tenant import Firm

# The least and most a term may be, as fractions. They stop a slip, not a view: the client's own terms sit inside them.
LIMITS = {"start_rate": (0.01, 0.15), "upper_band": (0.05, 0.60), "lower_band": (0.05, 0.60), "cut": (0.01, 0.50), "raise": (0.01, 0.50)}
LABELS = {"start_rate": "The start rate", "upper_band": "The upper guardrail", "lower_band": "The lower guardrail", "cut": "The cut", "raise": "The raise"}
REQUIRED = ("upper_band", "lower_band", "cut", "raise")
DECIDES = "nothing: it is a run beside fixed spending, and spending changes only when the client and their adviser decide"
NOT_MODELLED = ["tax_on_the_draw", "a_floor_on_spending", "a_ceiling_on_spending", "guaranteed_income_governed_by_the_rule", "taxes_and_fees_on_withdrawals"]


class GuardrailError(ValueError):
    """These terms cannot be used, with the reason."""


def _fraction(value: Any, key: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise GuardrailError(f"{LABELS[key]} must be a number.")
    low, high = LIMITS[key]
    if not low <= n <= high:
        raise GuardrailError(f"{LABELS[key]} must be between {low:.0%} and {high:.0%}.")
    return round(n, 6)


def clean(terms: dict | None) -> dict:
    """The terms as fractions, checked. The four of upper, lower, cut and raise are required; the start rate may be left out."""
    t = dict(terms or {})
    out: dict[str, Any] = {}
    for key in LIMITS:
        out[key] = _fraction(t.get(key), key)
    missing = [LABELS[k].lower() for k in REQUIRED if out[k] is None]
    if missing:
        raise GuardrailError(f"A guardrail rule needs {', '.join(missing)}. Give all of upper, lower, cut and raise, or none to read the IPS's terms.")
    skip = t.get("skip_inflation")
    out["skip_inflation"] = (str(skip).strip().lower() == "true") if skip not in (None, "") else False
    return out


def _owners(people: list, entities: list) -> list:
    return [x.id for x in (*people, *entities)]


async def terms_on_file(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    """The guardrail terms on the IPS in force for the household's mandates, the latest accepted if several, with where."""
    from app.aurea_core import ips

    people = (await session.execute(select(Person).where(Person.household_id == household_id))).scalars().all()
    entities = (await session.execute(select(LegalEntity).where(LegalEntity.household_id == household_id))).scalars().all()
    owners = _owners(people, entities)
    if not owners:
        return None
    mandates = (await session.execute(select(Mandate).where(Mandate.firm_id == firm_id, Mandate.is_active.is_(True))
                                      .where((Mandate.person_id.in_(owners)) | (Mandate.entity_id.in_(owners))))).scalars().all()
    found = []
    for m in mandates:
        policy = await ips.in_force(session, m.id)
        if policy is not None and policy.guardrail_spending:
            found.append((policy.accepted_on, m, policy))
    if not found:
        return None
    _, mandate, policy = max(found, key=lambda x: x[0])
    return {"terms": dict(policy.guardrail_spending), "mandate": mandate.name, "mandate_id": str(mandate.id), "ips_version": policy.version,
            "accepted_on": policy.accepted_on.isoformat(), "document": policy.document, "mandates_with_terms": len(found)}


def view_terms(t: dict) -> dict:
    return {"start_rate": t.get("start_rate"), "upper_band": t["upper_band"], "lower_band": t["lower_band"], "cut": t["cut"], "raise": t["raise"],
            "skip_inflation": bool(t.get("skip_inflation"))}


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, terms: dict | None = None) -> dict | None:
    """The guardrail run for one household. `terms` are what was said; with none, the IPS's. None if the household is not in this firm."""
    from app.aurea_core.graph import household_brain

    brain = await household_brain(session, household_id, firm_id=firm_id)
    if brain is None:
        return None
    firm = await session.get(Firm, firm_id)
    head = {"household_id": str(household_id), "household": brain["household"]["name"], "as_of": firm_time.today(firm).isoformat(),
            "decides": DECIDES, "not_modelled": NOT_MODELLED, "estimate": True}
    if terms:
        used, source, where = clean(terms), "stated", None
    else:
        on_file = await terms_on_file(session, household_id, firm_id=firm_id)
        if on_file is None:
            return {**head, "status": "not_assessed", "terms_from": None,
                    "reason": "No guardrail terms are on the household's IPS and none were given. Say the terms: the start rate, the upper and lower guardrails, the cut and the raise."}
        used, source, where = clean(on_file["terms"]), "ips", on_file
    plan = await retirement.for_household(session, household_id, firm_id=firm_id, overrides={"guardrails": used})
    if plan is None or not plan.get("guardrails"):
        return {**head, "status": "not_assessed", "terms_from": source, "reason": "No retirement plan can be run for this household, so there is no spending to compare."}
    block = plan["guardrails"]
    return {**head, "status": "assessed", "terms_from": source, "ips": where, **block,
            "retirement_age": plan["retirement_age"], "plan_to": plan["end_age"], "plan_income_basis": plan.get("income_basis"),
            "guaranteed_at_retirement": plan.get("guaranteed_income"), "assumptions_source": plan.get("assumptions_source"),
            "assumptions_version": plan.get("assumptions_version"), "assumed": plan.get("assumed", [])}
