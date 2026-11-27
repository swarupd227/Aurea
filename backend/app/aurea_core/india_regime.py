"""The two facts the Indian regime comparison needs beside salary and deductions (B-396, L200-2 India section 3.1).

  employer NPS    what the employer puts into the person's NPS, as recorded from a statement or payslip
                  (`retirement_stack.employer_nps`), deducted under each regime. With basic salary and dearness allowance on
                  file it is held to each regime's share of them; without it the figure is counted as recorded, the limit
                  reads not checked, and the comparison is shown without it too. None on file means none is counted, which
                  does not mean none is paid. The income on file is taken to include the contribution, as a Form 16's gross
                  salary does, so deducting it takes it out. The old regime's recorded deductions must leave it out.
  switching       whether business or professional income is on file. Where it is, the limit on switching regimes is stated
                  as a fact; where the figure is not on file it reads not assessed.

Both rest on `law_figures` rows that are assumed and unreviewed. Nothing here picks a regime for a client.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import law_figures
from app.models.graph import Person
from app.models.tenant import Firm

FIGURES = ("in_80ccd2_new_cap", "in_80ccd2_old_cap", "in_employer_contribution_cap", "in_regime_business_switch_rule")
BASIS = ("Assumed: the income on file includes the employer's contribution, as a Form 16 shows it; the employer is not a government; "
         "the old regime's recorded deductions leave the contribution out.")
NEEDS_BASIC = "basic salary and dearness allowance, to check the employer's NPS contribution against its limit"


def nps_facts(*, yearly: float | None, epf_yearly: float = 0.0, basic: float | None, document: str | None = None,
              stated_on: str | None = None) -> dict:
    """The employer's contribution as each regime would deduct it."""
    new_share, old_share = law_figures.value("in_80ccd2_new_cap"), law_figures.value("in_80ccd2_old_cap")
    line = law_figures.value("in_employer_contribution_cap")
    together = round(float(yearly or 0) + float(epf_yearly or 0), 2)
    out = {"recorded_yearly": round(float(yearly), 2) if yearly else None, "basic_salary": basic, "new": 0.0, "old": 0.0,
           "new_share": new_share, "old_share": old_share, "document": document, "stated_on": stated_on,
           "employer_together": together, "perquisite_line": line, "above_line": round(max(together - line, 0.0), 2), "basis": BASIS}
    if not yearly:
        return {**out, "state": "none_on_file",
                "said": "No employer contribution to NPS is on file, so none is in the comparison. That does not mean none is paid."}
    amount = round(float(yearly), 2)
    if basic is None:
        return {**out, "state": "cap_not_checked", "new": amount, "old": amount,
                "said": "Counted as recorded. Basic salary and dearness allowance are not on file, so the limit is not checked."}
    new, old = round(min(amount, float(basic) * new_share), 2), round(min(amount, float(basic) * old_share), 2)
    return {**out, "state": "capped" if new < amount or old < amount else "within_limit", "new": new, "old": old,
            "said": "Held to each regime's share of the basic salary and dearness allowance on file."}


def switching(business_income) -> dict:
    """Whether the yearly choice of regime is limited for this person, from the business income on file."""
    rule = law_figures.get("in_regime_business_switch_rule")
    if business_income is None:
        return {"state": "not_assessed", "business_income": None, "label": "Switching limit not assessed",
                "said": "Whether this person has business or professional income is not on file.", "rule": rule.label}
    amount = round(float(business_income), 2)
    if amount > 0:
        return {"state": "limited", "business_income": amount, "label": "Switching is limited: business income on file",
                "said": rule.label + ". An assumed rule, not reviewed.", "rule": rule.label}
    return {"state": "no_business_income", "business_income": 0.0, "label": "No business income recorded: chosen year by year",
            "said": "No business or professional income, as recorded, so the limit on switching does not apply.", "rule": rule.label}


async def facts(session: AsyncSession, firm: Firm, person: Person, tax: dict) -> dict:
    """Both facts for one person, from their tax profile and the retirement records of their household."""
    from app.aurea_core import retirement_stack

    mine: list[dict] = []
    epf = 0.0
    if person.household_id:
        mine = [r for r in await retirement_stack.employer_nps(session, firm.id, person.household_id) if r["person_id"] == str(person.id)]
        epf = sum(float(r.monthly_amount) * 12 for r in await retirement_stack.current_records(session, firm.id, person.household_id)
                  if r.person_id == person.id and r.kind == "contribution" and r.scheme == "epf" and r.payer == "employer")
    one = mine[0] if mine else {}
    basic = tax.get("basic_salary")
    return {"employer_nps": nps_facts(yearly=one.get("yearly"), epf_yearly=round(epf, 2), basic=None if basic is None else float(basic),
                                      document=one.get("document"), stated_on=one.get("stated_on")),
            "switching": switching(tax.get("business_income"))}
