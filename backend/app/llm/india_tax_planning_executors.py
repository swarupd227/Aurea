"""The conversation's tools for India's tax planning (B-396): the after-tax comparison and the stated returns behind it
(`aurea_core/india_after_tax.py`), and the regime and harvest reviews with the year-end gain-harvest tasks
(`aurea_core/india_tax_year.py`).

Mixed into ``ToolExecutors`` (app/llm/executors.py), which registers each tool in its executor map and each Confirm card in
``preview_change``. Every card is the module's ``prepare_*``: every check the write makes, nothing written. ``ExecutorError``
is imported where it is raised, because executors.py imports this module.
"""
from __future__ import annotations

from typing import Any

INDIA_TAX_PLANNING_TOOLS = ("read_after_tax_comparison", "record_stated_return", "read_tax_reviews", "record_tax_review",
                            "raise_harvest_tasks")
RETURN_TERMS = ("vehicle", "name", "rate_percent", "stated_on", "document")
REVIEW_TERMS = ("person_id", "entity_id", "kind", "covered", "document", "reviewed_on", "tax_year")


def _terms(inputs: dict, keys) -> dict:
    """Every named term, the missing ones as None, so the module says what is missing in its own words."""
    return {k: (None if inputs.get(k) == "" else inputs.get(k)) for k in keys}


def _error(exc: Exception):
    from app.llm.executors import ExecutorError

    return ExecutorError(str(exc))


class IndiaTaxPlanningExecutors:
    """Needs the host's session, firm_id, user_id, role, _firm() and _shelf_user()."""

    def _india_tax_planning_map(self) -> dict:
        return {k: getattr(self, k) for k in INDIA_TAX_PLANNING_TOOLS}

    # -- Reads -------------------------------------------------------------------------------------------

    async def read_after_tax_comparison(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_after_tax
        from app.llm.executors import _parse_uuid

        hid = inputs.get("household_id")
        try:
            result = await india_after_tax.comparison(self.session, await self._firm(),
                                                      household_id=_parse_uuid(hid, "household_id") if hid else None,
                                                      amount=inputs.get("amount"), months=inputs.get("months"))
        except india_after_tax.AfterTaxError as exc:
            raise _error(exc)
        if not result["applies"]:
            result["message"] = "This comparison is read for an Indian firm."
            return result
        missing = [r["label"].lower() for r in result["rates"] if r["state"] != "on_file"]
        assessed = sum(1 for t in result["taxpayers"] if t["state"] == "assessed")
        result["message"] = ((f"{assessed} of {len(result['taxpayers'])} taxpayer(s) assessed. " if result["household_id"] else
                              "The stated returns only; name a household for what each leaves after tax. ")
                             + (f"No stated return on file for: {', '.join(missing)}. " if missing else "")
                             + ("The amount is assumed for illustration. " if result["amount_assumed"] else "")
                             + ("The period is assumed. " if result["months_assumed"] else "")
                             + "Returns are as stated in the documents, not promised; the tax rules are assumed and unreviewed. "
                             + "Lay the figures out and recommend nothing.")
        return result

    async def read_tax_reviews(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_tax_year
        from app.llm.executors import _parse_uuid

        hid = inputs.get("household_id")
        try:
            result = await india_tax_year.overview(self.session, await self._firm(),
                                                   household_id=_parse_uuid(hid, "household_id") if hid else None)
        except india_tax_year.TaxYearError as exc:
            raise _error(exc)
        if not result["applies"]:
            result["message"] = "Regime and harvest reviews are read for an Indian firm."
            return result
        c, q = result["completion"], result["harvest"]["counts"]
        result["message"] = (f"Tax year {result['tax_year']}: regime reviews on record for {c['regime']['done']} of {c['regime']['of']} taxpayer(s), "
                             f"harvest reviews for {c['harvest']['done']} of {c['harvest']['of']}. Year-end gain harvest: {q['to_raise']} to raise, "
                             f"{q['raised']} with a task, {q['reviewed']} already reviewed. {result['note']}")
        return result

    # -- Writes ------------------------------------------------------------------------------------------

    async def record_stated_return(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_after_tax

        firm = await self._firm()
        try:
            row = await india_after_tax.record_return(self.session, firm, self.role, by=await self._shelf_user(), **_terms(inputs, RETURN_TERMS))
            result = await india_after_tax.comparison(self.session, firm)
        except india_after_tax.AfterTaxError as exc:
            raise _error(exc)
        result["recorded"] = row
        result["message"] = (f"Recorded {row['rate']:g}% a year before tax for the {row['label'].lower()}, as the document states it. "
                             "It is not a forecast, and it changes no investment.")
        return result

    async def record_tax_review(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_tax_year

        firm = await self._firm()
        try:
            row = await india_tax_year.record_review(self.session, firm, self.role, by=await self._shelf_user(), **_terms(inputs, REVIEW_TERMS))
            result = await india_tax_year.overview(self.session, firm, household_id=_uuid(row["household_id"]))
        except india_tax_year.TaxYearError as exc:
            raise _error(exc)
        result["recorded"] = row
        result["message"] = f"Recorded the {row['label'].lower()} for {row['name']}, tax year {row['tax_year']}. It changes no figure and trades nothing."
        return result

    async def raise_harvest_tasks(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from app.aurea_core import india_tax_year

        firm = await self._firm()
        hid = inputs.get("household_id") or None
        try:
            done = await india_tax_year.raise_tasks(self.session, firm, self.role, by=await self._shelf_user(), by_id=self.user_id,
                                                    household_id=hid)
            result = await india_tax_year.overview(self.session, firm, household_id=_uuid(hid) if hid else None)
        except india_tax_year.TaxYearError as exc:
            raise _error(exc)
        unassigned = sum(1 for r in done["raised"] if not r["adviser_id"])
        result["raised"] = done["raised"]
        result["message"] = (f"Raised {len(done['raised'])} task(s) for tax year {done['tax_year']}, due {done['due_date']}"
                             + (f"; {unassigned} unassigned, with no adviser recorded" if unassigned else "")
                             + ". Each is a reminder to look: nothing is sold and no client is told.")
        return result

    # -- Confirm cards -----------------------------------------------------------------------------------

    async def _india_tax_planning_preview(self, tool_key: str, inputs: dict[str, Any]) -> list[tuple[str, Any]]:
        from app.aurea_core import firm_time, india_after_tax, india_tax_year, markets

        firm = await self._firm()
        say = markets.money_for(firm)
        try:
            if tool_key == "record_stated_return":
                p = await india_after_tax.prepare_return(self.session, firm, self.role, **_terms(inputs, RETURN_TERMS))
                before = p["replaces"]
                return [("Vehicle", india_after_tax.VEHICLES[p["vehicle"]]), ("Named", p["name"]),
                        ("Return a year, before tax", f"{p['rate']:g}%"), ("Stated on", firm_time.spoken(p["stated_on"])),
                        ("Replaces", f"{float(before.rate):g}% of {firm_time.spoken(before.stated_on)}" if before else "Nothing on file"),
                        ("Document", p["document"]),
                        ("Effect", "The after-tax comparison uses it for every household. It is not a forecast and moves nothing")]
            if tool_key == "record_tax_review":
                p = await india_tax_year.prepare_review(self.session, firm, self.role, **_terms(inputs, REVIEW_TERMS))
                f = p["figures"]
                if p["kind"] == "regime" and f.get("assessed"):
                    shown = f"New {say(f['new_tax'])}, old {say(f['old_tax'])}" + ("" if f.get("conclusive") else " (not conclusive)")
                elif p["kind"] == "harvest" and f:
                    shown = f"Exemption left {say(f['exemption_left'])}, gain that would use it {say(f['gain_to_use_it'])}"
                else:
                    shown = "Not worked out for a past year" if not f else "The comparison is not assessed"
                return [("Taxpayer", p["name"]), ("Household", p["household"].name), ("Review", india_tax_year.KINDS[p["kind"]]),
                        ("Tax year", p["tax_year"]), ("Held on", firm_time.spoken(p["reviewed_on"])), ("What was covered", p["covered"]),
                        ("Figures kept beside it", shown), ("Evidence", p["document"]),
                        ("Note", "Records that a person held it. It files nothing and trades nothing")]
            if tool_key == "raise_harvest_tasks":
                p = await india_tax_year.prepare_raise(self.session, firm, self.role, household_id=inputs.get("household_id") or None)
                return [("Tax year", p["tax_year"]), ("Tasks", str(len(p["ready"]))), ("Due", firm_time.spoken(p["due_date"])),
                        *[(f"{q['name']} ({q['household']})",
                           f"{say(q['gain'])} of gain would use {say(q['exemption_left'])} left; to {q['adviser'] or 'nobody: no adviser recorded'}")
                          for q in p["ready"]],
                        ("Note", "One task per taxpayer and tax year. A reminder to look: no sale or rebuy is advised and no client is told")]
        except (india_after_tax.AfterTaxError, india_tax_year.TaxYearError) as exc:
            raise _error(exc)
        return []


def _uuid(value):
    import uuid

    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
